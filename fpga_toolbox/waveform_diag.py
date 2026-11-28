"""Agent 友好的 VCD/FST 波形诊断器（零外部依赖）。

背景：仿真失败时，Agent 通常只有一段日志和一份波形文件。日志会告诉它"第 3
笔事务 FAIL"，但不会告诉它**为什么**。传统做法是人打开 GTKWave，用鼠标拖时间轴、
点信号、看哪一拍变 X —— 这正是文本 Agent 做不到的事。

本模块把波形压缩成**面向文本的结论**：

  * **X/Z 传播点** —— 第一个未知值出现在哪个信号、哪个时刻。X 往往是根因：
    未复位的寄存器、未连接的端口、被 x 污染的组合逻辑。
  * **握手死锁窗口** —— 对 AXI4/APB 逐通道统计 VALID/READY 的高电平区间，
    找出「VALID 恒高但 READY 恒低」的时间窗（死锁）。
  * **信号翻转统计** —— 谁从未变化（可能是常量/未驱动）。

设计取舍（刻意不做的事）：

  * **不解析 FST**。FST 是压缩二进制格式，需要 zlib + 自定义位打包解码，
    且语义必须转回 VCD 才有意义。本工具只支持 VCD（文本格式）—— 这已覆盖
    默认输出（`run_sim` 的 `waveform_format` 默认 `vcd`）。遇到 FST 时给出
    可操作的错误，而不是抛出解码异常。
  * **不做图形/时序图渲染**。目标是"结论"，不是"图"。
  * **不加载整个波形到内存**。VCD 可能几百 MB，逐行流式解析。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .core.types import err_result, ok_result

#: VCD 头部指令
_VAR_RE = re.compile(r"\$var\s+(\S+)\s+(\d+)\s+(\S+)\s+([^\s]+)(?:\s+(\S+))?\s*\$end")
_SCOPE_RE = re.compile(r"\$scope\s+(\S+)\s+(\S+)\s*\$end")
_UPSCOPE_RE = re.compile(r"\$upscope")
_ENDDEF_RE = re.compile(r"\$enddefinitions\s*\$end")
_VALUE_CHANGE = re.compile(r"^([01xzXZ])([^\s]+)$")
_VECTOR_CHANGE = re.compile(r"^([brh])([0-9a-fA-FxzXZ_]+)\s+(\S+)$")
_TIME_RE = re.compile(r"^#(\d+)")


def _is_unknown(val: str) -> bool:
    """值里是否含 x/z（单个 bit 或向量）。"""
    return "x" in val.lower() or "z" in val.lower()



_READY_OF = {
    "AWVALID": "AWREADY",
    "WVALID": "WREADY",
    "ARVALID": "ARREADY",
    "BVALID": "BREADY",
    "RVALID": "RREADY",
    # AXI4-Stream（唯一的 VALID/READY 在同一前缀下的协议）
    "TVALID": "TREADY",
}


def _ready_of(sig: str) -> Optional[str]:
    """按信号名查它对应的 READY 信号。

    除了裸名（bvalid -> bready）外，还要剥掉常见的**总线通道前缀**：
    ``s_axi_bvalid`` / ``m_axi_bvalid`` 是 AXI4 的 master/slave 前缀，是单个
    标识符（下划线分隔，不是 ``axi.bvalid`` 那样的对象访问），所以
    ``split(".")`` 归一后仍是 ``S_AXI_BVALID``，直接查表查不到。桥接 TB 实测
    踩中：``while (!(s_axi_bvalid && m_axi_bready))`` 被误判 R3。
    """
    key = sig.split(".")[-1].upper()
    if key in _READY_OF:
        return _READY_OF[key]
    # 迭代剥前缀：真实 RTL 里 VALID 信号常带多段前缀（s_axi_awvalid、
    # axi_bvalid、u_bvalid、m_axis_tvalid ...）。旧实现只剥 s_/m_ + axis 一段
    # 前缀，`axi_awvalid` 这类裸 axi_ 前缀或两段前缀查不到 → 返回 None →
    # 明明同时查了 READY 仍漏报死锁（实测 axi_awvalid@10 拉高、axi_awready
    # 从未拉高却检不出）。逐段剥前缀直到命中表项。
    parts = key.split("_")
    for i in range(1, len(parts)):
        candidate = "_".join(parts[i:])
        if candidate in _READY_OF:
            return _READY_OF[candidate]
    return None


def _detect_format(path: Path) -> Optional[str]:
    """按扩展名与魔数判断波形格式。返回 'vcd' / 'fst' / None。"""
    suffix = path.suffix.lower()
    if suffix == ".vcd":
        return "vcd"
    if suffix in (".fst",):
        return "fst"
    if suffix in (".lxt2", ".wlf", ".shm", ".vcd.gz"):
        return suffix.lstrip(".")
    return None


def _scan(
    path: Path,
    max_time: Optional[int],
    track_signals: Optional[List[str]],
) -> Dict[str, Any]:
    """流式解析 VCD，收集首 X/Z 传播点、翻转计数、握手窗口。

    `max_time` 限制分析窗口（避免大波形全扫）。
    `track_signals` 只关注这些信号（其余仍参与 X 检测但不建明细）。
    """
    signals: Dict[str, Dict[str, Any]] = {}   # id_code -> var info
    id_to_name: Dict[str, str] = {}
    scope_stack: List[str] = []
    in_defs = True
    cur_time = 0

    first_x: List[Dict[str, Any]] = []        # 每个信号的首 X/Z
    x_seen: set = set()
    toggles: Dict[str, int] = {}
    prev_value: Dict[str, str] = {}
    transitions: Dict[str, List[Tuple[int, bool]]] = {}
    max_time_seen = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if in_defs:
                mv = _VAR_RE.match(line)
                if mv:
                    var_type, width_s, idc, name, _idx = mv.groups()
                    width = int(width_s)
                    full = (scope_stack[-1] + "." + name) if scope_stack else name
                    signals[idc] = {
                        "name": name, "full": full, "width": width,
                        "type": var_type,
                    }
                    id_to_name[idc] = full
                    continue
                ms = _SCOPE_RE.match(line)
                if ms:
                    scope_stack.append(ms.group(2))
                    continue
                if _UPSCOPE_RE.match(line):
                    if scope_stack:
                        scope_stack.pop()
                    continue
                if _ENDDEF_RE.match(line):
                    in_defs = False
                continue

            # ---- value change 段 ----
            mt = _TIME_RE.match(line)
            if mt:
                cur_time = int(mt.group(1))
                max_time_seen = max(max_time_seen, cur_time)
                if max_time is not None and cur_time > max_time:
                    break
                continue

            # ⚠ 匹配顺序很重要：必须**先**试向量格式 `b<num> <id>`。
            # 因为单比特正则 `^([01xzXZ])(\S+)$` 会把 `bx #` 误吞成
            # "值='b', id='#'"（b 是合法单比特值、# 是 id），于是那条 8 位
            # 向量的 x 根本不会被识别 —— 实测 X 检测返回 0 条，假阴性。
            mvec = _VECTOR_CHANGE.match(line)
            if mvec:
                _b, val, idc = mvec.groups()
            else:
                mvc = _VALUE_CHANGE.match(line)
                if not mvc:
                    continue
                val, idc = mvc.group(1), mvc.group(2)

            name = id_to_name.get(idc)
            if name is None:
                continue
            # 时间过滤（信号变化可能滞后于当前 #time）
            if max_time is not None and cur_time > max_time:
                continue

            prev = prev_value.get(idc)
            if prev != val:
                toggles[idc] = toggles.get(idc, 0) + 1
                prev_value[idc] = val

            if _is_unknown(val) and idc not in x_seen:
                x_seen.add(idc)
                first_x.append({
                    "signal": name,
                    "time": cur_time,
                    "value": val,
                    "width": signals[idc]["width"],
                })

            # 收集握手信号变迁，供死锁分析用。两类协议：
            #   * AXI / AXI4-Stream：*VALID / *READY 两信号握手
            #   * APB：*PSEL / *PENABLE / *PREADY 三信号握手（无 VALID/READY 对，
            #     旧过滤器 `endswith(("VALID","READY"))` 会把 APB 全排除 → APB
            #     死锁检测完全不工作）
            base = name.split(".")[-1].upper()
            if base.endswith(("VALID", "READY", "PSEL", "PENABLE", "PREADY")):
                transitions.setdefault(idc, []).append((cur_time, val == "1"))

    return {
        "signals": {k: v for k, v in signals.items()},
        "id_to_name": id_to_name,
        "first_x": first_x,
        "toggles": toggles,
        "max_time_seen": max_time_seen,
        "transitions": transitions,
    }



def _find_handshake_deadlocks(scan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """找出「VALID 拉高后 READY 一直为低」的死锁窗口。

    Agent 最需要但最难从日志看出：日志只会说「第 3 笔事务超时」，而波形能直接
    指出「TVALID 从 t=100 一直拉高到仿真结束，TREADY 从未拉高」—— 这就是死锁，
    且指向 DUT 的流控实现。
    """
    id_to_name = scan["id_to_name"]
    transitions = scan["transitions"]
    max_t = scan["max_time_seen"]

    # 把信号按名字分组（同一逻辑信号可能只有一个 id，但保险起见按 full name 归并）
    by_name: Dict[str, List[Tuple[int, bool]]] = {}
    for idc, changes in transitions.items():
        name = id_to_name.get(idc)
        if name:
            by_name[name] = changes

    deadlocks: List[Dict[str, Any]] = []
    for name, changes in by_name.items():
        base = name.split(".")[-1]
        ready_name = _ready_of(base)
        if not ready_name:
            continue
        # 找同 scope 的 READY 信号。不能猜 `prefix.ready_name` —— 真实 RTL 里
        # READY 名常带自己的通道前缀（VALID=axi_awvalid 时 READY=axi_awready，
        # 不是 tb.ready）。改为：在**同 scope**（同 `.` 前缀）内找**以
        # ready_name 结尾**的信号（`axi_awready` 以 `awready` 结尾）。
        prefix = name[: name.rfind(".")] if "." in name else ""
        rchanges = None
        ready_full = None
        chan = base[: -len(ready_name)]  # VALID 名去掉 ready_name 的通道前缀
        # 同 scope + READY 名以 ready_name 结尾 + **通道前缀一致**。
        # 只用后缀匹配会跨通道误配（wvalid 的后缀是 valid，会匹配到
        # awready —— 不同通道）。通道前缀一致才配对：awvalid/awready 的
        # 通道前缀都是 "aw"，wvalid/wready 都是 "w"。
        chan = base[: -len(ready_name)]
        for cand, ch in by_name.items():
            cand_prefix = cand[: cand.rfind(".")] if "." in cand else ""
            cand_base = cand.split(".")[-1].lower()
            if cand_prefix != prefix or not cand_base.endswith(ready_name.lower()):
                continue
            if cand_base[: -len(ready_name)] == chan:   # 同通道
                rchanges = ch
                ready_full = cand
                break
        if not rchanges:
            continue

        # 遍历 VALID 拉高的每一段，看 READY 是否在段内拉高过
        high_start = None
        for ct, is_high in changes:
            if is_high and high_start is None:
                high_start = ct
            elif not is_high and high_start is not None:
                high_start = None
        if high_start is None:
            continue
        r_ever = any(is_r and ct_r >= high_start for ct_r, is_r in rchanges)
        if not r_ever:
            deadlocks.append({
                "valid_signal": name,
                "valid_high_since": high_start,
                "ready_signal": ready_full,
                "window_end": max_t,
                "note": "VALID 拉高后 READY 从未拉高 —— 握手死锁，通常指向 DUT 流控实现",
            })
    return deadlocks


def _find_apb_deadlocks(scan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """找出 APB 握手死锁：PSEL 拉高后 PREADY 从未拉高。

    APB 是**三信号协议**，与 AXI 的 VALID/READY 两信号握手不同：
      * PSEL + PENABLE 同时高 = access 相位（一次传输请求）
      * PREADY 高 = 从机应答完成

    死锁模式：PSEL 拉高后 PREADY 一直为低 —— 主设备在等从机应答，从机永不
    应答，仿真在此挂死。日志只会说「APB 事务超时」，波形能直接指出
    「PSEL 从 t=X 拉高，PREADY 到仿真结束从未拉高」。

    与 AXI 检测分开实现的原因：APB 没有 VALID/READY 对（`_ready_of` 返回
    None），走不到 AXI 的检测分支。APB 需要按「PSEL 前缀找同组 PREADY」来配对。
    """
    id_to_name = scan["id_to_name"]
    transitions = scan["transitions"]
    max_t = scan["max_time_seen"]

    by_name: Dict[str, List[Tuple[int, bool]]] = {}
    for idc, changes in transitions.items():
        name = id_to_name.get(idc)
        if name:
            by_name[name] = changes

    deadlocks: List[Dict[str, Any]] = []
    # 找每个 PSEL 信号，按同前缀配对 PREADY
    for name, changes in by_name.items():
        base = name.split(".")[-1].lower()
        if not base.endswith("psel"):
            continue
        prefix = name[: name.rfind(".")] if "." in name else ""
        ready_full = f"{prefix}.pready" if prefix else "pready"
        rchanges = by_name.get(ready_full)
        if rchanges is None:
            # 裸名兜底
            for cand, ch in by_name.items():
                if cand.split(".")[-1].lower() == "pready":
                    rchanges = ch
                    ready_full = cand
                    break
        if not rchanges:
            continue

        # PSEL 拉高的最后一段起点（简化：只看是否曾拉高且结束时仍高）
        high_start = None
        for ct, is_high in changes:
            if is_high and high_start is None:
                high_start = ct
            elif not is_high:
                high_start = None
        if high_start is None:
            continue
        r_ever = any(is_r and ct_r >= high_start for ct_r, is_r in rchanges)
        if not r_ever:
            deadlocks.append({
                "valid_signal": name,
                "valid_high_since": high_start,
                "ready_signal": ready_full,
                "window_end": max_t,
                "note": "APB: PSEL 拉高后 PREADY 从未拉高 —— 握手死锁，从机永不应答",
            })
    return deadlocks


def diagnose_waveform(
    waveform_path: str = "",
    signals: Optional[List[str]] = None,
    max_time: Optional[int] = None,
) -> Dict[str, Any]:
    """分析 VCD 波形，输出面向文本 Agent 的结论。

    ``signals``  只关注这些信号（其余仍参与 X 检测但不建明细）。
    ``max_time`` 限制分析窗口（时间单位与 VCD 内的 timescale 一致）。
    """
    if not waveform_path or not str(waveform_path).strip():
        return err_result("diagnose_waveform", "waveform_path is required.")
    p = Path(waveform_path)
    if not p.is_file():
        return err_result("diagnose_waveform", f"Waveform file not found: {waveform_path}")

    fmt = _detect_format(p)
    if fmt != "vcd":
        # FST/LXT2/WLF 是二进制压缩格式；本工具不做二进制解码，而是给出
        # 可操作的指引（改用 VCD 或用工具自带转换），而不是抛解码异常。
        return err_result(
            "diagnose_waveform",
            f"Unsupported waveform format '{fmt}' (only VCD is supported). "
            f"Re-run the simulation with waveform_format='vcd', or convert with "
            f"`vcd2fst`/`fst2vcd` from the Verilator distribution.",
            data={"format": fmt, "path": str(p.resolve())},
        )

    scan = _scan(p, max_time, signals)
    n_signals = len(scan["id_to_name"])

    # ---- 结论 1：X/Z 传播点（按时间升序）----
    first_x = sorted(scan["first_x"], key=lambda x: (x["time"], x["signal"]))
    # 只保留前 N 个（Agent 不需要全部；通常首几个就是根因）
    top_x = first_x[:20]

    # ---- 结论 2：从未翻转的信号（疑似常量/未驱动）----
    stuck = []
    for idc, cnt in scan["toggles"].items():
        name = scan["id_to_name"].get(idc)
        if cnt == 0 and (signals is None or name in (signals or [])):
            stuck.append({"signal": name, "value": scan["prev_value"].get(idc)})

    # ---- 结论 3：握手死锁（VALID 拉高后 READY 从未拉高）----
    deadlocks = _find_handshake_deadlocks(scan) + _find_apb_deadlocks(scan)

    summary = {
        "waveform": str(p.resolve()),
        "format": "vcd",
        "signal_count": n_signals,
        "time_range": {"start": 0, "end": scan["max_time_seen"]},
        "x_propagation": {
            "count": len(first_x),
            "first": top_x,
            "interpretation": (
                "第一个未知值出现在上述信号/时刻。X 通常源于未复位的寄存器、"
                "未连接的端口或被 x 污染的组合逻辑 —— 从最早出现的那一条往"
                "驱动源追溯，通常就是根因。"
                if first_x else "未检测到未知值（X/Z）。"),
        },
        "handshake_deadlocks": {
            "count": len(deadlocks),
            "windows": deadlocks[:10],
            "interpretation": (
                "存在 VALID 拉高后 READY 从未拉高的窗口 —— 这是握手死锁，"
                "通常指向 DUT 的流控实现（如 ready 只在特定状态下拉高，但状态机"
                "卡在了别的状态）。从 valid_high_since 那一拍往后查 DUT 状态机。"
                if deadlocks else "未检测到握手死锁。"),
        },
        "stuck_signals": {
            "count": len(stuck),
            "signals": stuck[:20],
            "interpretation": (
                "这些信号在整个窗口内从未翻转，可能是常量、未驱动或被 optimize 掉了。"
                if stuck else "无恒定信号（所有被观测信号都至少翻转一次）。"),
        },
    }
    return ok_result("diagnose_waveform", summary)


def tool_diagnose_waveform(
    waveform_path: str = "",
    signals: Optional[List[str]] = None,
    max_time: Optional[int] = None,
) -> Dict[str, Any]:
    """Tool wrapper: diagnose a VCD waveform for X propagation and stuck signals."""
    return diagnose_waveform(waveform_path, signals=signals, max_time=max_time)
