"""CLI / registry / 可视化 / 网表清洗 / 门禁自身

CLI / registry / 可视化 / 网表清洗 / 门禁自身

从 tests/test_full_coverage.py 按领域拆分而来（原文件 ~5500 行、86 个用例，
定位失败需要通读全文）。拆分只搬移代码，不改断言。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from fpga_toolbox.cli import _parse_json_or_file, run_single_tool  # noqa: E402
from fpga_toolbox.cli import main as cli_main  # noqa: E402
from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench  # noqa: E402
from fpga_toolbox.registry import call_tool  # noqa: E402
from fpga_toolbox.visual_renderer import tool_render_visual_report  # noqa: E402


def test_cli_in_process_all_paths() -> None:
    print("coverage: cli.py in-process (_parse_json_or_file, _add_schema_args, _extract_params, run_single_tool, main)")
    # _parse_json_or_file: inline JSON + @file UTF-8 + @file non-UTF-8
    assert _parse_json_or_file('{"a": 1}') == {"a": 1}
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        good_json = td_p / "good.json"
        good_json.write_text('{"k": [1, 2]}', encoding="utf-8")
        assert _parse_json_or_file(f"@{good_json}") == {"k": [1, 2]}

        bad_utf8 = td_p / "bad.json"
        bad_utf8.write_bytes(b"\x80\x81\x82")
        try:
            _parse_json_or_file(f"@{bad_utf8}")
            raise AssertionError("Expected ValueError on non-UTF-8 @file")
        except ValueError as exc:
            assert "UTF-8" in str(exc)

    # main: list, schemas, index_repo (success), missing file (failure exit 1)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_list = cli_main(["list"])
    assert rc_list == 0 and "clean_pt_netlist" in buf.getvalue()

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_schemas = cli_main(["schemas"])
    assert rc_schemas == 0 and len(json.loads(buf.getvalue())) >= 13

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_idx = cli_main(["index_repo", "--project-root", str(ROOT / "examples" / "multi_bus_ref")])
    assert rc_idx == 0 and json.loads(buf.getvalue())["ok"] is True

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_fail = cli_main(["extract_interfaces", "--rtl-file", str(ROOT / "does_not_exist_12345.sv")])
    assert rc_fail == 1 and json.loads(buf.getvalue())["ok"] is False

    # run_single_tool in-process
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_single = run_single_tool("index_repo", ["--project-root", str(ROOT / "examples" / "multi_bus_ref")])
    assert rc_single == 0 and json.loads(buf.getvalue())["ok"] is True

def test_cli_atfile_errors_structured() -> None:
    """Defect 101: @file 指向不存在的路径让 FileNotFoundError 冒泡成堆栈。

    main() 只捕 ValueError，而 read_text 抛的是 FileNotFoundError（OSError
    子类），不是 ValueError —— 于是「路径打错 / 文件被删」这类用户输入错误
    直接变成 Python 堆栈，而不是与工具信封同构的结构化错误。
    """
    print("coverage: CLI @file missing-path / bad-JSON -> structured error")
    import contextlib
    import io

    from fpga_toolbox.cli import main

    missing = "/nonexistent/dir/does_not_exist.json"

    def _run(argv):
        # main() 会把结构化 JSON 打到 stdout；吞掉输出，只看返回码
        with contextlib.redirect_stdout(io.StringIO()):
            return main(argv)

    # object 参数 @missing（normalize_sim_result 只需 --module-name）
    assert _run(["normalize_sim_result", "--module-name", "m",
                 "--coverage-summary", f"@{missing}"]) == 1
    # 行内非法 JSON 同样结构化
    assert _run(["normalize_sim_result", "--module-name", "m",
                 "--coverage-summary", "{not valid json"]) == 1

    # array 参数单个 @missing（提供真实必填文件，避免 argparse 先 SystemExit）
    with tempfile.TemporaryDirectory() as td:
        inv = Path(td) / "in.v"
        inv.write_text("module m(output q); endmodule\n", encoding="utf-8")
        rc = _run(["clean_pt_netlist", "--in-verilog", str(inv),
                   "--out-verilog", str(Path(td) / "o.v"),
                   "--blackbox-modules", f"@{missing}"])
        assert rc == 1, rc

def test_tool_schema_descriptions_match_signature_defaults() -> None:
    """Defect 157/158: schema 描述必须与函数签名默认值一致。

    157: copy_sandbox 的 `force` 描述写成「Allow cleaning a directory that
         carries the sandbox marker」——实际行为是「清理**没有**标记的目录
        也需要 force」，方向完全反了。用户照描述理解会以为 force 只影响
         已标记沙箱，而它可以清空用户自己的未标记目录。
    158: copy_sandbox 的 `clean` 描述写「default false」，而函数签名是
         `clean: bool = True`：不传该参数时**会清空**已标记沙箱里的旧文件。
         这直接误导用户。

    这里做通用审计：凡描述里写了 `default <bool/int>` 的参数，都必须与签名
    默认值一致，防止同类偏差再次出现。
    """
    print("coverage: schema descriptions match signature defaults")
    import inspect

    from fpga_toolbox.registry import TOOL_REGISTRY

    mismatches = []
    for tool, entry in TOOL_REGISTRY.items():
        fn = entry["fn"]
        props = entry["inputSchema"]["properties"]
        sig = inspect.signature(fn)
        for pname, spec in props.items():
            desc = spec.get("description", "") or ""
            m = re.search(r"default\s+(true|false)", desc, re.IGNORECASE)
            if m and pname in sig.parameters:
                claimed = m.group(1).lower() == "true"
                actual = sig.parameters[pname].default
                if isinstance(actual, bool) and actual != claimed:
                    mismatches.append((tool, pname, "bool", claimed, actual))
            m2 = re.search(r"default\s+([0-9]+(?:\.[0-9]+)?)", desc)
            if m2 and pname in sig.parameters:
                claimed_n = float(m2.group(1))
                actual_n = sig.parameters[pname].default
                if isinstance(actual_n, (int, float)) and not isinstance(actual_n, bool):
                    if abs(float(actual_n) - claimed_n) > 1e-9:
                        mismatches.append((tool, pname, "num", claimed_n, actual_n))

    assert not mismatches, f"schema description/signature default mismatches: {mismatches}"

    # copy_sandbox 的两个具体参数（缺陷 157/158）必须描述正确
    from fpga_toolbox.registry import list_tool_schemas
    cs = next(x for x in list_tool_schemas() if x["name"] == "copy_sandbox")
    props = cs["inputSchema"]["properties"]
    force_desc = props["force"]["description"]
    assert "DESTRUCTIVE" in force_desc, force_desc
    assert "NO sandbox marker" in force_desc, force_desc
    clean_desc = props["clean"]["description"]
    assert "default TRUE" in clean_desc, clean_desc

def test_visual_renderer_rich_project_pages_and_highlight() -> None:
    print("coverage: visual_renderer (_highlight_sv, _render_project_panel, pcie_matrix, capability_matrix)")
    with tempfile.TemporaryDirectory() as td:
        out_html = Path(td) / "rich_report.html"
        report = {
            "module": "omni_test",
            "overall_status": "PASS",
            "generated_at": "2026-11-24T00:00:00Z",
            "summary": {"total": 2, "passed": 2, "failed": 0, "skipped": 0, "pass_rate": 1.0},
            "checks": [
                {
                    "rule_id": "CHK_001",
                    "protocol": "PCIe-Gen4-x4",
                    "category": "tlp_rw",
                    "engine": "Verilator 5.054",
                    "tx": "MWr+MRd 0x10",
                    "expected": "0x55AA1234",
                    "observed": "0x55AA1234",
                    "status": "PASS",
                },
                {
                    "rule_id": "CHK_002",
                    "protocol": "SPI",
                    "category": "mode0",
                    "engine": "Verilator 5.054",
                    "tx": "SPI WR 0x00=0xA5",
                    "expected": "0xA5",
                    "observed": "0xA5",
                    "status": "PASS",
                },
            ],
            "capability_matrix": [
                {
                    "protocol": "PCIe 3x3",
                    "dut": "pcie_tlp_ep.sv",
                    "extract": "PASS",
                    "emit_tb": "SUPPORTED",
                    "sim_engine": "Verilator 5.054",
                    "checks_summary": "18/18 PASS",
                    "mutation": "KILLED",
                }
            ],
            "mutation_summary": {
                "killed": 1,
                "total": 1,
                "mutation_score": 100.0,
                "results": [
                    {"id": "M01", "file": "dut.sv", "old": "a <= b;", "new": "a <= 0;", "desc": "zero", "killed": True, "returncode": 1}
                ],
            },
            "raw_log_excerpt": "PASS: omni_test\n",
            "project_pages": [
                {
                    "id": "pcie_3x3",
                    "title": "PCIe 3x3 Project",
                    "short_label": "PCIe 3x3",
                    "protocol": "pcie",
                    "dut": "pcie_tlp_ep.sv",
                    "clock": "clk",
                    "reset": "rst_n",
                    "emitted_tb": "tb_pcie.sv",
                    "standalone_file": "pcie_3x3.html",
                    "dut_code": "module pcie_tlp_ep(input logic clk);\n  // comment\n  localparam int N = 32'hFF;\n  initial $display(\"ok\");\nendmodule\n",
                    "port_map": {
                        "clk": {"port": "clk", "direction": "input", "width": 1},
                        "tx_data": {"port": "pcie_tx_tdata", "direction": "output", "width": 128},
                    },
                    "pcie_matrix": {
                        f"pcie_g{g}x{lanes}": {"dw": 64 if lanes == 1 else (128 if lanes == 4 else 512), "status": "PASS", "checks": 2}
                        for g in (3, 4, 5)
                        for lanes in (1, 4, 16)
                    },
                    "checks": [
                        {"rule_id": "CHK_001", "protocol": "PCIe", "category": "tlp", "engine": "Verilator", "tx": "MWr", "expected": "OK", "observed": "OK", "status": "PASS"}
                    ],
                    "raw_log": "PASS: pcie\n",
                }
            ],
        }
        res = tool_render_visual_report(
            report=report,
            dep_graph={"dependency_closure": ["omni_test", "sub_a"]},
            extracted_interfaces=[{"protocol": "pcie", "port_map": {"tx_data": "pcie_tx_tdata"}}],
            out_html_path=str(out_html),
        )
        assert res["ok"] and res["data"]["consistency_ok"] is True
        html_txt = out_html.read_text(encoding="utf-8")
        assert 'class="sv-kw"' in html_txt and 'class="sv-cmt"' in html_txt and 'class="sv-sys"' in html_txt
        assert "dir-in" in html_txt and "dir-out" in html_txt and "128-bit" in html_txt
        assert "PCIe Gen5" in html_txt and "DW=512" in html_txt

def test_visual_renderer_consistency_and_shape_validation() -> None:
    """Defect 120: 审计报告自检只看 HTML 标签，不看数据是否自相矛盾。

    `render_visual_report` 的职责是"生成一份审计报告"，它自带
    `_check_html_consistency` 自检 —— 但那个自检只查 `<table>` 标签配平和
    渲染行数（W050/W051），**完全不看 report 数据本身**。于是下面四种自相矛盾
    的报告全部 ok=True、consistency_ok=True、零诊断，被原样印进 HTML：

      * summary.passed=2 而 checks 里全 FAIL
      * summary.total 与实际条数不符
      * overall_status=PASS 但没有任何检查通过
      * overall_status=FAIL 但所有检查都通过

    读者面对"通过率 100%"和逐条 FAIL 行，无从判断该信哪个。

    另一处：report 是外部/AI 生成的 JSON，嵌套字段形状任意。旧实现一路
    `c.get(...)`，畸形输入得到裸 `AttributeError: 'str' object has no attribute
    'get'` —— 没有字段名、没有索引。
    """
    print("coverage: render_visual_report data consistency (W052/W053) + shape validation")
    from fpga_toolbox.visual_renderer import tool_render_visual_report

    def _render(report: dict) -> dict:
        with tempfile.TemporaryDirectory() as td:
            return tool_render_visual_report(report, {}, [], str(Path(td) / "r.html"))

    # ---- 数据一致性：四种矛盾都必须给出诊断 ----
    cases = {
        "summary over-reports passes": {
            "summary": {"total": 2, "passed": 2, "failed": 0},
            "checks": [{"rule_id": "A", "status": "FAIL"}]},
        "summary.total wrong": {
            "summary": {"total": 5, "passed": 1, "failed": 0},
            "checks": [{"rule_id": "A", "status": "PASS"}]},
        "PASS headline, all rows FAIL": {
            "overall_status": "PASS",
            "checks": [{"rule_id": "A", "status": "FAIL"}]},
        "FAIL headline, all rows PASS": {
            "overall_status": "FAIL",
            "checks": [{"rule_id": "A", "status": "PASS"}]},
    }
    for label, extra in cases.items():
        r = _render({"module": "m", **extra})
        codes = {d["code"] for d in r.get("diagnostics") or []}
        assert r["ok"], (label, r.get("error"))
        assert codes & {"W052", "W053"}, (label, codes)
        assert r["data"]["consistency_ok"] is False, (label, r["data"])

    # 自洽的报告不能误报
    ok = _render({"module": "m", "overall_status": "PASS",
                  "summary": {"total": 1, "passed": 1, "failed": 0},
                  "checks": [{"rule_id": "A", "status": "PASS"}]})
    assert ok["data"]["consistency_ok"] is True, [d["code"] for d in ok["diagnostics"]]

    # ---- 形状校验：畸形字段给出可定位错误，而不是裸 AttributeError ----
    malformed = {
        "report.checks": {"module": "m", "checks": ["oops"]},
        "report.checks (None)": {"module": "m", "checks": [None]},
        "report.project_pages": {"module": "m", "checks": [], "project_pages": ["x"]},
        "report.capability_matrix": {"module": "m", "checks": [], "capability_matrix": ["x"]},
        "report.summary": {"module": "m", "checks": [], "summary": "x"},
        "report.mutation_summary.results": {
            "module": "m", "checks": [], "mutation_summary": {"results": ["x"]}},
    }
    for label, rep in malformed.items():
        r = _render(rep)
        assert r["ok"] is False, (label, r.get("data"))
        err = str(r["error"])
        assert "attribute 'get'" not in err, (label, err[:160])
        # 错误必须点名是哪个字段
        assert label.split(" ")[0] in err, (label, err[:160])

def test_verify_invariants_layer3_tolerates_missing_ruff() -> None:
    """verify_invariants Layer 3 在无 ruff 环境必须降级，而不是崩溃。

    回归 91：`scripts/verify_invariants.py` 的 Layer 3 直接
    `subprocess.run(["ruff", ...])`，无 ruff 时抛未捕获的 FileNotFoundError
    带完整堆栈，整个门禁崩溃 —— 而本地开发者/裸 CI 常无 ruff。修复后应降级为
    "compileall-only" 并返回 ok=True。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "vi", Path(__file__).resolve().parents[1] / "scripts" / "verify_invariants.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    fn = getattr(mod, "_check_layer3_static_hygiene", None)
    assert fn is not None, "_check_layer3_static_hygiene missing"

    # 用 PATH 里没有 ruff 的干净环境跑（把 PATH 置空再补最小集）
    saved = os.environ.get("PATH")
    try:
        os.environ["PATH"] = "/usr/bin:/bin"  # 无 ruff
        ok, detail = fn()
    finally:
        if saved is not None:
            os.environ["PATH"] = saved

    assert ok is True, f"Layer 3 must pass (degrade to compileall) without ruff: {detail}"
    assert "ruff not installed" in detail or "compileall" in detail, detail

def test_install_git_hook_uses_git_toplevel() -> None:
    """install_git_hook.sh 必须用 git rev-parse 定位仓库根，而非脚本自身位置。

    回归 92：旧实现 REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    假设脚本总在 <repo>/scripts/ 下。用户把脚本复制到别处（仓库根、/tmp）运行时，
    REPO_ROOT 指向错误位置，误报 "Not a git repository" —— 即使 cwd 在真实仓库里。
    修复后用 git rev-parse --show-toplevel 基于【当前工作目录】定位。
    """

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "install_git_hook.sh"
    assert script.is_file(), str(script)

    # 必须用 git rev-parse 定位仓库根（而非仅依赖脚本自身位置）
    body = script.read_text(encoding="utf-8")
    assert "git rev-parse --show-toplevel" in body, "should locate repo via git rev-parse"

    # Windows runner 的 PATH 上可能只有 WSL 的 bash 桩（`bash` 存在但一跑就报
    # "Windows Subsystem for Linux has no installed distributions"），因此不能只看
    # shutil.which，必须实跑一次确认可用，否则跳过 shell 语法检查与端到端。
    bash = shutil.which("bash")
    if bash:
        try:
            if subprocess.run([bash, "-c", "exit 0"], capture_output=True, timeout=30).returncode != 0:
                bash = None
        except Exception:
            bash = None
    if not bash:
        print("coverage: install_git_hook.sh 静态断言通过；无可用的 bash，跳过 bash -n 与端到端")
        return

    # 语法检查
    assert subprocess.run([bash, "-n", str(script)]).returncode == 0, "bash -n failed"

    # 端到端：在临时 git 仓库的【子目录】运行，应成功装上 hook
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=str(wd), check=True)
        sub = wd / "deep" / "nested"
        sub.mkdir(parents=True, exist_ok=True)
        # 把脚本复制到子目录运行（模拟用户从别处执行）
        (sub / "install_git_hook.sh").write_bytes(script.read_bytes())
        r = subprocess.run([bash, "install_git_hook.sh"], cwd=str(sub),
                           capture_output=True, text=True)
        assert r.returncode == 0, f"rc={r.returncode} {r.stderr[:200]}"
        hook = wd / ".git" / "hooks" / "pre-commit"
        assert hook.is_file(), "pre-commit hook not installed"
        assert "verify_invariants.py --fast" in hook.read_text(encoding="utf-8")

    # 非 git 目录 → 退出码 1 + 可读提示
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([bash, str(script)], cwd=td, capture_output=True, text=True)
        assert r.returncode == 1, f"rc={r.returncode}"
        assert "Not a git repository" in r.stderr, r.stderr[:200]

def test_bug_hunt_regressions() -> None:
    """Regression tests for 5 real bugs discovered during adversarial testing."""
    print("coverage: bug-hunt regressions (multi-group QoR, escaped PT inst, active-high rst, SV string //)")
    from fpga_toolbox.eda_campaign import clean_netlist_for_pt, parse_dc_pt_qor

    # Bug 2: multi-group DC/PT QoR with a violating 2nd clock group MUST NOT report timing_clean=True
    qor_multi = """
  Timing Path Group 'pcs_clk'
  Critical Path Slack:              0.00
  Total Negative Slack:             0.00
  No. of Violating Paths:           0
  Timing Path Group 'cfg_clk'
  Critical Path Slack:             -0.25
  Total Negative Slack:            -3.50
  No. of Violating Paths:           14
  Leaf Cell Count:                  1000
"""
    rq = parse_dc_pt_qor(qor_multi)
    assert rq["data"]["wns"] == -0.25
    assert rq["data"]["tns"] == -3.5
    assert rq["data"]["violating_paths"] == 14
    assert rq["data"]["timing_clean"] is False

    # Bug 3: Verilog escaped instance identifier (\u_rd/0) must be removed by clean_netlist_for_pt
    v_esc = "module m(output q);\n  RDBUS \\u_rd/0 (.o_d(q));\nendmodule\n"
    cleaned, stats = clean_netlist_for_pt(v_esc)
    assert stats["removed_instances"] == 1 and "assign q = 1'b0;" in cleaned

    # Bug 4: active-high reset ('reset') must assert 1 then deassert 0 in generated TB
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        rtl = td_p / "apb_hi_rst.sv"
        rtl.write_text(
            "module apb_hi_rst (input logic pclk, input logic reset, input logic psel, "
            "input logic penable, input logic pwrite, input logic [15:0] paddr, "
            "input logic [31:0] pwdata, input logic [3:0] pstrb, output logic pready, "
            "output logic [31:0] prdata, output logic pslverr); "
            "assign pready=1; assign prdata=0; assign pslverr=0; endmodule\n",
            encoding="utf-8",
        )
        em = emit_protocol_testbench(str(rtl), str(td_p / "out"), protocol="apb")
        assert em["ok"]
        tb_txt = Path(em["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        assert ", reset = 1;" in tb_txt and "    reset = 0;" in tb_txt

    # Bug 5: // inside a double-quoted SV string literal must NOT split into sv-cmt
    with tempfile.TemporaryDirectory() as td:
        hp = Path(td) / "str_hl.html"
        rep = {
            "module": "m", "overall_status": "PASS", "generated_at": "2026-11-24",
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "pass_rate": 1.0},
            "checks": [{"rule_id": "C1", "category": "test", "status": "PASS", "detail": "ok"}],
            "project_pages": [{
                "id": "p1", "title": "P1", "short_label": "P1", "protocol": "apb",
                "dut": "a.sv", "clock": "clk", "reset": "rst_n", "emitted_tb": "tb.sv",
                "dut_code": 'module m; initial $display("http://example.com"); // real cmt\nendmodule\n',
                "port_map": {}, "checks": [{"rule_id": "C1", "category": "test", "status": "PASS", "detail": "ok"}],
            }],
        }
        tool_render_visual_report(rep, {"dependency_closure": ["m"]}, [], str(hp))
        ht = hp.read_text(encoding="utf-8")
        assert 'sv-cmt">//example.com' not in ht
        assert 'sv-cmt">// real cmt</span>' in ht

def test_netlist_cleaner_preserves_comments() -> None:
    """Defect 162: PT 网表清理会把注释里提到的黑盒实例也删掉。

    `clean_pt_netlist` 的实例正则直接在原文上跑，没有排除注释区间：

        // BUFX2 u_comment (.A(clk), .Y(n1));   <- 用户注释
        BUFX2 u1 (.A(clk), .Y(n1));            <- 真实实例
        BUFX2 #(.DRIVE(2)) u2 (.A(n1), .Y(n2)); <- 参数化实例

    实测输出把三行（含注释行）全部替换，用户注释内容丢失，且产生
    `// // [fpga_toolbox:pt_clean] ...` 这种叠加注释。
    """
    print("coverage: netlist cleaner preserves user comments")

    netlist = (
        "module top (input clk, output q);\n"
        "  wire n1, n2;\n"
        "  // BUFX2 u_comment (.A(clk), .Y(n1));\n"
        "  BUFX2 u1 (.A(clk), .Y(n1));\n"
        "  BUFX2 #(.DRIVE(2)) u2 (.A(n1), .Y(n2));\n"
        "  DFFX1 u3 (.D(n2), .CK(clk), .Q(q));\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "net.v"
        dst = root / "clean.v"
        src.write_text(netlist, encoding="utf-8")
        r = call_tool("clean_pt_netlist", {
            "in_verilog": str(src), "out_verilog": str(dst),
            "blackbox_modules": ["BUFX2"]})
        assert r["ok"], str(r.get("error"))[:250]
        out = dst.read_text(encoding="utf-8")

        # 用户注释必须保留（原样）
        assert "// BUFX2 u_comment (.A(clk), .Y(n1));" in out, out
        # 两个真实实例被替换 → 恰好两条替换注释
        assert out.count("[fpga_toolbox:pt_clean]") == 2, out
        # 不能出现叠加注释
        assert "// // [fpga_toolbox" not in out, out
        # 非黑盒实例保留
        assert "DFFX1 u3" in out, out
        assert "u1 (" not in out and "u2 (" not in out, out

        # 块注释里提到的实例同样不能删
        net2 = (
            "module top (input clk, output q);\n"
            "  wire n1;\n"
            "  /* multi\n     BUFX2 u_comment (.A(clk), .Y(n1));\n     line */\n"
            "  BUFX2 u1 (.A(clk), .Y(n1));\n"
            "  // mentions endmodule in comment\n"
            "  DFFX1 u2 (.D(n1), .CK(clk), .Q(q));\n"
            "endmodule\n")
        src2 = root / "net2.v"
        dst2 = root / "clean2.v"
        src2.write_text(net2, encoding="utf-8")
        r2 = call_tool("clean_pt_netlist", {
            "in_verilog": str(src2), "out_verilog": str(dst2),
            "blackbox_modules": ["BUFX2"]})
        assert r2["ok"], str(r2.get("error"))[:250]
        out2 = dst2.read_text(encoding="utf-8")
        # 块注释完整保留
        assert "BUFX2 u_comment (.A(clk), .Y(n1));" in out2, out2
        assert "/* multi" in out2 and "line */" in out2, out2
        assert out2.count("[fpga_toolbox:pt_clean]") == 1, out2
        # 注释里的 endmodule 不能让 assign 插错位置：块必须正常结束
        assert out2.rstrip().endswith("endmodule"), out2

def test_netlist_cleaner_ties_off_blackbox_outputs() -> None:
    """Defect 163: 黑盒输出端口未识别时，清理后留下悬空 wire。

    `clean_pt_netlist` 只把以 `o_` 开头的端口当输出，于是
    `RDBUS #(.WIDTH(4)) u1 (.dout(d), .vout(valid))` 的输出网络
    `d` / `valid` 在实例被删后**没有任何 assign**：实测
    `verilator -Wall` 报 4 条 UNDRIVEN，而 PrimeTime 的 link_design 恰恰拒绝
    未驱动网络 —— 工具想解决的问题换了个形式留下。
    """
    print("coverage: netlist cleaner ties off blackbox outputs")

    netlist = (
        "module top (input clk, output [3:0] q, output v);\n"
        "  wire [3:0] d;\n"
        "  wire valid;\n"
        "  RDBUS #(.WIDTH(4)) u1 (.clk(clk), .din(clk), .dout(d), .vout(valid));\n"
        "  assign q = d;\n"
        "  assign v = valid;\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "net.v"
        dst = root / "clean.v"
        src.write_text(netlist, encoding="utf-8")
        r = call_tool("clean_pt_netlist", {
            "in_verilog": str(src), "out_verilog": str(dst),
            "blackbox_modules": ["RDBUS"]})
        assert r["ok"], str(r.get("error"))[:250]
        out = dst.read_text(encoding="utf-8")

        # 实例必须被删
        assert "RDBUS" not in out.replace("[fpga_toolbox:pt_clean]", ""), out
        # 两个输出网络都必须被 tie 到 0（标量形式，PrimeTime 不接受拼接）
        assert "assign d = 1'b0;" in out, out
        assert "assign valid = 1'b0;" in out, out
        assert "assign {d, valid}" not in out, out
        # tie-off 必须在 endmodule 之前
        assert out.index("assign d = 1'b0;") < out.index("endmodule"), out

        # 输入端口（clk/din）不能被 tie —— 它们由外部驱动
        assert "assign clk = 1'b0;" not in out, out


def main() -> int:
    test_cli_in_process_all_paths()
    test_cli_atfile_errors_structured()
    test_tool_schema_descriptions_match_signature_defaults()
    test_visual_renderer_rich_project_pages_and_highlight()
    test_visual_renderer_consistency_and_shape_validation()
    test_verify_invariants_layer3_tolerates_missing_ruff()
    test_install_git_hook_uses_git_toplevel()
    test_bug_hunt_regressions()
    test_netlist_cleaner_preserves_comments()
    test_netlist_cleaner_ties_off_blackbox_outputs()
    print("ALL test_tooling_coverage.py TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
