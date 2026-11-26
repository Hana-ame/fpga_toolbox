"""Synopsys VCS/UVM, DC K-2015.06 & PrimeTime K-2015.06 Campaign Automation & Pitfall Guards.

Codifies production workflows and hard-won tool-boundary guards for ASIC/FPGA paper reproduction:
  * Remote csh-safe SSH/SCP runner generation with /dev/shm tmpfs staging & checkpoint/resume
  * VCS/UVM 1.2 golden log parser (delivered/fid_bad/NP_LAT/retry/UVM_ERROR/win_span/calibration)
  * DC K-2015.06 & PrimeTime K-2015.06 QoR/timing parser with fake-success & UID-423/119/530 guards
  * PrimeTime Verilog netlist blackbox cleaner (RDBUS/CLKCROSSBUS/CLKCROSSDFF -> scalar assign = 1'b0)
  * Paired-baseline bit-exact reproducibility verification
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result

# ---- VCS / UVM Golden Regexes (Paper 1 DLR-MAC §1.2) ----
# 数字模式：允许千分位逗号（真实 VCS/DC 日志常写 `10,000`）。
# 若只写 (\d+)，`delivered=10,000` 会被截成 10——实测当 expected_frames=10
# 时会**误判通过**（实际交付 10000 帧却报 10 并 PASS），属静默错判。
_NUM = r"\d[\d,]*"


def _to_int(s: str) -> int:
    """把可能带千分位逗号的数字串转 int。"""
    return int(s.replace(",", ""))


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


# 字段顺序未定义（真实日志可能写成 `FID: fid_bad=0 delivered=10000`），
# 因此不能要求 `delivered=` 紧跟 `[C3900] FID:`。旧正则在前置 fid_bad 时
# 完全匹配不到 -> delivered=None -> 该场次被判 FAIL。改为只锚定
# `delivered=` 本身（仍限定在 FID 语义下的唯一字段名，不会误匹配其它行）。
_UVM_DELIVERED_RE = re.compile(r"\bdelivered=(" + _NUM + r")")
_UVM_FID_BAD_RE = re.compile(r"fid_bad=(" + _NUM + r")")
_UVM_LAT_RE = re.compile(
    r"\[C3900\]\s+NP_LAT:\s+frames=(" + _NUM + r")"
    r"(?:\s+min=(" + _NUM + r"))?\s+avg=(" + _NUM + r")"
    r"(?:\s+max=(" + _NUM + r"))?"
)
_UVM_RETRY_RE = re.compile(r"all_retry=(" + _NUM + r")")
_UVM_SENT_RE = re.compile(r"send_nums=(" + _NUM + r")")
_UVM_ERR_RE = re.compile(r"UVM_ERROR\s*:\s+(" + _NUM + r")")
# UVM_FATAL 与 UVM_ERROR 是**并列**的失败计数，且 FATAL 更严重（仿真会立即中止）。
# 旧实现只读 UVM_ERROR，实测 `UVM_FATAL : 3` 的日志仍判 passed=True —— 一份明确
# 报告了 3 个致命错误的仿真被当成通过。
_UVM_FATAL_RE = re.compile(r"UVM_FATAL\s*:\s+(" + _NUM + r")")
_UVM_WIN_SPAN_RE = re.compile(r"win_span=(" + _NUM + r")")
_UVM_CALIB_RE = re.compile(
    r"pcs_words=(" + _NUM + r").*?flips_exp=([0-9.,]+).*?flips_act=(" + _NUM + r")"
)

# ---- DC / PT QoR Regexes (§2.1, §3.2) ----
_QOR_SLACK_RE = re.compile(r"Critical Path Slack:\s*([-+]?[\d,]+(?:\.\d+)?)")
_QOR_TNS_RE = re.compile(r"Total Negative Slack:\s*([-+]?[\d,]+(?:\.\d+)?)")
_QOR_VIOL_RE = re.compile(r"No\. of Violating Paths:\s*(" + _NUM + r")")
_QOR_LEAF_RE = re.compile(r"Leaf Cell Count:\s*(" + _NUM + r")")
_QOR_COMB_RE = re.compile(r"Combinational Area:\s*([0-9.,]+)")
_QOR_NONCOMB_RE = re.compile(r"Noncombinat(?:ional|orial) Area:\s*([0-9.,]+)")


def parse_uvm_log(log_text: str, expected_frames: Optional[int] = None) -> Dict[str, Any]:
    """Parse a VCS/UVM simulation log and apply strict pass/stale-simv gates."""
    lines = [ln for ln in log_text.splitlines() if ln.strip()]
    diags: List[Dict[str, Any]] = []

    def _last_int(pat: re.Pattern[str]) -> Optional[int]:
        matches = pat.findall(log_text)
        return _to_int(matches[-1]) if matches else None

    delivered = _last_int(_UVM_DELIVERED_RE)
    fid_bad = _last_int(_UVM_FID_BAD_RE)
    all_retry = _last_int(_UVM_RETRY_RE)
    send_nums = _last_int(_UVM_SENT_RE)
    uvm_error = _last_int(_UVM_ERR_RE)
    uvm_fatal = _last_int(_UVM_FATAL_RE)

    lat_matches = _UVM_LAT_RE.findall(log_text)
    frames_obs: Optional[int] = None
    lat_min: Optional[int] = None
    lat_avg: Optional[int] = None
    lat_max: Optional[int] = None
    if lat_matches:
        f_s, mn_s, av_s, mx_s = lat_matches[-1]
        frames_obs = _to_int(f_s)
        lat_min = _to_int(mn_s) if mn_s else None
        lat_avg = _to_int(av_s)
        lat_max = _to_int(mx_s) if mx_s else None

    win_spans = [_to_int(x) for x in _UVM_WIN_SPAN_RE.findall(log_text)]
    max_win_span = max(win_spans) if win_spans else None

    calib = _UVM_CALIB_RE.findall(log_text)
    pcs_words = _to_int(calib[-1][0]) if calib else None
    flips_exp = _to_float(calib[-1][1]) if calib else None
    flips_act = _to_int(calib[-1][2]) if calib else None

    # Stale simv / truncated log detection (§7)
    if len(lines) <= 2 or uvm_error is None:
        diags.append({
            "severity": "ERROR",
            "code": "E050",
            "path": "uvm.log",
            "message": "Log truncated or missing 'UVM_ERROR :' summary (possible stale simv binary or crash).",
        })

    target_frames = expected_frames if expected_frames is not None else frames_obs
    passed = (
        len(diags) == 0
        and delivered is not None
        and target_frames is not None
        and delivered == target_frames
        and fid_bad == 0
        and uvm_error == 0
        and not (uvm_fatal or 0)
        # 零帧不是"通过"：delivered=0 且 frames=0 时 0 == 0 成立，旧实现因此把
        # 一个**什么都没跑**的仿真判成通过（静默假通过）。要求至少交付 1 帧。
        and (target_frames or 0) > 0
        and (delivered or 0) > 0
    )

    data = {
        "passed": passed,
        "frames_expected": target_frames,
        "delivered": delivered,
        "fid_bad": fid_bad,
        "lat_min": lat_min,
        "lat_avg": lat_avg,
        "lat_max": lat_max,
        "all_retry": all_retry,
        "send_nums": send_nums,
        "retry_rate": round(all_retry / target_frames, 6) if (all_retry is not None and target_frames) else None,
        "uvm_error": uvm_error,
        "uvm_fatal": uvm_fatal,
        "max_win_span": max_win_span,
        "calibration": {
            "pcs_words": pcs_words,
            "flips_exp": flips_exp,
            "flips_act": flips_act,
            "act_over_exp": round(flips_act / flips_exp, 4) if (flips_act is not None and flips_exp) else None,
            "actual_rate": (flips_act / pcs_words) if (flips_act is not None and pcs_words) else None,
        } if pcs_words is not None else None,
    }
    if passed:
        return ok_result("parse_uvm_log", data, diagnostics=diags)
    return err_result(
        "parse_uvm_log",
        f"UVM run gate failed (delivered={delivered}/{target_frames}, fid_bad={fid_bad}, uvm_error={uvm_error}, uvm_fatal={uvm_fatal}).",
        diagnostics=diags,
        data=data,
    )


def aggregate_seed_medians(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute Table-3 style 5-seed medians and aggregate calibration rates.

    入参必须是 **``parse_uvm_log(...)["data"]``** 的内层数据，而不是它的返回值
    本身。若误传整条 ToolResult（``{ok, tool, data, diagnostics, error}``），
    旧实现会静默得出 ``n_passed=0 / lat_avg_median=None``——调用方看到的是
    「全部种子失败」或「没有数据」，而不是「参数层级传错了」。这里显式探测
    并抛 ``ValueError``，把静默错误变成立刻可见的错误。
    """
    for r in runs:
        if isinstance(r, dict) and "data" in r and "ok" in r and "tool" in r:
            raise ValueError(
                "aggregate_seed_medians() expects the inner data dict (take the "
                '\'data\' key of parse_uvm_log(...)), not the full ToolResult '
                "envelope. Pass [r['data'] for r in results]."
            )
    lats = [r["lat_avg"] for r in runs if r.get("lat_avg") is not None]
    retries = [r["retry_rate"] for r in runs if r.get("retry_rate") is not None]
    tot_words = sum((r.get("calibration") or {}).get("pcs_words") or 0 for r in runs)
    tot_exp = sum((r.get("calibration") or {}).get("flips_exp") or 0.0 for r in runs)
    tot_act = sum((r.get("calibration") or {}).get("flips_act") or 0 for r in runs)
    return {
        "n_runs": len(runs),
        "n_passed": sum(1 for r in runs if r.get("passed")),
        "lat_avg_median": statistics.median(lats) if lats else None,
        "retry_rate_median": round(statistics.median(retries), 6) if retries else None,
        "calib_act_over_exp": round(tot_act / tot_exp, 4) if tot_exp > 0 else None,
        "calib_actual_rate": (tot_act / tot_words) if tot_words > 0 else None,
    }


def parse_dc_pt_qor(report_text: str, raw_log_text: str = "") -> Dict[str, Any]:
    """Parse Synopsys DC/PT QoR report and flag K-2015.06 fake-success & UID pitfalls."""
    diags: List[Dict[str, Any]] = []
    combined = report_text + "\n" + raw_log_text

    # Pitfall E046: DC fake-success ("No paths." / UID-423)
    if "UID-423" in combined or "Invalid path group name" in combined:
        diags.append({
            "severity": "ERROR",
            "code": "E046",
            "path": "dc.qor",
            "message": "DC K-2015.06 UID-423: 'async_default' group unsupported in DC; measure recovery in PrimeTime with {**async_default**}.",
        })
    if len(report_text.strip()) < 700 and "No paths." in report_text:
        diags.append({
            "severity": "ERROR",
            "code": "E046",
            "path": "dc.qor",
            "message": "Fake-success report detected (<700B with 'No paths.').",
        })

    # Pitfall E047: set_max_fanout on net (UID-119)
    if "UID-119" in combined:
        diags.append({
            "severity": "ERROR",
            "code": "E047",
            "path": "dc.tcl",
            "message": "DC UID-119: set_max_fanout does not accept net objects (use enable_recovery_removal_arcs + high_fanout_net_threshold).",
        })

    # Pitfall E048: write_db unsupported in K-2015 (UID-530)
    if "UID-530" in combined or "write_db" in raw_log_text:
        diags.append({
            "severity": "ERROR",
            "code": "E048",
            "path": "dc.tcl",
            "message": "DC K-2015.06 UID-530: write_db / -format db unsupported; export Verilog (.v), .sdc, .sdf for PrimeTime.",
        })

    # Pitfall E049: OOM/disk kill
    if "Received Signal 15" in combined or "No space left on device" in combined:
        diags.append({
            "severity": "ERROR",
            "code": "E049",
            "path": "dc.env",
            "message": "Run terminated (Signal 15 / disk full). Stage workdir on /dev/shm and limit concurrent DC jobs <= 2.",
        })

    def _first_float(pat: re.Pattern[str], txt: str) -> Optional[float]:
        m = pat.search(txt)
        return _to_float(m.group(1)) if m else None

    def _first_int(pat: re.Pattern[str], txt: str) -> Optional[int]:
        m = pat.search(txt)
        return _to_int(m.group(1)) if m else None

    # Parse async_default section specifically if present (PrimeTime)
    async_sec = None
    m_async = re.search(r"Timing Path Group '\*\*async_default\*\*'.*?(?=\n\s*Timing Path Group|\Z)", report_text, re.S)
    if m_async:
        blk = m_async.group(0)
        async_sec = {
            "wns": _first_float(_QOR_SLACK_RE, blk),
            "tns": _first_float(_QOR_TNS_RE, blk),
            "violating_paths": _first_int(_QOR_VIOL_RE, blk),
        }

    comb_area = _first_float(_QOR_COMB_RE, report_text)
    noncomb_area = _first_float(_QOR_NONCOMB_RE, report_text)
    total_area = round(comb_area + noncomb_area, 6) if (comb_area is not None and noncomb_area is not None) else None

    all_slacks = [v for v in (_to_float(x) for x in _QOR_SLACK_RE.findall(report_text)) if v is not None]
    all_tns = [v for v in (_to_float(x) for x in _QOR_TNS_RE.findall(report_text)) if v is not None]
    all_viols = [v for v in (_to_int(x) for x in _QOR_VIOL_RE.findall(report_text)) if v is not None]
    worst_wns = min(all_slacks) if all_slacks else None
    total_tns = round(sum(all_tns), 6) if all_tns else None
    total_viols = sum(all_viols) if all_viols else None

    data = {
        "wns": worst_wns,
        "tns": total_tns,
        "violating_paths": total_viols,
        "leaf_cell_count": _first_int(_QOR_LEAF_RE, report_text),
        "combinational_area": comb_area,
        "noncombinational_area": noncomb_area,
        "total_cell_area": total_area,
        "async_default": async_sec,
        "timing_clean": (worst_wns is not None and worst_wns >= 0.0 and total_viols == 0),
    }
    if diags:
        return err_result(
            "parse_dc_pt_qor",
            f"Detected {len(diags)} Synopsys DC/PT pitfall(s).",
            diagnostics=diags,
            data=data,
        )
    return ok_result("parse_dc_pt_qor", data)


def verify_paired_baseline_reproducibility(run_a: Dict[str, Any], run_b: Dict[str, Any]) -> Dict[str, Any]:
    """Verify bit-exact reproducibility between two baseline DC runs (§2.4 & §8.1)."""
    # 两个指标都必须**有值且相等**才算 bit-exact。旧实现只要求
    # leaf_cell_count 非 None，于是 total_cell_area 双方缺失时 `None == None`
    # 会判为「逐位一致」，bit_exact=True——用小样本证明核心结论，属假通过
    # （文档 §2.4 / §8.1 要求用 Leaf Cell Count **与**总面积共同证明）。
    ca, cb = run_a.get("leaf_cell_count"), run_b.get("leaf_cell_count")
    aa, ab = run_a.get("total_cell_area"), run_b.get("total_cell_area")
    exact_cells = ca is not None and cb is not None and ca == cb
    exact_area = aa is not None and ab is not None and aa == ab
    ok = exact_cells and exact_area
    data = {
        "bit_exact": ok,
        "leaf_cell_count_a": run_a.get("leaf_cell_count"),
        "leaf_cell_count_b": run_b.get("leaf_cell_count"),
        "total_cell_area_a": run_a.get("total_cell_area"),
        "total_cell_area_b": run_b.get("total_cell_area"),
    }
    return ok_result("verify_paired_baseline", data) if ok else err_result(
        "verify_paired_baseline", "Paired baseline runs are not bit-exact identical."
    ) | {"data": data}


def _flatten_nets(expr: str) -> List[str]:
    """Flatten a Verilog port connection (including concatenations `{a, b}`) into scalar net names.

    注意：**保留位选择**（`.o_data({q_a[1], q_a[0]})` -> `['q_a[1]', 'q_a[0]']`，
    而不是合并成 `q_a`）。因为下游要求「逐根插入标量 assign」，对 `{q_a[1], q_a[0]}`
    这种拼接，展开成两条标量 assign 才能精确覆盖被 blackbox 驱动的位；若合并成
    `assign q_a = 1'b0;` 会用 1 位去驱动多位向量，反而引入位宽不匹配。
    """
    cleaned = expr.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        cleaned = cleaned[1:-1]
    nets: List[str] = []
    for part in cleaned.split(","):
        tok = part.strip()
        if not tok or tok.startswith("1'b") or tok.startswith("0") or tok.isdigit():
            continue
        if tok not in nets:
            nets.append(tok)
    return nets


def clean_netlist_for_pt(
    verilog_text: str,
    blackbox_modules: Sequence[str] = ("RDBUS", "CLKCROSSBUS", "CLKCROSSDFF"),
    output_port_prefixes: Sequence[str] = ("o_",),
) -> Tuple[str, Dict[str, Any]]:
    """Clean unresolved parameterized blackbox instances before PrimeTime link_design (§3.1).

    Removes instances of ``blackbox_modules`` inside each ``module ... endmodule`` block
    and emits scalar ``assign <net> = 1'b0;`` statements right before ``endmodule``
    (PrimeTime rejects concatenated ``assign {a,b} = 0;``).
    """
    bb_alt = "|".join(re.escape(m) for m in blackbox_modules)
    # 实例匹配必须容忍嵌套括号：
    #   1) 参数化覆盖 `RDBUS #(.WIDTH(8)) u_x (...)` —— 旧实现用 `\((.*?)\)`
    #      非贪婪，遇到 `#(` 里的第一个 `)` 就截断，导致这类实例完全匹配不到
    #      （removed_instances 恒为 0）。而这恰恰是本文档要解决的
    #      「参数化 RDBUS 位宽不一致」主场景。
    #   2) 端口值可含嵌套（`{a, b}` / `o[3:0]` / 嵌套调用）。
    # 用显式结构：可选 `#(...)` + 实例名 + 配平端口括号，两层都允许嵌套。
    _nested = r"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*"
    inst_re = re.compile(
        # 不能锚定行首 `^[ \t]*`：DC 导出的网表常把整个模块压成一行
        # （`module t(o); output o; RDBUS #(.W) u (...); endmodule`），此时实例
        # 前面是 `; ` 而不是行首，锚定行首会完全匹配不到（实测 removed=0）。
        # 用 `(?<![\w.$])` 保证只在标识符边界起匹配，不误伤 `my_RDBUS_x` 之类。
        rf"(?<![\w.$])(?:{bb_alt})\b"
        rf"(?:\s*#\s*\(\s*{_nested}\s*\))?"
        rf"\s+(?:\\\S+|[A-Za-z0-9_\$]+)\s*\(\s*({_nested}\s*)\)\s*;",
        re.M | re.S,
    )
    port_re = re.compile(r"\.([A-Za-z0-9_]+)\s*\((" + _nested + r")\)")
    # `endmodule` 可能出现在注释或字符串里。旧的 `.*?endmodule` 会在注释中的
    # endmodule 上提前截断，导致该块之后的实例（如紧随注释的 RDBUS）落在块外
    # 而永不被清理（实测 removed_instances=0，网表原样输出）。
    # 用「模块体原子」逐 token 前进：注释/字符串整体跳过，只有真正的
    # endmodule 才结束匹配。
    # `endmodule` 可能出现在注释或字符串里。旧的 `.*?endmodule` 会在注释中的
    # endmodule 上提前截断，使该块之后的实例（如紧随注释的 RDBUS）落在块外而
    # 永不被清理（实测 removed_instances=0，网表原样输出）。
    # 逐个 token 前进：注释/字符串整体跳过，只有真正的 endmodule 才结束匹配。
    _SKIP = r'''/\*.*?\*/|//[^\n]*|"(?:[^"\\]|\\.)*"'''
    mod_re = re.compile(
        r"module\s+[A-Za-z0-9_\$](?:" + _SKIP + r"|(?!\bendmodule\b)[\s\S])*?\bendmodule\b",
        re.S,
    )
    # 注意：_clean_module 用 m.group(1) 取整块，因此上面整段必须是一个捕获组。
    # 但 `_SKIP` 里已含非捕获组，这里用外层括号包住整个模式。
    mod_re = re.compile(
        "(" + mod_re.pattern + ")",
        re.S,
    )

    removed_instances = 0
    tied_nets_total: List[str] = []

    def _clean_module(m: re.Match[str]) -> str:
        nonlocal removed_instances
        block = m.group(1)
        mod_ties: List[str] = []

        def _strip_inst(im: re.Match[str]) -> str:
            nonlocal removed_instances
            removed_instances += 1
            body = im.group(1)
            for pm in port_re.finditer(body):
                pname, conn = pm.group(1), pm.group(2)
                if any(pname.startswith(pref) for pref in output_port_prefixes):
                    for net in _flatten_nets(conn):
                        if net not in mod_ties:
                            mod_ties.append(net)
            return "// [fpga_toolbox:pt_clean] removed unresolved parameterized blackbox instance"

        new_block = inst_re.sub(_strip_inst, block)
        if mod_ties:
            tied_nets_total.extend(mod_ties)
            assigns = "\n".join(f"  assign {net} = 1'b0;" for net in mod_ties)
            # 只能在**真正的** endmodule 前插入 assign。旧实现用
            # re.sub(r"\bendmodule\b", ...) 会命中注释里的 endmodule，把
            # assign 语句插进注释中间，产出语法错误的网表（实测
            # `// mentions   assign o = 1'b0;\nendmodule but ...`）。
            # 这里定位块内最后一个被注释/字符串包住的 endmodule 之后的位置。
            real_end = None
            scan_re = re.compile(
                _SKIP + r"|\bendmodule\b",
                re.S,
            )
            for mm in scan_re.finditer(new_block):
                if mm.group(0) == "endmodule":
                    real_end = mm.start()
            if real_end is not None:
                new_block = new_block[:real_end] + assigns + "\n" + new_block[real_end:]
        return new_block

    cleaned = mod_re.sub(_clean_module, verilog_text)
    return cleaned, {
        "removed_instances": removed_instances,
        "tied_off_nets_count": len(tied_nets_total),
        "tied_off_nets": tied_nets_total,
        "blackbox_modules": list(blackbox_modules),
    }


# host / tag / 文件名允许的字符。生成的脚本会被 scp 到远端执行，参数若含
# shell 元字符就能注入任意命令（实测 host='x"; touch /tmp/PWNED; "' 会真的
# 执行 touch）。这里做白名单校验，从源头挡住注入。
# 注意必须用 `\Z` 而不是 `$`：Python 的 `$` 也匹配「末尾换行符之前」的位置，
# 于是 `tag='t\n'` 能通过校验，插入脚本后变成
#   TAG="t
#   "
# —— 换行把字符串字面量截断，后续内容成为独立 shell 命令（实测 host='h\n'
# 生成 HOST="host1 后接换行）。这是可用的命令注入绕道，四个白名单全部改成 \Z。
_HOST_RE = re.compile(r"^[A-Za-z0-9_.@:\[\]-]+\Z")
_TAG_RE = re.compile(r"^[A-Za-z0-9_.-]+\Z")
# remote_work_dir 允许目录分隔符（~/work、/tmp/foo），但绝不允许 shell 元字符：
# 它会被原文插进**远端执行**的脚本里（use_dev_shm=False 路径的
# `mkdir -p {remote_work_dir}/{tag}`）。漏校验它会让 host/tag/script 三道白名单
# 形同虚设 —— 攻击面从另一条参数绕进来。
_REMOTE_WORK_DIR_RE = re.compile(r"^[A-Za-z0-9_./~\-]+\Z")


def generate_remote_run_script(
    host: str,
    tag: str,
    local_script_name: str,
    remote_work_dir: str = "~/work",
    use_dev_shm: bool = True,
) -> str:
    """Generate a csh-safe remote SSH/SCP launcher with /dev/shm staging & progress tracking (§0.1, §9).

    参数会被写入待执行的 shell 脚本，因此做严格白名单校验：``host`` 只允许
    主机名/用户@主机/IPv6 括号形式，``tag`` 只允许 ``[A-Za-z0-9_.-]``，
    ``local_script_name`` 必须是纯文件名（不含路径与 shell 元字符），
    ``remote_work_dir`` 允许目录分隔符但同样拒绝 shell 元字符（它会被原文插进
    远端执行的脚本）。
    非法输入抛 ``ValueError`` 而不是生成一个带注入的脚本。
    """
    if not _HOST_RE.match(host):
        raise ValueError(
            f"Invalid host {host!r}: allowed characters are [A-Za-z0-9_.@:[]-] "
            "(user@host / IPv4 / [IPv6]). Shell metacharacters are rejected "
            "because the generated script is executed on the remote side."
        )
    if not _TAG_RE.match(tag):
        raise ValueError(
            f"Invalid tag {tag!r}: allowed characters are [A-Za-z0-9_.-]. "
            "The tag is used as a /dev/shm directory name in the generated script."
        )
    # 字符白名单挡不住**语义**危险：`.` 和 `..` 都只含允许字符，但它们作为
    # 路径段会让「清理临时目录」越界。旧实现只查字符，实测：
    #   tag='..'  -> WORK="/dev/shm/.."      -> 脚本执行 rm -rf /dev/shm/..  (= /dev)
    #   tag='.'   -> WORK="/dev/shm/."       -> rm -rf /dev/shm/.  (= /dev/shm 整目录)
    # 生成脚本末尾有 `rm -rf $WORK`，所以纯点的 tag 会删掉远超预期的东西
    # （与已修的 remote_work_dir='/' 属同一类问题，只是从 tag 侧绕进来）。
    if tag in (".", "..") or ".." in tag.split("."):
        raise ValueError(
            f"Invalid tag {tag!r}: '.' and '..' (and any component containing "
            "'..') are not valid directory names. The tag names a /dev/shm "
            "directory that the generated script later removes with rm -rf."
        )
    if not _REMOTE_WORK_DIR_RE.match(remote_work_dir):
        raise ValueError(
            f"Invalid remote_work_dir {remote_work_dir!r}: allowed characters are "
            "[A-Za-z0-9_./~-] (e.g. ~/work, /tmp/foo). Shell metacharacters are "
            "rejected because remote_work_dir is interpolated verbatim into the "
            "script executed on the remote side (use_dev_shm=False path)."
        )
    # 字符白名单挡不住**语义**危险：`..` 和根路径都能构造出远超预期范围的
    # 删除命令。旧实现只查字符，实测：
    #   remote_work_dir='/'                -> WORK="//t"        -> rm -rf //t  (= /t)
    #   remote_work_dir='~/work/..'        -> WORK="~/work/../t" -> rm -rf ~/t
    #   remote_work_dir='/dev/shm/..'      -> WORK="/dev/shm/../t" -> rm -rf /t
    # 生成脚本末尾是 `$SSH_CMD "bash -c 'rm -rf $WORK'"`，于是「清理临时目录」
    # 会变成删除主目录下的任意同名目录。这里显式拒绝 `..` 段与根路径。
    _wd_norm = remote_work_dir.rstrip("/")
    if ".." in remote_work_dir.split("/") or _wd_norm in ("", "~", "/"):
        raise ValueError(
            f"Invalid remote_work_dir {remote_work_dir!r}: must be a concrete "
            "sub-directory, not a root/'~' or a path containing '..'. The generated "
            "script ends with `rm -rf $WORK`, so a path that escapes the intended "
            "staging directory would delete unrelated files on the remote host."
        )
    base = Path(local_script_name).name
    if base != local_script_name or not re.match(r"^[A-Za-z0-9_.-]+\Z", base):
        raise ValueError(
            f"Invalid local_script_name {local_script_name!r}: must be a bare file "
            "name with only [A-Za-z0-9_.-] (no directories, no shell metacharacters)."
        )
    # 远端工作目录必须与 use_dev_shm 一致：脚本头把它算成 $WORK，下面的
    # mkdir/cd 以及**打印给用户的 scp/rm -rf 提示**都用 $WORK。
    # 旧实现只改了 mkdir/cd，末尾两条提示仍硬编码 /dev/shm/$TAG ——
    # use_dev_shm=False 时用户照着提示 scp 会去 /dev/shm 找一个不存在的目录
    # （报告其实在 remote_work_dir 下），rm -rf 也删不到真正的工作目录。
    work_dir = f"/dev/shm/{tag}" if use_dev_shm else f"{remote_work_dir}/{tag}"
    shm_line = f'  mkdir -p {work_dir} && cd {work_dir}\n'
    return f"""#!/usr/bin/env bash
# Auto-generated by fpga_toolbox.eda_campaign (csh-safe + /dev/shm tmpfs discipline)
set -euo pipefail
HOST="{host}"
TAG="{tag}"
WORK="{work_dir}"
SCRIPT="{local_script_name}"
SSH_CMD="ssh -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa $HOST"

scp "$SCRIPT" "$HOST:/tmp/$(basename "$SCRIPT")"
$SSH_CMD "bash -c '
{shm_line}  nohup bash /tmp/$(basename "$SCRIPT") > /tmp/${{TAG}}.nohup 2>&1 &
  echo PID=\\$!
'"
echo "Launched $TAG on $HOST. Collect reports after completion:"
echo "  scp -r $HOST:$WORK/report ./${{TAG}}_report"
echo "  $SSH_CMD \\"bash -c 'rm -rf $WORK'\\""
"""


def tool_clean_pt_netlist(
    in_verilog: str,
    out_verilog: str,
    blackbox_modules: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Tool wrapper: clean unresolved blackboxes from a DC Verilog netlist for PrimeTime."""
    inp = Path(in_verilog)
    if not inp.is_file():
        return err_result("clean_pt_netlist", f"Netlist not found: {in_verilog}")
    # 与 sv_parser.py / validator.py 一致：DC 导出的网表注释里可能有
    # GBK/Shift-JIS/latin-1 字节，裸 read_text(utf-8) 会抛 UnicodeDecodeError
    # 带堆栈冒泡（"which byte" 之类信息全无）。errors="replace" 只影响注释文本，
    # 模块/实例结构照常解析。
    cleaned, stats = clean_netlist_for_pt(
        inp.read_text(encoding="utf-8", errors="replace"),
        blackbox_modules=tuple(blackbox_modules) if blackbox_modules else ("RDBUS", "CLKCROSSBUS", "CLKCROSSDFF"),
    )
    outp = Path(out_verilog)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(cleaned, encoding="utf-8")
    stats["out_verilog"] = str(outp.resolve())

    # 产物自检：清理后输出里不应再出现黑盒模块的**实例**。若仍有残留，
    # 说明输入里那些实例的写法没被正则覆盖（如缺少分号、语法不完整、黑盒名
    # 出现在无法识别的构造里）。旧实现此时仍返回 ok=True + removed_instances=0，
    # 用户以为清理完了，实际 PrimeTime 仍会 link 失败——不仅要清理，还要
    # 证明清理生效。
    bb_names = blackbox_modules or ["RDBUS", "CLKCROSSBUS", "CLKCROSSDFF"]
    # 检测残留前先剥掉注释与字符串：黑盒名可能只出现在注释里（如
    # `// RDBUS is used elsewhere`），那不是实例，不能误报成未清理。
    _no_comment = re.sub(r"/\*.*?\*/|//[^\n]*|\"(?:[^\"\\]|\\.)*\"", " ", cleaned, flags=re.S)
    residual = []
    for name in bb_names:
        # 只认「实例化」形态：模块名后跟参数块 `#(` 或实例标识符
        # 实例名可能是 Verilog 转义标识符（`\u_bb/0 (...)`），也可能以 $
        # 开头；`\b` 在 `\` 之后不成立，旧正则漏掉转义实例名导致的残留。
        if re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}\s*(?:#\s*\(|[A-Za-z_$\\])",
                     _no_comment):
            residual.append(name)
    if residual:
        return err_result(
            "clean_pt_netlist",
            f"Blackbox instance(s) still present in the cleaned netlist: {residual}. "
            "Their instantiation syntax was not matched (missing semicolon / truncated "
            "statement / unsupported construct). PrimeTime would still fail link_design.",
            diagnostics=[{
                "severity": "ERROR",
                "code": "E051",
                "path": "pt.netlist",
                "message": f"Unremoved blackbox instance(s) {residual}; netlist is not PT-clean.",
            }],
            data=stats,
        )
    return ok_result("clean_pt_netlist", stats)
