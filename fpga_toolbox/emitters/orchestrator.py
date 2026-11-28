"""多协议 TB 编排：生成前校验 → 选模板 → 后处理 → 落盘 + 运行脚本。

原 ``protocol_tb_emitter.py`` 的编排层（``emit_protocol_testbench``）与脚本生成器。
协议模板本体已拆到同包的 ``apb.py`` / ``axi4.py`` / ... —— 本模块只负责：

  1. 模块与接口选择（多模块文件、多接口 DUT）
  2. 生成前三道闸门（端口存在性、方向、可选信号）
  3. 派发到对应协议模板
  4. 后处理（复位极性、波形注入、`.*` 展开）
  5. 落盘 SV TB + Verilator 运行脚本

波形注入 / lint_off / 运行脚本生成都在这里，因为它们对所有协议一致。
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.types import err_result, ok_result
from ..iface_extractor import tool_extract_interfaces
from .ahb_lite import _emit_ahb_tb
from .apb import _emit_apb_tb
from .axi4 import _emit_axi4_tb
from .axi4_stream import _emit_axi4_stream_tb
from .common import (
    SUPPORTED_MULTI_PROTOCOLS,
    _missing_tb_ports,
    _wrong_dir_ports,
)
from .ddr import _emit_ddr3_tb, _emit_ddr4_tb, _emit_ddr5_tb
from .ethernet import _emit_ethernet_tb
from .hbm import _emit_hbm_tb
from .pcie import _PCIE_GENS, _PCIE_LANES, _emit_pcie_tb
from .spi import _emit_spi_tb


def add_lint_off(tb_src: str) -> str:
    """在生成的 TB 里关掉 PINMISSING。

    DUT 常带有 TB 不关心的额外端口（调试口、test_mode、scan_en 等）。模板用
    `.*` 通配连接，只覆盖 TB 里声明了同名变量的端口，其余会触发 Verilator 的
    PINMISSING 警告——而 `emit` 的 run 脚本默认把 warning 当错误，构建直接
    "Exiting due to 1 warning(s)" 失败，工具却已返回 ok=True。
    """
    out = []
    in_header = False
    for line in tb_src.splitlines(keepends=True):
        out.append(line)
        if line.startswith("module tb_"):
            # module 头可能是单行 `module tb_x;`，也可能是多行参数化形式
            # （module 名后跟 #( ... );）——后者要等到 `);` 才结束。
            if line.rstrip().endswith(";"):
                in_header = False
                out.append(
                    "  // DUT 可能有 TB 不关心的额外端口（调试/测试/scan），\n"
                    "  // 显式连接覆盖不到会触发 PINMISSING，故显式关闭。\n"
                    "  /* verilator lint_off PINMISSING */\n"
                )
            else:
                in_header = True
        elif in_header and line.rstrip().endswith(";"):
            in_header = False
            out.append(
                "  // DUT 可能有 TB 不关心的额外端口（调试/测试/scan），\n"
                "  // 显式连接覆盖不到会触发 PINMISSING，故显式关闭。\n"
                "  /* verilator lint_off PINMISSING */\n"
            )
    return "".join(out)


def add_waveform_dump(tb_src: str, top_module: str) -> str:
    """给生成的 TB 在 initial 块最前面注入 $dumpfile / $dumpvars。

    为什么要用环境变量控制：波形是可选的（会让仿真慢、文件大），不该默认开启。
    但 TB 一旦编译成二进制，dump 目标就在运行期固定了——所以这里用
    `$value$plusargs` 从仿真命令行读 +WAVE / +WAVE_FILE，无需重新生成 TB。

    关键事实（实测）：Verilator 在**没有** `--trace` 时只打一条
    `-Info: $dumpvar ignored, as Verilated without --trace`，仿真照常
    PASS、exit 0，波形文件根本不存在。$dumpvars 写在 TB 里只是必要条件，
    run 脚本必须同时加 --trace（由 WAVE=1 控制），二者缺一不可。

    注入位置：第一个 `initial begin` 之后的第一行。此时 timescale/clk 已定义，
    且在任何激励之前开始记录，不会漏掉开头几个周期的信号。
    """
    snippet = (
        "    // ---- waveform dump (optional; enable with +WAVE on the sim command line) ----\n"
        '    if ($test$plusargs("WAVE")) begin\n'
        '      if ($value$plusargs("WAVE_FILE=%s", wave_path)) begin\n'
        '        $dumpfile(wave_path);\n'
        "      end else begin\n"
        '        $dumpfile("wave.vcd");\n'
        "      end\n"
        f"      $dumpvars(0, {top_module});\n"
        "    end\n"
    )
    # 在第一个 initial begin 之后插入
    marker = "  initial begin\n"
    idx = tb_src.find(marker)
    if idx < 0:
        return tb_src  # 模板形态变了：不静默破坏，保持原样
    insert_at = idx + len(marker)
    out = tb_src[:insert_at] + snippet + tb_src[insert_at:]

    # $value$plusargs 的目标必须是一个已声明的变量（Verilator 会报
    # "Can't find definition of variable: 'wave_path'" 直接拒绝编译——
    # 这是实际跑一遍才发现的）。声明放在 module 顶部、endmodule 之前。
    decl = '  string wave_path;\n'
    if decl not in out:
        close = out.rfind("endmodule")
        if close > 0:
            out = out[:close] + decl + out[close:]
    return out


def _emit_run_script(
    proto: str,
    mod: str,
    rtl_abs: Path,
    tb_abs: Path,
    *,
    pcie_suites: Optional[List[str]] = None,
    pcie_matrix: bool = False,
) -> str:
    """生成 suite 派发脚本。

    suite 列表必须与 TB 模板**实际支持的配置**一致：
    * TB 走 3x3 矩阵（DUT 声明了 GEN/LANES）时才是 9 个 suite，且必须把
      ``-GGEN`` / ``-GLANES`` 传给 verilator 才能覆盖参数；
    * TB 是固定配置时只有一个 suite，**不能**再传 ``-GGEN/-GLANES``——
      对没有该参数的设计跑 ``verilator -GGEN=3`` 会得到
      ``Parameters from the command line were not found in the design`` 并以
      非 0 退出，9 个「suite」全部是编译失败（旧 `sim.sh` 就踩过这个）。
    """
    # top 必须等于 TB 文件里实际声明的 module 名。直接取 tb_abs.stem ——
    # 调用方保证文件名 == module 名（协议模板与桥接模板都遵守），这样桥接
    # TB（module 名是 tb_<mod>_bridge 而非 tb_<mod>_<proto>）也能正确驱动。
    # 旧实现用 f"tb_{mod}_{proto}" 硬算，桥接下会生成 --top-module tb_x_axi4
    # 而实际 module 是 tb_x_bridge，Verilator 报 "not found in design"。
    top = tb_abs.stem
    if proto == "pcie":
        names = list(pcie_suites or [])
        if not names:
            names = [f"pcie_g{g}x{n}" for g in _PCIE_GENS for n in _PCIE_LANES]
        cases: List[str] = []
        for sname in names:
            flags = ""
            if pcie_matrix:
                # suite 名形如 pcie_g4x16，直接解析出需要覆盖的参数值。
                mm = re.match(r"pcie_g(\d+)x(\d+)$", sname)
                if mm:
                    flags = f" -GGEN={mm.group(1)} -GLANES={mm.group(2)}"
            cases.append(f'  {sname}) run_one {sname} {top}{flags} "{rtl_abs}" "{tb_abs}" ;;')
        all_s = " ".join(names)
    else:
        all_s = proto
        cases = [f'  {proto}) run_one {proto} {top} "{rtl_abs}" "{tb_abs}" ;;']
    cases_s = "\n".join(cases)

    return f"""#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
VLT="${{VERILATOR:-verilator}}"

# 波形开关：WAVE=1 时给 verilator 加 --trace / --trace-fst，并让 TB 的
# $dumpfile 落到 WAVE_DIR（默认脚本目录）。
# 注意：Verilator 在没有 --trace 时**静默忽略** $dumpvars（只打一条 -Info），
# 仿真仍 PASS、exit 0，但波形文件不存在。所以 --trace 必须与 TB 里的
# $dumpfile/$dumpvars 配对，run_sim 再用 require_waveform 对产物做断言。
WAVE="${{WAVE:-0}}"
WAVE_FORMAT="${{WAVE_FORMAT:-vcd}}"
TRACE_FLAG=""
if [ "$WAVE" = "1" ]; then
  case "$WAVE_FORMAT" in
    fst) TRACE_FLAG="--trace-fst" ;;
    *)   TRACE_FLAG="--trace" ;;
  esac
fi

run_one() {{
  local name="$1"; shift
  local top="$1"; shift
  local out="$HERE/obj_$name"
  local bin="$out/V$top"
  # 波形开关变化必须重建：带 --trace / --trace-fst 与不带是三套二进制。
  # 复用旧 binary 会造成两种假成功：
  #   * 加了 WAVE=1 却没有波形（旧 binary 无 trace）
  #   * WAVE_FORMAT=fst 却产出 VCD 内容（旧 binary 是 --trace 编的）
  # 所以标记文件里记下**格式**，不只是"开没开"。
  #
  # want_tag 必须在 if/else **之外**声明：若放在某一支里，另一支根本不执行
  # 声明，而脚本用 `set -u`，之后 `printf '%s' "$want_tag"` 报
  # `unbound variable` 并以 rc=1 失败。实测踩坑：全新目录**首次**构建
  # （走 then 分支，binary 不存在）100% 失败；第二次运行 binary 已存在、
  # 走 else 分支反而正常 —— 这个反差把缺陷完全掩盖住了。
  local want_tag="off"
  if [ "$WAVE" = "1" ]; then want_tag="$WAVE_FORMAT"; fi
  local need_build=0
  if [ ! -x "$bin" ]; then
    need_build=1
  else
    for arg in "$@"; do
      if [ -f "$arg" ] && [ "$arg" -nt "$bin" ]; then
        need_build=1
      fi
    done
    local have_tag=""
    if [ -f "$out/.wave_mode" ]; then have_tag="$(cat "$out/.wave_mode" 2>/dev/null || true)"; fi
    if [ "$want_tag" != "$have_tag" ]; then
      need_build=1
    fi
  fi
  echo "== sim: $name (top=$top) =="
  if [ "$need_build" -eq 1 ]; then
    rm -rf "$out"
    # --timescale 1ns/1ps：TB 头部写了 `timescale，但大量真实 RTL 把它交给
    # 编译选项、文件里不写。Verilator 5.x 对「部分模块有 timescale、部分没有」
    # 报 TIMESCALEMOD，并在默认配置下当 error 处理（"Exiting due to N
    # warning(s)"），于是一个完全合法的 DUT 直接构建失败 —— 实测无 timescale
    # 的 APB 从机 emit 返回 ok=True 但 run 脚本 rc!=0。
    # 这里用 --timescale 统一给**所有**模块同一个 timescale（与 tb_emitter 的
    # COMPILE_ARGS 口径一致），比 -Wno-TIMESCALEMOD 更正确：既不压制告警，
    # 又让 DUT 的时间单位真正确定下来。
    "$VLT" --binary --build-jobs 4 $TRACE_FLAG --timescale 1ns/1ps -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME -Wno-PINMISSING --top-module "$top" -Mdir "$out" "$@" >/dev/null
    printf '%s' "$want_tag" > "$out/.wave_mode"
  fi
  if [ "$WAVE" = "1" ]; then
    # +WAVE 让 TB 里的 $dumpvars 真正执行；+WAVE_FILE 指定落盘路径。
    # 只用环境变量不生效：TB 读的是 plusargs，不是 env。
    local wave_file="${{WAVE_DIR:-$HERE}}/wave_${{name}}.${{WAVE_FORMAT:-vcd}}"
    "$bin" +WAVE "+WAVE_FILE=$wave_file"
  else
    "$bin"
  fi
}}

case "${{1:-all}}" in
{cases_s}
  all)
    for s in {all_s}; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
"""


def emit_protocol_testbench(
    rtl_file: str,
    out_dir: str,
    protocol: Optional[str] = None,
) -> Dict[str, Any]:
    """Emit a self-checking SV testbench + Verilator runner from RTL AST for any supported bus protocol."""
    rp = Path(rtl_file).resolve()
    if not rp.is_file():
        return err_result("emit_testbench", f"RTL file not found: {rtl_file}")

    ex = tool_extract_interfaces(str(rp))
    if not ex.get("ok"):
        return err_result("emit_testbench", f"Interface extraction failed for {rtl_file}: {ex.get('error')}")
    mods = (ex.get("data") or {}).get("modules") or []
    if not mods:
        return err_result("emit_testbench", f"No modules found in {rtl_file}.")

    # 模块选择：一个文件里可以有多个 module（helper/package wrapper/顶层），
    # 旧实现固定取 mods[0]——若第一个是 helper，就会报 "Protocol 'apb' not found
    # in helper" 而拒绝一个完全合法的文件。正确做法是在所有模块里挑**含目标
    # 协议接口**的那个（auto 时挑第一个含任一支持协议的）。
    want = None if (not protocol or protocol == "auto") else protocol
    m = None
    chosen = None
    if want is not None:
        # 显式指定协议：直接找含该协议接口的模块（原逻辑）。
        for cand in mods:
            for i in (cand.get("interfaces") or []):
                if i.get("protocol") == want:
                    m, chosen = cand, i
                    break
            if m is not None:
                break
    else:
        # auto 模式：**优先选 role=slave 的接口**。
        #
        # 所有 TB 模板都是 slave 视角（TB 驱动请求侧、等 DUT 响应）。桥接 DUT
        # 同时暴露一个 slave 侧和一个 master 侧；旧实现按文件里接口的出现顺序
        # 取第一个命中的，可能选中 master 侧 —— 而模板期望的是 slave 方向，
        # 于是报「DUT port direction(s) disagree」把一个完全合法的桥接 DUT 拒掉
        # （实测 SPI→APB 桥：auto 选中 apb master 侧 → 方向不符被拒；而同一
        # DUT 传 protocol='spi' 反而能识别出 slave 侧）。
        #
        # 正确做法：auto 时先在所有模块里找 role=slave 的接口，找不到才退回
        # 「任意命中」。这样桥接 DUT 的 slave 侧稳定入选，进而走桥接分支生成
        # 联合 TB。
        for role_pref in ("slave", None):
            for cand in mods:
                for i in (cand.get("interfaces") or []):
                    if i.get("protocol") not in SUPPORTED_MULTI_PROTOCOLS:
                        continue
                    if role_pref is not None and i.get("role") != role_pref:
                        continue
                    m, chosen = cand, i
                    break
                if m is not None:
                    break
            if m is not None:
                break
    if m is None or chosen is None:
        found = {cand.get("module_name"): [i.get("protocol") for i in (cand.get("interfaces") or [])] for cand in mods}
        # 「识别到了协议」与「有对应 TB 模板」是两件事。分类器能认出 11 种协议，
        # emitter 只有 SUPPORTED_MULTI_PROTOCOLS 这 8 种模板。旧错误信息对两者
        # 一律说「not found」，于是 `axi4_lite` 的 DUT 会得到
        # 「No supported multi-bus interface found ... modules: {m: ['axi4_lite']}」
        # —— 用户会以为分类器没认出来，而实际是 分类器认出了、模板不支持。
        detected = sorted({p for protos in found.values() for p in protos if p})
        no_template = [p for p in detected if p not in SUPPORTED_MULTI_PROTOCOLS]
        if want is None and detected and no_template:
            return err_result(
                "emit_testbench",
                f"Cannot emit a testbench for {rp.name}: the RTL's bus protocol(s) "
                f"{no_template} were detected but this emitter has no testbench template "
                f"for them. Supported protocols: {list(SUPPORTED_MULTI_PROTOCOLS)}. "
                f"Detected per module: {found}.",
                data={"detected_protocols": detected,
                      "unsupported_protocols": no_template,
                      "supported_protocols": list(SUPPORTED_MULTI_PROTOCOLS)},
            )
        if want is not None and want in detected:
            return err_result(
                "emit_testbench",
                f"Protocol {want!r} was detected in {rp.name} but this emitter has no "
                f"testbench template for it. Supported protocols: "
                f"{list(SUPPORTED_MULTI_PROTOCOLS)}. Detected per module: {found}.",
                data={"detected_protocols": detected,
                      "unsupported_protocols": [want],
                      "supported_protocols": list(SUPPORTED_MULTI_PROTOCOLS)},
            )
        # 走到这里说明「既没有匹配 want 的模块，也没有 want 在检测集里」。
        # 旧文案 `Protocol 'axi4' not found in any module (modules: {...})`
        # 只说了没找到，不直接指出「这个 DUT 其实是 axi4_lite」——用户的第一
        # 反应会是「分类器没认出我的总线」，而实际是自己传错了协议名 / DUT
        # 版本与预期不同。把检测到的协议集合写进错误信息。
        if want is not None and detected:
            return err_result(
                "emit_testbench",
                f"Protocol {want!r} was requested but {rp.name} does not contain it. "
                f"Detected bus protocol(s): {detected}. "
                f"(Supported protocols: {list(SUPPORTED_MULTI_PROTOCOLS)}.) "
                f"Detected per module: {found}.",
                data={"requested_protocol": want,
                      "detected_protocols": detected,
                      "supported_protocols": list(SUPPORTED_MULTI_PROTOCOLS)},
            )
        return err_result(
            "emit_testbench",
            f"{'Protocol ' + repr(want) + ' not found' if want else 'No supported multi-bus interface found'} "
            f"in any module of {rp.name} (modules: {found}).",
        )
    mod_name = str(m.get("module_name") or rp.stem)

    # **多接口 DUT**：一个模块可能同时暴露多个同/异协议总线端口（如两个 APB
    # 从机 s0_*/s1_*）。模板只能驱动其中一个 —— 但旧实现静默只取第一个，
    # 其余接口的端口既不连接也不 tie-off：实测双 APB DUT 生成的 TB 里
    # `s1_*` 一个都不出现，s1 侧的输入悬空、行为未定义，而工具报 ok=True。
    # 这里把「未被覆盖的接口」显式报出来（W056），让调用方知道这份 TB
    # 只验证了 DUT 的一部分。
    # 多模块文件里可能有**多个**都匹配目标协议的模块（如两个不同地址宽度的
    # APB 从机）。旧实现静默取文件顺序里的第一个，用户在返回体里只能看到
    # module_name，如果没细看就无法察觉「测的不是我以为的那个模块」。
    # 这里把其它候选列出来（W057），让选择是显式的。
    _other_modules = []
    for _cand in mods:
        if _cand is m:
            continue
        for x in (_cand.get("interfaces") or []):
            if want is None:
                _ok_proto = x.get("protocol") in SUPPORTED_MULTI_PROTOCOLS
            else:
                _ok_proto = x.get("protocol") == want
            if _ok_proto:
                _other_modules.append({
                    "module_name": _cand.get("module_name"),
                    "protocol": x.get("protocol"),
                    "group_prefix": x.get("group_prefix"),
                })
                break
    _all_ifaces = [x for x in (m.get("interfaces") or []) if x is not chosen]
    _uncovered = [
        {"protocol": x.get("protocol"), "group_prefix": x.get("group_prefix"),
         "signal_count": len(x.get("port_map") or {})}
        for x in _all_ifaces
        if x.get("protocol") in SUPPORTED_MULTI_PROTOCOLS
        or (x.get("port_map") or {})
    ]
    # 时钟/复位必须从 RTL 真实推断出来，不能回退到 "clk"/"rst_n" 这类猜的名字：
    # DUT 没有 reset 端口时（extract_interfaces 会报 W011 并给 reset=None），
    # 旧实现仍硬连 `.rst_n(rst_n)`，得到 `%Error-PINNOTFOUND: Pin not found:
    # 'rst_n'` —— 编译失败但工具报 ok=True。
    clk_port = (m.get("clock") or {}).get("port")
    rst_port = (m.get("reset") or {}).get("port")
    if not clk_port:
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{protocol or 'auto'}' testbench for {mod_name}: no clock "
            "port detected in the RTL (the template must drive a clock). "
            "Add a clock port (clk/aclk/hclk/...) or wire it explicitly.",
        )
    if not rst_port:
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{protocol or 'auto'}' testbench for {mod_name}: no reset "
            "port detected in the RTL. The generated template hard-wires a reset pin, "
            "so a fabricated name would not compile (PINNOTFOUND). "
            "Add a reset port (rst/rst_n/aresetn/hresetn/...) or drive the DUT without one.",
        )
    clk = str(clk_port)
    rst = str(rst_port)
    proto = str(chosen["protocol"])

    # 生成前校验 TB 模板硬连的端口在 DUT 上确实存在，避免产出无法编译的 TB
    # 却仍报 ok=True（Verilator PINNOTFOUND 才是最终判据，但那已经太晚）。
    # extract_interfaces 的模块数据不含 ports 字段（只有 total_ports），所以
    # 端口名与方向都要从 RTL 直接解析一次拿到（缺端口校验与方向校验都需要）。
    from ..core.sv_parser import parse_sv_file as _psf

    rtl_ports: List[str] = []
    rtl_dir: Dict[str, str] = {}
    rtl_widths: Dict[str, int] = {}
    rtl_params: Dict[str, int] = {}
    for _m in _psf(rp):
        if _m.name == mod_name:
            rtl_ports = [p.name for p in _m.ports]
            rtl_dir = {p.name: str(p.direction) for p in _m.ports}
            rtl_widths = {p.name: int(p.width) for p in _m.ports}
            rtl_params = dict(_m.parameters or {})
            break

    # ---- 桥接识别（提前到缺端口/方向校验之前）----
    # 桥接 DUT 同时暴露 slave 侧和 master 侧。单协议模板的方向表是 **slave
    # 视角**（TB 驱动请求侧），而桥接的 slave 侧方向恰好相反（如 SPI 从机的
    # sclk 是 input 而非 output）—— 若先跑单协议方向校验，桥接 DUT 会被误判
    # 「DUT port direction disagree」而拒掉，根本到不了桥接分支（实测 SPI→APB
    # 桥复现）。所以这里先识别桥接结构；桥接路径会跳过下面的单协议校验。
    _is_bridge = False
    _bridge_info = None
    _explicit = protocol and str(protocol) not in ("auto", "")
    if not _explicit:
        from .bridge import classify_bridge
        _bridge_info = classify_bridge(m.get("interfaces") or [])
        _is_bridge = _bridge_info is not None

    _skip_single_proto_gates = _is_bridge

    missing_ports = [] if _skip_single_proto_gates else _missing_tb_ports(proto, chosen, rtl_ports)
    if missing_ports:
        # 若总线端口是用 SV `interface` 打包的，各个平铺信号本来就不在 DUT
        # 顶层 —— 报「DUT 缺少 psel->psel, ...」会把排查方向完全带偏。
        # 这种情况如实说明成因。
        _sv_if = chosen.get("sv_interface") if isinstance(chosen, dict) else None
        _if_type = (_sv_if or {}).get("interface_type") or ""
        if _sv_if and not (chosen.get("port_map") or {}):
            return err_result(
                "emit_testbench",
                f"Cannot emit a '{proto}' testbench for {mod_name}: the '{proto}' bus is "
                f"declared as an SV interface port '{_sv_if.get('port')}' of type "
                f"'{_if_type}'"
                + (f" (modport {_sv_if['modport']})" if _sv_if.get("modport") else "")
                + ". The interface's signals live inside the interface, so they cannot "
                f"be driven as flat DUT ports; the template would need the individual "
                f"signals ({', '.join(missing_ports)}). Either flatten the bus into plain "
                "ports in the RTL, or instantiate the interface in your own testbench "
                "and bind it to the DUT.",
                data={"protocol": proto, "sv_interface": _sv_if,
                      "unexpanded_signal_count": len(missing_ports)},
            )
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{proto}' testbench for {mod_name}: DUT is missing port(s) "
            f"{', '.join(missing_ports)} that the generated template drives. "
            f"Detected RTL ports: {sorted(rtl_ports)}",
        )

    # 方向校验：方向不匹配不会编译失败，但会在运行期永久挂起（TB 等不到响应），
    # 比编译失败更隐蔽——所以必须在生成前拦住。
    # 桥接 TB 自己处理两侧的方向（联合 TB 的 TB 在上游当 master、下游当 slave），
    # 不适用单协议的方向表 —— 桥接路径已在上面跳过了缺端口校验，这里同样跳过。
    wrong_dir = [] if _skip_single_proto_gates else _wrong_dir_ports(proto, chosen, rtl_dir)
    if wrong_dir:
        # 方向不符最常见的原因是**角色反了**：所有模板都假定 DUT 是「从设备」，
        # 而分类器的 role 字段已经知道 DUT 是 master。旧错误只列端口方向，
        # 用户会以为 RTL 写错了方向，实际是「主设备不能配从设备模板」。
        _role = str(chosen.get("role") or "")
        if _role == "master":
            # 各协议里 TB 扮演的**对端角色**并不相同：总线类（APB/AXI/AHB/DDR）
            # 是 slave，SPI 是从设备，Ethernet 是链路对端（对端 MAC/PHY），
            # DDR5 是 DRAM。旧文案一律说 "acts as a bus slave"，对 SPI/Ethernet
            # 会误导用户去找一个不存在的"从设备"。按协议给准确措辞。
            _peer = {
                "spi": "SPI slave (peripheral such as flash/sensor)",
                "ethernet": "link partner (peer MAC/PHY)",
                "ddr5": "DRAM device",
                "ddr3": "DRAM device",
                "ddr4": "DRAM device",
                "hbm": "HBM stack",
            }.get(proto, "bus slave")
            return err_result(
                "emit_testbench",
                f"Cannot emit a '{proto}' testbench for {mod_name}: the DUT was "
                f"classified as a {proto} **master**, but the generated testbench "
                f"acts as the **{_peer}** (it drives the response side and waits "
                f"for the DUT's requests). Port directions therefore disagree: "
                f"{'; '.join(wrong_dir)}. Write a master-side testbench manually, or "
                f"run emit_testbench on the peer device this master talks to.",
                data={"protocol": proto, "role": _role,
                      "testbench_role": _peer,
                      "direction_mismatches": wrong_dir},
            )
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{proto}' testbench for {mod_name}: DUT port direction(s) "
            f"disagree with the template: {'; '.join(wrong_dir)}. "
            "A direction mismatch does not fail to compile but hangs the simulation "
            "(the testbench waits forever for a response)."
            + (f" (Detected role: {_role}.)" if _role else ""),
            data={"protocol": proto, "role": _role,
                  "direction_mismatches": wrong_dir},
        )

    # 同 tb_emitter：空 out_dir 会写进 cwd 并报成功，必须显式拒绝。
    if not str(out_dir).strip():
        return err_result(
            "emit_testbench",
            "out_dir is required: refusing to write the generated testbench into "
            "the current working directory. Pass an explicit output directory.",
        )
    od = Path(out_dir).resolve()
    od.mkdir(parents=True, exist_ok=True)

    pcie_suites: List[str] = []
    pcie_matrix = False

    # ---- 桥接 DUT：走双边联合 TB ----
    # 桥接 DUT（AXI4→APB 等）同时暴露一个 slave 侧和一个 master 侧。单协议
    # 模板只驱动一侧、另一侧 tie-off，等于完全没验证桥接逻辑本身。这里先尝试
    # 识别桥接结构；识别到且组合已支持就改用联合 TB。
    # 注意：只有在调用方**没有显式指定** protocol，或指定的正是上游侧时才走桥接
    # —— 用户显式要 `protocol=apb` 时通常是想看下游侧，尊重其选择。
    # （桥接识别已在上面提前做过：_bridge_info / _is_bridge / _skip_single_proto_gates）
    if not _explicit and _bridge_info is not None:
        from .bridge import SUPPORTED_BRIDGES, emit_bridge

        _up_p, _dn_p, _up_i, _dn_i = _bridge_info
        try:
            tb_src = emit_bridge(mod_name, clk, rst, _up_p, _dn_p, _up_i, _dn_i)
        except ValueError as exc:
            # 识别到桥接但组合暂无联合模板：如实说明，**不**静默退化成
            # 单侧 TB（那会让用户以为桥接被验证过）。
            return err_result(
                "emit_testbench",
                f"Cannot emit a joint testbench for bridge {mod_name} "
                f"({_up_p} -> {_dn_p}): {exc}",
                data={"module_name": mod_name, "bridge": {"upstream": _up_p,
                                                          "downstream": _dn_p},
                      "supported_bridges": [
                          f"{a}->{b}" for (a, b) in sorted(SUPPORTED_BRIDGES)],
                      "hint": "pass protocol='<side>' to emit a single-sided TB"},
            )
    else:
        tb_src = None

    if tb_src is not None:
        # 桥接 TB 已生成：跳过单协议派发，但保留后处理（复位极性/落盘/脚本）
        pass
    elif proto == "apb":
        tb_src = _emit_apb_tb(mod_name, clk, rst, chosen)
    elif proto == "axi4":
        tb_src = _emit_axi4_tb(mod_name, clk, rst, chosen)
    elif proto == "axi4_stream":
        tb_src = _emit_axi4_stream_tb(mod_name, clk, rst, chosen)
    elif proto == "ahb_lite":
        tb_src = _emit_ahb_tb(mod_name, clk, rst, chosen)
    elif proto == "ddr3":
        tb_src = _emit_ddr3_tb(mod_name, clk, rst, chosen)
    elif proto == "ddr4":
        tb_src = _emit_ddr4_tb(mod_name, clk, rst, chosen)
    elif proto == "ddr5":
        tb_src = _emit_ddr5_tb(mod_name, clk, rst, chosen)
    elif proto == "spi":
        tb_src = _emit_spi_tb(mod_name, clk, rst, chosen)
    elif proto == "ethernet":
        tb_src = _emit_ethernet_tb(mod_name, clk, rst, chosen)
    elif proto == "hbm":
        tb_src = _emit_hbm_tb(mod_name, clk, rst, chosen)
    elif proto == "pcie":
        tb_src, pcie_suites, pcie_matrix = _emit_pcie_tb(
            mod_name, clk, rst, chosen,
            dut_params=rtl_params, port_widths=rtl_widths,
        )
    else:
        return err_result(
            "emit_testbench",
            f"Unsupported protocol: {proto}. This emitter has no testbench template "
            f"for it. Supported protocols: {list(SUPPORTED_MULTI_PROTOCOLS)}.",
            data={"unsupported_protocols": [proto],
                  "supported_protocols": list(SUPPORTED_MULTI_PROTOCOLS)},
        )

    # 复位极性：active-low 的模板形态是「声明 `, rst = 0` + 释放时 `rst = 1;`」。
    # active-high 时必须反过来。
    #
    # 这里**不再只靠**两处字符串 replace：模板缩进/写法一变（比如声明行不再是
    # `, {rst} = 0`、initial 里不再是 4 空格缩进），replace 会静默匹配不到，
    # 生成的 TB 就带着反相的复位跑下去 —— 仿真不报错，只是 DUT 永远停在复位
    # （或永远不复位），表现为莫名其妙的 FAIL/超时。因此改成：
    #   1) 先用正则按「语义」改写（容忍任意缩进/空格）；
    #   2) 改写后断言确实改到了（否则这条证据链就是断的），改不到就返回错误，
    #      绝不静默生成一份极性不可信的 TB。
    rst_active_low = bool((m.get("reset") or {}).get("active_low", True))
    if not rst_active_low:
        _before = tb_src
        # 声明处：`, rst = 0` -> `, rst = 1`（允许任意空白）
        tb_src, _n1 = re.subn(
            rf"(,\s*{re.escape(rst)}\s*=\s*)0\b", r"\g<1>1", tb_src, count=1)
        # 释放处：行首缩进 + `rst = 1;` -> `rst = 0;`
        tb_src, _n2 = re.subn(
            rf"^([ \t]*){re.escape(rst)}\s*=\s*1\s*;",
            lambda mm: f"{mm.group(1)}{rst} = 0;", tb_src, count=1, flags=re.M)
        if _n1 == 0 or _n2 == 0:
            return err_result(
                "emit_testbench",
                f"Cannot honour active-high reset '{rst}' for module {mod_name}: the "
                f"'{proto}' template's reset declaration/release no longer matches the "
                "expected shape, so the generated testbench would drive the reset with "
                "the wrong polarity (simulation would hang or never leave reset). "
                "Refusing to emit an unverified artifact.",
                data={"protocol": proto, "reset": rst, "matched_decl": _n1,
                      "matched_release": _n2, "template_changed": tb_src != _before},
            )

    # 给生成的 TB 插入 PINMISSING lint_off：DUT 常有 TB 不关心的额外端口
    # （调试/测试/scan 模式），`.*` 通配覆盖不到，Verilator 会报 PINMISSING
    # 并以 "Exiting due to N warning(s)" 阻断构建，而工具已报 ok=True。
    # top_module 必须与模板里**实际声明的 module 名**一致，否则 Verilator
    # --top-module 直接报 "not found in design"。桥接 TB 的 module 名是
    # `tb_<mod>_bridge`（不按单协议 proto 命名），所以这里要分支。
    top_module = f"tb_{mod_name}_bridge" if _is_bridge else f"tb_{mod_name}_{proto}"
    tb_path = od / f"{top_module}.sv"
    # 脚本名与 TB 名保持同一套 tag：桥接 TB 是 `_bridge`，单协议是 `_<proto>`。
    _tag = "bridge" if _is_bridge else proto
    sim_path = od / f"run_{mod_name}_{_tag}.sh"

    # **绝不能覆盖用户的输入文件**：当 out_dir 恰好是 rtl_file 所在目录、且
    # 用户文件与某个产物同名时（例如用户把上一次生成的 tb_*.sv 改名后当 RTL
    # 传回来），旧实现直接 write_text 覆盖 —— 实测用户 RTL 内容消失、工具报
    # ok=True。这是毁数据。两个产物都要在写之前核对。
    _rtl_resolved = rp.resolve()
    for _out in (tb_path, sim_path):
        if _out.resolve() == _rtl_resolved:
            return err_result(
                "emit_testbench",
                f"Refusing to emit: generated artifact {_out.name!r} would overwrite "
                f"the input RTL file {rp.name!r} (out_dir is the RTL's own directory "
                "and the names collide). Choose a different out_dir or rename the "
                "input file.",
                data={"protocol": proto, "colliding_artifact": _out.name,
                      "rtl_file": str(_rtl_resolved)},
            )
    # 先注入波形 dump，再套 lint_off：dump 要落在 initial 块内，
    # 而 lint_off 改的是 module 头部之后的 pragma 区，两者互不干扰。
    tb_with_dump = add_waveform_dump(tb_src, top_module)
    tb_path.write_text(add_lint_off(tb_with_dump), encoding="utf-8")

    sim_path.write_text(
        _emit_run_script(proto, mod_name, rp, tb_path, pcie_suites=pcie_suites, pcie_matrix=pcie_matrix),
        encoding="utf-8",
    )
    os.chmod(sim_path, os.stat(sim_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    suites = pcie_suites if proto == "pcie" else [proto]
    _diags: List[Dict[str, Any]] = []
    for _om in _other_modules:
        _diags.append({
            "severity": "WARNING",
            "code": "W057",
            "path": f"module[{_om.get('module_name')}]",
            "message": (
                f"{rp.name} contains another module '{_om.get('module_name')}' with a "
                f"'{_om.get('protocol')}' interface too, but this testbench targets "
                f"'{mod_name}' (the first match in file order). Generate a separate "
                "testbench per module if you meant that one."
            ),
        })
    for _u in _uncovered:
        _g = _u.get("group_prefix") or "default"
        _diags.append({
            "severity": "WARNING",
            "code": "W056",
            "path": f"interface[{_g}]",
            "message": (
                f"the DUT also exposes a '{_u.get('protocol')}' interface (group "
                f"'{_g}', {_u.get('signal_count')} signal(s)) that this testbench does "
                "not drive or check. Its ports are left unconnected, so that part of "
                "the DUT is not verified - and in some tools unconnected multi-bus DUT "
                "ports can even break elaboration (the emitted TB sets lint_off "
                "PINMISSING for the same reason). Generate a separate testbench for it "
                "(split the DUT, or wrap each interface in its own module)."
            ),
        })
    return ok_result(
        "emit_testbench",
        {
            "module_name": mod_name,
            "protocol": proto,
            "clock": clk,
            "reset": rst,
            "suites": suites,
            "artifacts": {
                "sv_testbench": str(tb_path),
                "sim_script": str(sim_path),
            },
            "uncovered_interfaces": _uncovered,
            "other_candidate_modules": _other_modules,
        },
        diagnostics=_diags,
    )
