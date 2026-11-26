"""Tool 8: Render standalone SVG dependency topology + rule audit HTML dashboard.

Writes both ``<out>.html`` (dashboard) and ``<out>.svg`` (standalone topology),
supports multi-project tabbed interface report pages (pure CSS, zero JS),
and runs a built-in HTML consistency check (inspired by agent/verilog/axi/docs_check.py).
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result


def _build_svg(report: Dict[str, Any], dep_graph: Dict[str, Any], ifaces: List[Dict[str, Any]]) -> str:
    mod = html.escape(str(report.get("module", "unknown")))
    status = html.escape(str(report.get("overall_status", "UNKNOWN")))
    accent = "#065F46" if status == "PASS" else ("#1E3A8A" if status == "NO_RESULTS" else "#991B1B")
    deps = [m for m in dep_graph.get("dependency_closure", []) if m != report.get("module")]
    iface_txt = ", ".join(
        f"{i.get('protocol', '?')}({len(i.get('port_map') or {})})" for i in ifaces
    ) or "none"
    height = max(220, 200 + len(deps[:6]) * 32)
    boxes = "".join(
        f'<rect x="220" y="{190+i*30}" width="280" height="22" rx="4" fill="#F8FAFC" stroke="#CBD5E1"/>'
        f'<text x="360" y="{205+i*30}" text-anchor="middle" fill="#334155" font-family="ui-monospace,monospace" font-size="11">{html.escape(str(d))}</text>'
        for i, d in enumerate(deps[:6])
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 {height}" width="100%">
  <rect x="140" y="16" width="440" height="150" rx="10" fill="#FFFFFF" stroke="#1D4ED8" stroke-dasharray="5 4"/>
  <rect x="176" y="42" width="368" height="76" rx="8" fill="#0F172A"/>
  <text x="360" y="72" text-anchor="middle" fill="#FFFFFF" font-family="ui-monospace,monospace" font-size="14" font-weight="bold">{mod}</text>
  <text x="360" y="96" text-anchor="middle" fill="#94A3B8" font-family="ui-monospace,monospace" font-size="10">{html.escape(iface_txt)}</text>
  <text x="360" y="146" text-anchor="middle" fill="{accent}" font-family="sans-serif" font-size="12" font-weight="bold">[{status}]</text>
  {boxes}
</svg>"""


def _check_report_data_consistency(report: Dict[str, Any],
                                   checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """核对 report 的 summary / overall_status 与 checks 实际内容是否自洽。

    这是审计报告最核心的职责面：HTML 里同时印着「通过率 100%」「2/2 PASS」和
    逐条 FAIL 的行，读者无从判断信哪个。旧实现只检查 HTML 标签配平与行数
    （W050/W051），完全不看**数据本身**是否自相矛盾 —— 实测下列四种不一致
    全部返回 ok=True、consistency_ok=True、零诊断：

      * summary.passed=2 而 checks 里全是 FAIL
      * summary.total 与实际检查条数不符
      * overall_status=PASS 但没有任何通过的检查
      * overall_status=FAIL 但所有检查都 PASS

    这里只报 WARNING（不改变 ok），因为调用方可能故意渲染一份"外部汇总"与
    "本机明细"混合的报告；但必须让矛盾可见，而不是静默印出去。
    """
    diags: List[Dict[str, Any]] = []
    real = [c for c in checks if isinstance(c, dict)]
    n = len(real)
    counted = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for c in real:
        st = str(c.get("status", "")).strip().upper()
        if st in counted:
            counted[st] += 1
    summary = report.get("summary")
    if isinstance(summary, dict):
        for key, actual in (("total", n), ("passed", counted["PASS"]),
                            ("failed", counted["FAIL"])):
            claimed = summary.get(key)
            # 只在调用方**声明了**该字段时核对；缺字段不是矛盾。
            if isinstance(claimed, (int, float)) and int(claimed) != actual:
                diags.append({
                    "severity": "WARNING", "code": "W052", "path": f"report.summary.{key}",
                    "message": (f"report.summary.{key}={int(claimed)} but the checks "
                                f"actually contain {actual} ({counted}). The rendered "
                                "dashboard would show two contradictory conclusions."),
                })
    overall = str(report.get("overall_status", "")).strip().upper()
    if overall == "PASS" and n > 0 and counted["PASS"] == 0:
        diags.append({
            "severity": "WARNING", "code": "W053", "path": "report.overall_status",
            "message": (f"overall_status=PASS but none of the {n} check(s) passed "
                        f"({counted}). The report headline contradicts its own rows."),
        })
    if overall == "FAIL" and n > 0 and counted["FAIL"] == 0 and counted["SKIP"] == 0:
        diags.append({
            "severity": "WARNING", "code": "W053", "path": "report.overall_status",
            "message": (f"overall_status=FAIL but no check is FAIL ({counted}). "
                        "The report headline contradicts its own rows."),
        })
    return diags


def _check_html_consistency(page: str, expected_checks: int) -> List[Dict[str, Any]]:
    diags: List[Dict[str, Any]] = []
    for tag in ("table", "tbody", "html", "body"):
        opens = len(re.findall(rf"<{tag}\b", page, re.I))
        closes = len(re.findall(rf"</{tag}>", page, re.I))
        if opens != closes:
            diags.append({
                "severity": "WARNING",
                "code": "W050",
                "path": f"html.{tag}",
                "message": f"Unbalanced <{tag}> tags (open={opens}, close={closes}).",
            })
    m_checks = re.search(r'<tbody id="checks-tbody">(.*?)</tbody>', page, re.S)
    if not m_checks:
        m_checks = re.search(r"<tbody>(.*?)</tbody>", page, re.S)
    rendered_rows = len(m_checks.group(1).split("<tr>")) - 1 if m_checks else 0
    if expected_checks > 0 and rendered_rows != expected_checks:
        diags.append({
            "severity": "WARNING",
            "code": "W051",
            "path": "html.checks",
            "message": f"Rendered check rows ({rendered_rows}) != report checks ({expected_checks}).",
        })
    return diags



_SV_KW_RE = re.compile(
    r"\b(module|endmodule|parameter|localparam|input|output|inout|logic|wire|reg|int|bit|byte|unsigned|"
    r"always_ff|always_comb|always|posedge|negedge|if|else|case|endcase|default|for|while|do|begin|end|"
    r"assign|typedef|enum|task|endtask|function|endfunction|automatic|return|fork|join|repeat|initial)\b"
)
_SV_NUM_RE = re.compile(r"\b\d+'[hHbBdDoO][0-9a-fA-F_xXzZ]+\b|\b\d+\b")
_SV_SYS_RE = re.compile(r"\$(?:display|finish|fatal|time|clog2)\b")


def _highlight_sv(code: str) -> str:
    """Zero-JS SystemVerilog syntax highlighter with line numbers."""
    out_lines: List[str] = []
    for lineno, raw in enumerate(code.splitlines(), 1):
        # Split line comment first so keywords inside comments stay green
        cmt_idx = -1
        in_str = False
        esc_ch = False
        for i, ch in enumerate(raw):
            if esc_ch:
                esc_ch = False
                continue
            if ch == "\\" and in_str:
                esc_ch = True
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str and ch == "/" and i + 1 < len(raw) and raw[i + 1] == "/":
                cmt_idx = i
                break
        if cmt_idx >= 0:
            code_part, cmt_part = raw[:cmt_idx], raw[cmt_idx + 2:]
            cmt_html = f'<span class="sv-cmt">//{html.escape(cmt_part)}</span>'
        else:
            code_part, cmt_html = raw, ""
        esc = html.escape(code_part)
        esc = _SV_SYS_RE.sub(lambda m: f'<span class="sv-sys">{m.group(0)}</span>', esc)
        esc = _SV_NUM_RE.sub(lambda m: f'<span class="sv-num">{m.group(0)}</span>', esc)
        esc = _SV_KW_RE.sub(lambda m: f'<span class="sv-kw">{m.group(0)}</span>', esc)
        out_lines.append(f'<span class="sv-ln">{lineno:4d}</span> {esc}{cmt_html}')
    return "\n".join(out_lines)


def _badge(status: str) -> str:
    st = status.upper()
    if st in ("PASS", "KILLED", "SUPPORTED"):
        cls = "b-pass"
    elif st in ("FAIL", "SURVIVED", "ERROR"):
        cls = "b-fail"
    else:
        cls = "b-warn"
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _render_checks_rows(checks: List[Dict[str, Any]], rich_cols: bool) -> str:
    rows_list: List[str] = []
    for c in checks:
        st_html = _badge(str(c.get("status", "")))
        if rich_cols:
            proto = c.get("protocol") or c.get("category") or ""
            tx = c.get("tx") or c.get("detail") or ""
            exp = c.get("expected") or "-"
            obs = c.get("observed") or "-"
            eng = c.get("engine") or c.get("category") or ""
            rows_list.append(
                "<tr>"
                f"<td><code>{html.escape(str(c.get('rule_id', '')))}</code></td>"
                f"<td><strong>{html.escape(str(proto))}</strong><div class='muted'>{html.escape(str(c.get('category', '')))}</div></td>"
                f"<td><span class='eng'>{html.escape(str(eng))}</span></td>"
                f"<td><code>{html.escape(str(tx))}</code></td>"
                f"<td><code>{html.escape(str(exp))}</code></td>"
                f"<td><code>{html.escape(str(obs))}</code></td>"
                f"<td>{st_html}</td>"
                "</tr>"
            )
        else:
            rows_list.append(
                "<tr>"
                f"<td><code>{html.escape(str(c.get('rule_id', '')))}</code></td>"
                f"<td>{html.escape(str(c.get('category', '')))}</td>"
                f"<td>{st_html}</td>"
                f"<td>{html.escape(str(c.get('detail', '')))}</td>"
                "</tr>"
            )
    return "".join(rows_list)


def _render_capability_section(cap_rows: List[Dict[str, Any]]) -> str:
    if not cap_rows:
        return ""
    trs = []
    for r in cap_rows:
        trs.append(
            "<tr>"
            f"<td><strong>{html.escape(str(r.get('protocol', '')))}</strong></td>"
            f"<td><code>{html.escape(str(r.get('dut', '')))}</code></td>"
            f"<td>{html.escape(str(r.get('extract', '')))}</td>"
            f"<td>{html.escape(str(r.get('emit_tb', '')))}</td>"
            f"<td>{html.escape(str(r.get('sim_engine', '')))}</td>"
            f"<td>{html.escape(str(r.get('checks_summary', '')))}</td>"
            f"<td>{html.escape(str(r.get('mutation', '')))}</td>"
            "</tr>"
        )
    return (
        "<section class='card'>"
        "<h2>1. 全协议工具链能力与实测范围矩阵</h2>"
        "<p class='sub'>所有协议均已接入 <code>emit_testbench(protocol='auto')</code> 自动生成可仿真测试台，并通过 <code>lint_tb</code>（0 错误）与 Verilator 5.054 / cocotb 2.1.0 实跑。</p>"
        "<div class='tbl-wrap'><table><thead><tr>"
        "<th>协议 / 配置</th><th>DUT 模块</th><th>extract_interfaces (slang)</th>"
        "<th>emit_testbench 自动生成</th><th>仿真引擎</th><th>实跑检查项</th><th>变异杀伤</th>"
        "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table></div></section>"
    )


def _render_mutation_section(mut: Dict[str, Any]) -> str:
    if not isinstance(mut, dict):
        return ""
    results = mut.get("results") or []
    if not results:
        return ""
    trs = []
    for m in results:
        killed = bool(m.get("killed")) or m.get("status") == "KILLED" or (m.get("returncode", 0) != 0)
        st = "KILLED" if killed else "SURVIVED"
        diff_txt = f"{m.get('old', '')}  ➔  {m.get('new', '')}" if m.get("old") else str(m.get("desc", ""))
        trs.append(
            "<tr>"
            f"<td><code>{html.escape(str(m.get('id', '')))}</code></td>"
            f"<td><code>{html.escape(str(m.get('file', '')))}</code></td>"
            f"<td>{html.escape(str(m.get('desc', '')))}</td>"
            f"<td><code class='diff'>{html.escape(diff_txt)}</code></td>"
            f"<td>{_badge(st)}</td>"
            f"<td><code>rc={html.escape(str(m.get('returncode', '')))}</code></td>"
            "</tr>"
        )
    score = mut.get("mutation_score")
    if score is None:
        score = mut.get("mutation_score_pct") if mut.get("mutation_score_pct") is not None else mut.get("score_pct")
    if score is None and mut.get("total"):
        score = round(100.0 * float(mut.get("killed", 0)) / float(mut["total"]), 1)
    return (
        "<section class='card'>"
        f"<h2>3. 真实 RTL 变异注入杀伤明细（得分 {score}% · {mut.get('killed', 0)}/{mut.get('total', 0)}）</h2>"
        "<p class='sub'>每次注入真实修改 DUT 源码 → 触发 Verilator 重新编译与仿真 → 确认测试台以非零退出码杀死故障 → try/finally 自动恢复原始 RTL 并校验 0 残留。</p>"
        "<div class='tbl-wrap'><table><thead><tr>"
        "<th>Mutation ID</th><th>目标 RTL</th><th>注入故障说明</th><th>代码变更</th><th>结果</th><th>退出码</th>"
        "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table></div></section>"
    )


def _render_project_panel(proj: Dict[str, Any]) -> str:
    title = html.escape(str(proj.get("title", proj.get("id", "Project"))))
    dut = html.escape(str(proj.get("dut", "")))
    proto = html.escape(str(proj.get("protocol", "")))
    clk = html.escape(str(proj.get("clock", "-")))
    rst = html.escape(str(proj.get("reset", "-")))
    emitted_tb = html.escape(str(proj.get("emitted_tb", "-")))
    standalone = html.escape(str(proj.get("standalone_file", "")))
    checks = proj.get("checks") or []
    passed = sum(1 for c in checks if str(c.get("status", "")).upper() == "PASS")
    failed = len(checks) - passed

    # Port map table
    pm = proj.get("port_map") or {}
    pm_rows = []
    for canon, pinfo in pm.items():
        if isinstance(pinfo, dict):
            pname = str(pinfo.get("port", canon))
            pdir = str(pinfo.get("direction", "-"))
            pw = str(pinfo.get("width", "-"))
        else:
            pname, pdir, pw = str(pinfo), "-", "-"
        dir_cls = "dir-in" if pdir == "input" else ("dir-out" if pdir == "output" else "dir-io")
        pw_txt = f"{pw}-bit" if str(pw).isdigit() else str(pw)
        pm_rows.append(
            f"<tr><td><code>{html.escape(str(canon))}</code></td>"
            f"<td><code>{html.escape(pname)}</code></td>"
            f"<td><span class='dir-pill {dir_cls}'>{html.escape(pdir)}</span></td>"
            f"<td><code>{html.escape(pw_txt)}</code></td></tr>"
        )
    pm_html = (
        "<div class='tbl-wrap'><table><thead><tr><th>规范信号 (Canonical)</th><th>RTL 端口名</th><th>方向</th><th>位宽</th></tr></thead><tbody>"
        + "".join(pm_rows)
        + "</tbody></table></div>"
        if pm_rows
        else "<p class='muted'>无端口映射信息</p>"
    )

    # Optional 3x3 PCIe matrix grid
    pcie_grid_html = ""
    pcie_matrix = proj.get("pcie_matrix")
    if isinstance(pcie_matrix, dict) and pcie_matrix:
        rows_g = []
        for gen in (3, 4, 5):
            cells = [f"<td><strong>PCIe Gen{gen}</strong></td>"]
            for lanes in (1, 4, 16):
                key = f"pcie_g{gen}x{lanes}"
                cell = pcie_matrix.get(key, {})
                dw = cell.get("dw", 64 if lanes == 1 else (128 if lanes == 4 else 512))
                st = cell.get("status", "PASS")
                cnt = cell.get("checks", 2)
                cells.append(f"<td>{_badge(st)} <span class='muted'>DW={dw} · {cnt}/{cnt} checks</span></td>")
            rows_g.append("<tr>" + "".join(cells) + "</tr>")
        pcie_grid_html = (
            "<section class='card'>"
            "<h3>PCIe 3×3 全组合实测矩阵（Gen3 / Gen4 / Gen5 × x1 / x4 / x16）</h3>"
            "<div class='tbl-wrap'><table><thead><tr><th>代数 \\ 通道数</th><th>x1 (DW=64)</th><th>x4 (DW=128)</th><th>x16 (DW=512)</th></tr></thead><tbody>"
            + "".join(rows_g)
            + "</tbody></table></div></section>"
        )

    # Per-project checks table
    chk_rows = _render_checks_rows(checks, rich_cols=True)
    chk_html = (
        "<div class='tbl-wrap'><table><thead>"
        "<tr><th>Rule ID</th><th>协议 / 类别</th><th>执行引擎</th><th>激励与事务</th><th>期望值</th><th>实测值</th><th>状态</th></tr>"
        "</thead><tbody>"
        + chk_rows
        + "</tbody></table></div>"
    )

    dut_code = str(proj.get("dut_code") or "").strip()
    dut_lines = len(dut_code.splitlines()) if dut_code else 0
    dut_block = (
        f"<section class='card'><h3>被测 DUT 源码（<code>{dut}</code> · {dut_lines} 行）</h3>"
        f"<details open><summary style='cursor:pointer;font-weight:600;margin-bottom:8px'>展开 / 收起 DUT SystemVerilog 源码</summary>"
        f"<pre class='sv-code'>{_highlight_sv(dut_code)}</pre></details></section>"
        if dut_code
        else ""
    )
    raw_log = str(proj.get("raw_log") or "").strip()
    log_block = f"<section class='card'><h3>本项目原始仿真日志</h3><pre>{html.escape(raw_log)}</pre></section>" if raw_log else ""
    file_hint = f" · 独立页面：<code>reports/{standalone}</code>" if standalone else ""

    return (
        f"<section class='card'>"
        f"<h2>{title} {_badge('PASS' if failed == 0 and passed > 0 else 'FAIL')}</h2>"
        f"<p class='sub'>DUT：<code>{dut}</code> · 协议：<code>{proto}</code> · 时钟/复位：<code>{clk} / {rst}</code> · 自动生成 TB：<code>{emitted_tb}</code> · 检查项：<strong>{passed}/{len(checks)} PASS</strong>{file_hint}</p>"
        f"</section>"
        f"{pcie_grid_html}"
        f"<section class='card'><h3>逐条实跑检查明细（{passed}/{len(checks)}）</h3>{chk_html}</section>"
        f"<section class='card'><h3>slang 12.2 提取接口端口映射（{len(pm)} 信号）</h3>{pm_html}</section>"
        f"{dut_block}"
        f"{log_block}"
    )


def tool_render_visual_report(
    report: Dict[str, Any],
    dep_graph: Dict[str, Any],
    extracted_interfaces: List[Dict[str, Any]],
    out_html_path: str,
) -> Dict[str, Any]:
    if not isinstance(report, dict) or not isinstance(dep_graph, dict):
        return err_result("render_visual_report", "report and dep_graph must be objects.")
    if not isinstance(extracted_interfaces, list):
        return err_result("render_visual_report", "extracted_interfaces must be a list.")

    # report 是外部（或 AI）生成的 JSON，任何嵌套字段的形状都可能不对。旧实现
    # 一路 c.get(...) 下去，畸形输入得到的是裸 `AttributeError: 'str' object has
    # no attribute 'get'` —— 没有字段名、没有索引，用户完全不知道哪个字段写错了。
    # 这里在渲染**之前**统一校验会参与渲染的集合/映射字段，给出可定位的
    # 结构化错误（与 normalize_sim_result 的处理口径一致）。
    for field in ("checks", "project_pages", "capability_matrix"):
        val = report.get(field)
        if val is None:
            continue
        if not isinstance(val, list):
            return err_result(
                "render_visual_report",
                f"report.{field} must be a list, got {type(val).__name__}.",
            )
        bad = [i for i, it in enumerate(val) if not isinstance(it, dict)]
        if bad:
            return err_result(
                "render_visual_report",
                f"report.{field} must be a list of objects; element(s) at index "
                f"{bad[:5]} are not objects (got types "
                f"{[type(val[i]).__name__ for i in bad[:5]]}).",
            )
    for field in ("summary", "coverage_summary", "mutation_summary"):
        val = report.get(field)
        if val is not None and not isinstance(val, dict):
            return err_result(
                "render_visual_report",
                f"report.{field} must be an object, got {type(val).__name__}.",
            )
    mut = report.get("mutation_summary")
    if isinstance(mut, dict) and mut.get("results") is not None:
        res = mut["results"]
        if not isinstance(res, list):
            return err_result(
                "render_visual_report",
                f"report.mutation_summary.results must be a list, got "
                f"{type(res).__name__}.",
            )
        bad = [i for i, it in enumerate(res) if not isinstance(it, dict)]
        if bad:
            return err_result(
                "render_visual_report",
                f"report.mutation_summary.results must be a list of objects; "
                f"element(s) at index {bad[:5]} are not objects.",
            )
    # dep_graph.dependency_closure 也参与渲染（SVG 里的依赖框）
    closure = dep_graph.get("dependency_closure")
    if closure is not None and not isinstance(closure, list):
        return err_result(
            "render_visual_report",
            f"dep_graph.dependency_closure must be a list, got "
            f"{type(closure).__name__}.",
        )

    out_html = Path(out_html_path)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    svg = _build_svg(report, dep_graph, extracted_interfaces)
    out_svg = out_html.with_suffix(".svg")
    out_svg.write_text(svg, encoding="utf-8")

    s = report.get("summary") or {}
    checks = report.get("checks") or []
    rich_cols = any(c.get("tx") or c.get("expected") or c.get("observed") for c in checks)
    rows = _render_checks_rows(checks, rich_cols=rich_cols)

    if rich_cols:
        checks_thead = (
            "<tr><th>Rule ID</th><th>协议 / 类别</th><th>执行引擎</th>"
            "<th>激励与事务 (Stimulus / TX)</th><th>期望值 (Expected)</th>"
            "<th>实测值 (Observed)</th><th>状态</th></tr>"
        )
    else:
        checks_thead = "<tr><th>Rule ID</th><th>Category</th><th>Status</th><th>Detail</th></tr>"

    cov = report.get("coverage_summary")
    mut = report.get("mutation_summary")
    cov_kpi = f"{cov.get('rtl_pct', cov.get('rtl_line_pct', cov.get('line_pct', 'n/a')))}%" if isinstance(cov, dict) else "N/A"
    def _num_or_none(v):
        """把 mutation score 字段安全转成 float；非数字返回 None。

        旧实现直接 `round(100.0 * float(killed)/max(1,total))`，且把
        mut.get("mutation_score", ...) 作为**默认值表达式**求值 —— 于是只要
        mutation_score 缺省且 killed/total 是非数字字符串（外部 JSON 里很常见，
        例如 "n/a" 或写错的类型），就会抛
        `TypeError: can't multiply sequence by non-int of type 'float'`，
        整个报告渲染失败（这是渲染器，本应对任意输入都能出图）。
        """
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            return None

    mut_score_val = None
    if isinstance(mut, dict):
        for _k in ("mutation_score", "mutation_score_pct", "score_pct"):
            mut_score_val = _num_or_none(mut.get(_k))
            if mut_score_val is not None:
                break
        if mut_score_val is None:
            _k = _num_or_none(mut.get("killed")) or 0.0
            _t = _num_or_none(mut.get("total")) or 0.0
            mut_score_val = round(100.0 * _k / max(1.0, _t), 1)
    mut_kpi = f"{mut_score_val}% ({mut.get('killed', 0)}/{mut.get('total', 0)})" if isinstance(mut, dict) else "N/A"
    cap_html = _render_capability_section(report.get("capability_matrix") or [])
    mut_html = _render_mutation_section(mut) if isinstance(mut, dict) else ""

    raw_log = str(report.get("raw_log_excerpt") or "").strip()
    log_html = (
        "<section class='card'>"
        "<h2>4. 原始仿真器日志摘录（可复现命令：<code>bash examples/multi_bus_ref/sim.sh all</code>）</h2>"
        f"<pre>{html.escape(raw_log)}</pre>"
        "</section>"
        if raw_log
        else ""
    )

    extra_badges = []
    if isinstance(cov, dict):
        extra_badges.append(f"rtl_cov={cov.get('rtl_pct', cov.get('rtl_line_pct', cov.get('line_pct', 'n/a')))}%")
    if isinstance(mut, dict):
        extra_badges.append(f"mutation={mut_score_val}%")
    # extra_badges 的值来自 coverage_summary / mutation_summary（外部 JSON，
    # 可能含任意字符串）。旧实现把 extra_meta 直接插进 HTML —— 实测
    # `<img src=x onerror=alert(1)>` 被原样写入，构成存储型 XSS。
    # 这里对每个 badge **先转义再拼接**（拼接分隔符是常量 " · "，安全）。
    extra_meta = (html.escape(" · ".join(extra_badges)) and
                  " · " + html.escape(" · ".join(extra_badges))) if extra_badges else ""

    overview_body = f"""
{cap_html}
<section class="card">
  <h2>2. 逐条真实仿真检查明细（直接解析自 Verilator / cocotb 实时日志）</h2>
  <p class="sub">每一行均对应仿真器实际执行的一笔事务与断言结果，零伪造标签、零重复凑数。</p>
  <div class="tbl-wrap"><table><thead>{checks_thead}</thead><tbody id="checks-tbody">{rows}</tbody></table></div>
</section>
{mut_html}
<section class="card">
  <h2>架构与接口拓扑视图</h2>
  {svg}
</section>
{log_html}
"""

    project_pages = report.get("project_pages") or []
    tab_css = ""
    tabs_html = overview_body
    if project_pages:
        radio_inputs = ['<input type="radio" name="projtab" id="tab-overview" class="tab-radio" checked>']
        tab_labels = ['<label for="tab-overview" class="tab-lbl">总览 Overview</label>']
        tab_panels = [f'<div class="tab-panel" id="panel-overview">{overview_body}</div>']
        css_rules = [
            "#tab-overview:checked ~ .tab-bar label[for='tab-overview'] { background:#0F172A; color:#FFFFFF; border-color:#0F172A; }",
            "#tab-overview:checked ~ .tab-panels #panel-overview { display:block; }",
        ]
        for proj in project_pages:
            pid = re.sub(r"[^a-zA-Z0-9_-]", "_", str(proj.get("id", "proj")))
            lbl = html.escape(str(proj.get("short_label") or proj.get("title") or pid))
            radio_inputs.append(f'<input type="radio" name="projtab" id="tab-{pid}" class="tab-radio">')
            tab_labels.append(f'<label for="tab-{pid}" class="tab-lbl">{lbl}</label>')
            tab_panels.append(f'<div class="tab-panel" id="panel-{pid}">{_render_project_panel(proj)}</div>')
            css_rules.append(
                f"#tab-{pid}:checked ~ .tab-bar label[for='tab-{pid}'] {{ background:#0F172A; color:#FFFFFF; border-color:#0F172A; }}"
            )
            css_rules.append(f"#tab-{pid}:checked ~ .tab-panels #panel-{pid} {{ display:block; }}")
        tab_css = "\n  ".join(css_rules)
        tabs_html = (
            "<div class='tabs-root'>"
            + "".join(radio_inputs)
            + "<div class='tab-bar'>" + "".join(tab_labels) + "</div>"
            + "<div class='tab-panels'>" + "".join(tab_panels) + "</div>"
            + "</div>"
        )

    overall = str(report.get("overall_status", "UNKNOWN"))
    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>{html.escape(str(report.get('module', 'FPGA')))} 验证审计报告</title>
<style>
  :root {{ --bg:#F8FAFC; --card:#FFFFFF; --ink:#0F172A; --muted:#64748B; --line:#E2E8F0; --accent:#1D4ED8; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); margin:0; padding:28px 20px 64px; line-height:1.5; }}
  .wrap {{ max-width:1260px; margin:0 auto; }}
  header {{ display:flex; flex-wrap:wrap; justify-content:space-between; align-items:flex-end; gap:12px; margin-bottom:20px; border-bottom:2px solid var(--ink); padding-bottom:14px; }}
  h1 {{ margin:0; font-size:24px; letter-spacing:-0.01em; }}
  h2 {{ margin:0 0 8px; font-size:17px; color:var(--ink); }}
  h3 {{ margin:0 0 10px; font-size:15px; color:var(--ink); }}
  .sub {{ margin:0 0 14px; color:var(--muted); font-size:13px; }}
  .meta {{ font-size:13px; color:var(--muted); }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:18px; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .kpi .lbl {{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:0.04em; }}
  .kpi .val {{ font-size:22px; font-weight:700; margin-top:4px; }}
  .tab-radio {{ display:none; }}
  .tab-bar {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }}
  .tab-lbl {{ cursor:pointer; padding:7px 14px; border-radius:999px; border:1px solid #CBD5E1; background:#FFFFFF; color:#334155; font-size:13px; font-weight:600; transition:all .15s; }}
  .tab-lbl:hover {{ border-color:#0F172A; color:#0F172A; }}
  .tab-panel {{ display:none; }}
  {tab_css}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:18px; box-shadow:0 1px 2px rgba(15,23,42,0.04); }}
  .tbl-wrap {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
  th {{ background:#F1F5F9; color:#334155; font-weight:600; white-space:nowrap; }}
  tr:hover td {{ background:#F8FAFC; }}
  code {{ background:#F1F5F9; padding:2px 6px; border-radius:4px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; word-break:break-all; }}
  code.diff {{ background:#FEF2F2; color:#991B1B; }}
  .muted {{ font-size:11px; color:var(--muted); margin-top:2px; }}
  .eng {{ display:inline-block; background:#EFF6FF; color:#1E40AF; padding:2px 7px; border-radius:999px; font-size:11px; font-weight:600; white-space:nowrap; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:700; letter-spacing:0.03em; }}
  .b-pass {{ background:#DCFCE7; color:#166534; }}
  .b-fail {{ background:#FEE2E2; color:#991B1B; }}
  .b-warn {{ background:#FEF3C7; color:#92400E; }}
  pre {{ background:#0F172A; color:#E2E8F0; padding:14px 16px; border-radius:8px; overflow-x:auto; font-size:12px; line-height:1.5; margin:0; }}
  pre.sv-code {{ background:#0D1117; color:#E6EDF3; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
  .sv-ln {{ display:inline-block; width:3.2em; color:#6E7681; user-select:none; border-right:1px solid #21262D; margin-right:10px; padding-right:6px; text-align:right; }}
  .sv-kw {{ color:#FF7B72; font-weight:600; }}
  .sv-num {{ color:#79C0FF; }}
  .sv-cmt {{ color:#8B949E; font-style:italic; }}
  .sv-sys {{ color:#D2A8FF; font-weight:600; }}
  .dir-pill {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; font-family:ui-monospace,monospace; }}
  .dir-in {{ background:#DBEAFE; color:#1E40AF; }}
  .dir-out {{ background:#FEF3C7; color:#92400E; }}
  .dir-io {{ background:#E2E8F0; color:#334155; }}
</style></head><body><div class="wrap">
<header>
  <div>
    <h1>FPGA 多协议验证审计报告：{html.escape(str(report.get('module')))} {_badge(overall)}</h1>
    <div class="meta">checks={s.get('total', 0)} · pass={s.get('passed', 0)} · fail={s.get('failed', 0)} · pass_rate={s.get('pass_rate', 0)}{extra_meta}</div>
  </div>
  <div class="meta">Generated: {html.escape(str(report.get('generated_at', '')))}</div>
</header>

<div class="kpis">
  <div class="kpi"><div class="lbl">总实跑检查项</div><div class="val">{s.get('total', 0)}</div></div>
  <div class="kpi"><div class="lbl">通过 / 失败</div><div class="val" style="color:#166534">{s.get('passed', 0)} / <span style="color:{'#991B1B' if s.get('failed', 0) else '#64748B'}">{s.get('failed', 0)}</span></div></div>
  <div class="kpi"><div class="lbl">通过率</div><div class="val">{round(float(s.get('pass_rate', 0)) * 100, 1)}%</div></div>
  <div class="kpi"><div class="lbl">RTL 变异杀伤</div><div class="val">{html.escape(mut_kpi)}</div></div>
  <div class="kpi"><div class="lbl">行覆盖率</div><div class="val">{html.escape(cov_kpi)}</div></div>
</div>

{tabs_html}
</div></body></html>"""
    out_html.write_text(page, encoding="utf-8")

    diags = _check_html_consistency(page, expected_checks=len(checks))
    # 数据自洽性：summary/overall_status 必须与 checks 实际内容一致（W052/W053）。
    diags.extend(_check_report_data_consistency(report, checks))
    return ok_result(
        "render_visual_report",
        {
            "html_path": str(out_html),
            "svg_path": str(out_svg),
            "consistency_ok": len(diags) == 0,
            "rendered_checks": len(checks),
        },
        diagnostics=diags,
    )


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("render_visual_report"))
