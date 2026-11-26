"""Comprehensive coverage suite targeting every previously uncovered function and protocol path.

Covers:
  1. fpga_toolbox.cli in-process: _parse_json_or_file, _add_schema_args, _extract_params, run_single_tool, main
  2. fpga_toolbox.core.sv_parser: Verilog-1995 non-ANSI module header parsing (_parse_non_ansi)
  3. fpga_toolbox.core.types: BitFieldIR.width property & dataclass immutability
  4. fpga_toolbox.protocol_tb_emitter: _emit_axi4_tb, _emit_ahb_tb, _emit_ddr4_tb (plus all 7 protocols)
  5. fpga_toolbox.sandbox_copier: _clear_dir on already-marked sandbox with nested files, dirs, and symlinks
  6. fpga_toolbox.sim_runner: _fst_available probe (both real verilator & nonexistent binary)
  7. fpga_toolbox.visual_renderer: _highlight_sv, _render_project_panel, pcie_matrix, capability_matrix, mutation_summary
  8. All 11 protocol classifiers in iface_extractor (axi4_lite, axi4, axi4_stream, apb, ahb_lite, ddr3, ddr4, hbm, pcie, spi, ethernet)
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fpga_toolbox.cli import _parse_json_or_file, run_single_tool  # noqa: E402
from fpga_toolbox.cli import main as cli_main  # noqa: E402
from fpga_toolbox.core.sv_parser import parse_sv_file  # noqa: E402
from fpga_toolbox.core.types import BitFieldIR  # noqa: E402
from fpga_toolbox.iface_extractor import tool_extract_interfaces  # noqa: E402
from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench  # noqa: E402
from fpga_toolbox.registry import call_tool  # noqa: E402
from fpga_toolbox.sandbox_copier import tool_copy_sandbox  # noqa: E402
from fpga_toolbox.sim_runner import _fst_available  # noqa: E402
from fpga_toolbox.tb_linter import tool_lint_tb  # noqa: E402
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


def test_sv_parser_non_ansi_and_bitfield_width() -> None:
    print("coverage: sv_parser._parse_non_ansi & types.BitFieldIR.width")
    legacy_sv = """
module legacy_non_ansi (clk, rst_n, a_in, b_out);
  parameter W = 8;
  localparam H = W - 1;
  input clk;
  input rst_n;
  input wire [H:0] a_in;
  output reg [W-1:0] b_out;
  always @(posedge clk) if (!rst_n) b_out <= 0; else b_out <= a_in;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "legacy_non_ansi.sv"
        lp.write_text(legacy_sv, encoding="utf-8")
        mods = parse_sv_file(lp, backend="builtin")
    assert len(mods) == 1
    m = mods[0]
    assert m.name == "legacy_non_ansi"
    pmap = {p.name: p for p in m.ports}
    assert set(pmap.keys()) == {"clk", "rst_n", "a_in", "b_out"}
    assert pmap["a_in"].direction == "input" and pmap["a_in"].width == 8
    assert pmap["b_out"].direction == "output" and pmap["b_out"].width == 8

    bf = BitFieldIR(name="CTRL", msb=7, lsb=4, access="RW", reset_value=0x5)
    assert bf.width == 4
    bf1 = BitFieldIR(name="EN", msb=0, lsb=0, access="RW")
    assert bf1.width == 1


def test_protocol_tb_emitter_axi4_ahb_ddr4() -> None:
    print("coverage: protocol_tb_emitter (_emit_axi4_tb, _emit_ahb_tb, _emit_ddr4_tb)")
    ref = ROOT / "examples" / "multi_bus_ref"
    with tempfile.TemporaryDirectory() as td:
        for fname, proto in (
            ("axi4_full_slave.sv", "axi4"),
            ("ahb_lite_slave.sv", "ahb_lite"),
            ("ddr4_mem_ctrl.sv", "ddr4"),
        ):
            out_sub = Path(td) / proto
            res = emit_protocol_testbench(str(ref / fname), str(out_sub), protocol=proto)
            assert res["ok"], f"{proto} emit failed: {res}"
            tb_p = Path(res["data"]["artifacts"]["sv_testbench"])
            sh_p = Path(res["data"]["artifacts"]["sim_script"])
            assert tb_p.is_file() and sh_p.is_file()
            lnt = tool_lint_tb(str(out_sub))
            assert lnt["ok"] and lnt["data"]["error_count"] == 0, f"{proto} lint_tb failed: {lnt}"


def test_sandbox_clear_dir_marked_nested() -> None:
    print("coverage: sandbox_copier._clear_dir on marked sandbox with nested dirs & symlinks")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        proj = td_p / "proj"
        proj.mkdir()
        (proj / "a.sv").write_text("module a; endmodule\n", encoding="utf-8")
        (proj / "b.sv").write_text("module b; endmodule\n", encoding="utf-8")
        sb = td_p / "sb"

        r1 = tool_copy_sandbox(str(proj), ["a.sv"], str(sb), clean=True)
        assert r1["ok"] and (sb / "a.sv").is_file()

        # Plant nested subdir + file + symlink inside the ALREADY-MARKED sandbox
        nested = sb / "stale_sub"
        nested.mkdir()
        (nested / "old.txt").write_text("stale", encoding="utf-8")
        (sb / "stale_link").symlink_to(nested / "old.txt")

        # Second copy with clean=True invokes _clear_dir() and wipes stale_sub + stale_link
        r2 = tool_copy_sandbox(str(proj), ["b.sv"], str(sb), clean=True)
        assert r2["ok"]
        assert not nested.exists()
        assert not (sb / "stale_link").exists()
        assert not (sb / "a.sv").exists()
        assert (sb / "b.sv").is_file()


def test_sim_runner_fst_probe() -> None:
    print("coverage: sim_runner._fst_available probe")
    # Missing binary must cleanly return (False, reason)
    ok_bad, msg_bad = _fst_available("nonexistent_verilator_binary_xyz")
    assert ok_bad is False and msg_bad

    # Real verilator binary probe (either True if lz4.h installed, or False with diagnostic)
    ok_real, msg_real = _fst_available("verilator")
    assert isinstance(ok_real, bool) and isinstance(msg_real, str)


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


def test_all_11_protocol_classifiers_and_clean_pt_registry() -> None:
    print("coverage: all 11 protocol classifiers + clean_pt_netlist via call_tool")
    ref = ROOT / "examples" / "multi_bus_ref"
    omni = ref / "omni_hub" / "soc_omni_bus_hub.sv"
    r_omni = tool_extract_interfaces(str(omni))
    assert r_omni["ok"]
    protos = {i["protocol"] for i in r_omni["data"]["modules"][0]["interfaces"]}
    assert {"spi", "apb", "axi4", "ethernet", "pcie", "ddr4"}.issubset(protos), f"Missing protos: {protos}"

    for fname, exp in (
        ("ahb_lite_slave.sv", "ahb_lite"),
        ("ddr3_mem_ctrl.sv", "ddr3"),
        ("hbm_pc_ctrl.sv", "hbm"),
    ):
        r = tool_extract_interfaces(str(ref / fname))
        assert r["ok"] and any(i["protocol"] == exp for i in r["data"]["modules"][0]["interfaces"])

    # clean_pt_netlist via registry call_tool
    with tempfile.TemporaryDirectory() as td:
        in_v = Path(td) / "net.v"
        out_v = Path(td) / "net_clean.v"
        in_v.write_text("module m(output q);\n  RDBUS u0(.o_d(q));\nendmodule\n", encoding="utf-8")
        r_pt = call_tool("clean_pt_netlist", {"in_verilog": str(in_v), "out_verilog": str(out_v)})
        assert r_pt["ok"] and r_pt["data"]["removed_instances"] == 1
        assert "assign q = 1'b0;" in out_v.read_text(encoding="utf-8")




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
    import os
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
    import subprocess

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "install_git_hook.sh"
    assert script.is_file(), str(script)

    # 语法检查
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, "bash -n failed"
    # 必须用 git rev-parse 定位仓库根（而非仅依赖脚本自身位置）
    body = script.read_text(encoding="utf-8")
    assert "git rev-parse --show-toplevel" in body, "should locate repo via git rev-parse"

    # 端到端：在临时 git 仓库的【子目录】运行，应成功装上 hook
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=str(wd), check=True)
        sub = wd / "deep" / "nested"
        sub.mkdir(parents=True, exist_ok=True)
        # 把脚本复制到子目录运行（模拟用户从别处执行）
        (sub / "install_git_hook.sh").write_bytes(script.read_bytes())
        r = subprocess.run(["bash", "install_git_hook.sh"], cwd=str(sub),
                           capture_output=True, text=True)
        assert r.returncode == 0, f"rc={r.returncode} {r.stderr[:200]}"
        hook = wd / ".git" / "hooks" / "pre-commit"
        assert hook.is_file(), "pre-commit hook not installed"
        assert "verify_invariants.py --fast" in hook.read_text(encoding="utf-8")

    # 非 git 目录 → 退出码 1 + 可读提示
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(["bash", str(script)], cwd=td, capture_output=True, text=True)
        assert r.returncode == 1, f"rc={r.returncode}"
        assert "Not a git repository" in r.stderr, r.stderr[:200]


def test_mutation_target_containment() -> None:
    """Defect 99: run_mutation must not write to files outside the workdir.

    旧实现 ``wd / m["file"]`` 不做沙箱约束，绝对路径 / ``..`` / 指向外部的符号
    链接都会被 write_bytes 改写。finally 还原只在正常执行时生效，任何 crash
    落在 write 与 restore 之间就永久破坏外部文件。
    """
    print("coverage: mutation target containment (abs / ../ / symlink escape)")
    import os as _os

    from fpga_toolbox.mutation_runner import tool_run_mutation

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "a.sv").write_text("module a; endmodule\n", encoding="utf-8")
        outside = wd.parent / "ext_target.sv"
        outside.write_text("EXT_ORIG\n", encoding="utf-8")

        # 绝对路径越界
        spec_abs = wd / "m_abs.yaml"
        spec_abs.write_text(
            f'mutations:\n  - id: M01\n    file: "{outside}"\n    old: "EXT_ORIG"\n    new: "MUT"\n',
            encoding="utf-8")
        # 全部变异都越界 => 一个都评估不到 => 必须 ok=False（分数未定义），
        # 而不是像旧实现那样报一个「0% 或 100%」的分数。这条同时锁住缺陷 113
        # 的 evaluated==0 分支。
        r_abs = tool_run_mutation(str(wd), str(spec_abs), ["true"], min_score=0.0)
        assert not r_abs["ok"], "all-out-of-sandbox spec must not report a score"
        assert "No mutation could be evaluated" in r_abs["error"], r_abs["error"]
        assert r_abs["data"]["evaluated"] == 0
        assert any(x["status"] == "ERROR" for x in r_abs["data"]["results"]), r_abs["data"]["results"]
        assert outside.read_text(encoding="utf-8") == "EXT_ORIG\n"

        # ../ 相对路径越界
        spec_rel = wd / "m_rel.yaml"
        spec_rel.write_text(
            'mutations:\n  - id: M01\n    file: "../ext_target.sv"\n    old: "EXT_ORIG"\n    new: "MUT"\n',
            encoding="utf-8")
        r_rel = tool_run_mutation(str(wd), str(spec_rel), ["true"], min_score=0.0)
        assert not r_rel["ok"] and r_rel["data"]["evaluated"] == 0
        assert any(x["status"] == "ERROR" for x in r_rel["data"]["results"])
        assert outside.read_text(encoding="utf-8") == "EXT_ORIG\n"

        # 沙箱内符号链接指向外部
        outside2 = wd.parent / "ext_target2.sv"
        outside2.write_text("EXT2_ORIG\n", encoding="utf-8")
        _os.symlink(outside2, wd / "link.sv")
        spec_link = wd / "m_link.yaml"
        spec_link.write_text(
            'mutations:\n  - id: M01\n    file: "link.sv"\n    old: "EXT2_ORIG"\n    new: "MUT"\n',
            encoding="utf-8")
        r_link = tool_run_mutation(str(wd), str(spec_link), ["true"], min_score=0.0)
        assert not r_link["ok"] and r_link["data"]["evaluated"] == 0
        assert any(x["status"] == "ERROR" and "inside the working directory" in x["detail"]
                   for x in r_link["data"]["results"]), r_link["data"]["results"]
        assert outside2.read_text(encoding="utf-8") == "EXT2_ORIG\n"

        # 合法目标仍正常变异并还原
        spec_ok = wd / "m_ok.yaml"
        (wd / "good.sv").write_text("assign y = 1;\n", encoding="utf-8")
        spec_ok.write_text(
            'mutations:\n  - id: M01\n    file: "good.sv"\n    old: "assign y = 1;"\n    new: "assign y = 0;"\n',
            encoding="utf-8")
        # 合法目标仍正常变异并还原。test_cmd 必须「baseline 通过、变异后失败」
        # 才是真实可用的检测命令：grep 断言 good.sv 里仍是 assign y = 1。
        # 旧测试用 ["false"]（永远失败）——那正是缺陷 113 里"坏 test_cmd 造出
        # 假 100% 得分"的情形，现在会被 baseline 检查挡掉。
        r_ok = tool_run_mutation(
            str(wd), str(spec_ok),
            ["grep", "-q", "assign y = 1;", "good.sv"], min_score=100.0)
        assert r_ok["ok"], r_ok.get("error")
        assert r_ok["data"]["killed"] == 1, r_ok["data"]
        assert r_ok["data"]["evaluated"] == 1
        assert r_ok["data"]["baseline"]["returncode"] == 0
        assert "assign y = 1;" in (wd / "good.sv").read_text(encoding="utf-8")  # 已还原


def test_remote_work_dir_injection_blocked() -> None:
    """Defect 100: generate_remote_run_script 未校验 remote_work_dir。

    host/tag/local_script_name 都有白名单，但 remote_work_dir 会被原文插进
    **远端执行**的脚本（use_dev_shm=False 路径的
    `mkdir -p {remote_work_dir}/{tag}`）。漏校验它让三道白名单形同虚设。
    """
    print("coverage: remote_work_dir shell-injection blocked")
    from fpga_toolbox.eda_campaign import generate_remote_run_script

    # 注入向量必须被拒
    for bad in ["x; touch /tmp/PWNED; #", "$(reboot)", "`id`", "~/x && rm -rf /", "a|b"]:
        try:
            generate_remote_run_script("h", "t", "run.sh",
                                       remote_work_dir=bad, use_dev_shm=False)
            raise AssertionError(f"remote_work_dir injection NOT rejected: {bad!r}")
        except ValueError:
            pass

    # 合法目录仍可用，且原样出现在脚本里（无元字符）
    for good in ["~/work", "/tmp/eda", "/home/user/proj_run"]:
        s = generate_remote_run_script("h", "t", "run.sh",
                                       remote_work_dir=good, use_dev_shm=False)
        assert f"mkdir -p {good}/t && cd {good}/t" in s, (good, s)

    # dev_shm=True 路径不引用 remote_work_dir，但仍走校验且不受影响
    s_shm = generate_remote_run_script("h", "t", "run.sh",
                                       remote_work_dir="~/work", use_dev_shm=True)
    assert "mkdir -p /dev/shm/t" in s_shm
    # dev_shm=True 下非法 remote_work_dir 同样被拒（一致性）
    try:
        generate_remote_run_script("h", "t", "run.sh",
                                   remote_work_dir="; bad", use_dev_shm=True)
        raise AssertionError("dev_shm=True should still validate remote_work_dir")
    except ValueError:
        pass


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


def test_resolve_deps_path_prefixed_include() -> None:
    """Defect 102: 带路径前缀的 include 被误报 W101 并漏出 staged 清单。

    headers 按 basename 建索引（defs.svh -> inc/defs.svh），但 RTL 里的
    ``include 常带路径前缀（inc/defs.svh）。旧实现直接 hdrs[inc] 查不到 ->
    真实存在的 header 被误报 W101 not-found 并排除在 header_files / all_files
    之外，copy_sandbox 拿这份清单去复制就漏掉 header，沙箱 RTL 编译报
    「Cannot find include file」。
    """
    print("coverage: resolve_deps path-prefixed include (inc/defs.svh)")
    from fpga_toolbox.repo_indexer import tool_index_repo, tool_resolve_deps

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "inc").mkdir()
        (root / "inc" / "defs.svh").write_text("parameter W = 8;\n", encoding="utf-8")
        (root / "top.sv").write_text(
            'module top;\n`include "inc/defs.svh"\nendmodule\n', encoding="utf-8")
        idx = tool_index_repo(str(root))
        assert idx["ok"], idx.get("error")
        r = tool_resolve_deps("top", repo_index=idx["data"])
        assert r["ok"], r.get("error")
        # 真实存在的 header 必须进 staged 清单
        assert "inc/defs.svh" in r["data"]["header_files"], r["data"]["header_files"]
        assert "inc/defs.svh" in r["data"]["all_files"], r["data"]["all_files"]
        # 不应再误报 W101 not-found
        w101 = [d for d in r.get("diagnostics", []) if d.get("code") == "W101"]
        assert not w101, w101

        # 真正缺失的 header（basename 形式与带前缀形式）仍报 W101
        (root / "top2.sv").write_text(
            'module top2;\n`include "nowhere.svh"\n`include "sub/missing.svh"\nendmodule\n',
            encoding="utf-8")
        idx2 = tool_index_repo(str(root))
        r2 = tool_resolve_deps("top2", repo_index=idx2["data"])
        assert r2["ok"]
        w101_2 = sorted(d["path"] for d in r2.get("diagnostics", []) if d.get("code") == "W101")
        assert w101_2 == ["nowhere.svh", "sub/missing.svh"], w101_2
        assert r2["data"]["header_files"] == []


def test_validator_bad_offset_no_crash() -> None:
    """Defect 103: 非数字 offset（"abc" / 4.5 / "0xZZ"）让 int() ValueError 冒泡。

    spec 由用户/AI 手写，offset 可能写成非数字。旧实现 _to_int 里
    int(str(val), 0) 直接抛 ValueError，用户只看到 invalid literal for int()
    堆栈，没指向是哪个字段，也没落进诊断码体系。改用 _to_int_or_none，按既有
    E005（register_rules）/ E008（illegal_addr_rules）给可定位诊断。
    """
    print("coverage: validator non-numeric offset -> E005/E008 (no crash)")
    import yaml

    from fpga_toolbox.core.validator import build_and_validate_ir, load_raw_spec

    for bad in ["abc", 4.5, "0xZZ"]:
        with tempfile.TemporaryDirectory() as td:
            spec = {
                "boundary": {"module_name": "m"},
                "test_plan": {
                    "protocol_rules": {"max_resp_cycles": 16},
                    "register_rules": [
                        {"rule_id": "R1", "name": "CTRL", "offset": bad,
                         "reset_value": 0, "access": "RW"}],
                    "illegal_addr_rules": [
                        {"rule_id": "I1", "offset": bad,
                         "expected_resp": "SLVERR"}],
                },
            }
            spec_p = Path(td) / "s.yaml"
            spec_p.write_text(yaml.safe_dump(spec), encoding="utf-8")
            # 修复前这里抛 ValueError；修复后应产出诊断码而非崩溃
            _ir, diags = build_and_validate_ir(load_raw_spec(spec_p))
            codes = [d.code for d in diags]
            assert "E005" in codes, (bad, codes)  # register_rules offset
            assert "E008" in codes, (bad, codes)  # illegal_addr_rules offset
            e005 = next(d for d in diags if d.code == "E005")
            assert "must be an integer" in e005.message, e005.message

    # 合法数字 offset（含 hex 字符串）不应报 E005/E008 offset 错
    with tempfile.TemporaryDirectory() as td:
        spec = {
            "boundary": {"module_name": "m"},
            "test_plan": {
                "protocol_rules": {"max_resp_cycles": 16},
                "register_rules": [
                    {"rule_id": "R1", "name": "CTRL", "offset": "0x4",
                     "reset_value": 0, "access": "RW"}],
                "illegal_addr_rules": [],
            },
        }
        spec_p = Path(td) / "ok.yaml"
        spec_p.write_text(yaml.safe_dump(spec), encoding="utf-8")
        _ir, diags = build_and_validate_ir(load_raw_spec(spec_p))
        e005 = [d for d in diags if d.code == "E005"]
        assert not e005, e005  # 合法 hex offset 不误报


def test_handshake_while_case_insensitive_ready() -> None:
    """Defect 104: while 分支的 READY 映射不做大小写归一。

    _READY_OF 键为全大写，但 while 分支用 sig.replace("axi.", "") 不 .upper()，
    而 wait() 分支用 .split(".")[-1].upper()。实测同一份代码只改 VALID 信号
    大小写：`while (!axi.BVALID)` 在 BREADY 已拉高时报 R1（safe），而
    `while (!axi.bvalid)` 报 R3（假错误）—— 纯大小写差异导致判定不同。
    """
    print("coverage: while-loop READY mapping case-insensitive (R1 for all cases)")
    from fpga_toolbox.tb_linter import scan_handshake

    def _scan(stmt: str, sig: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            tb = Path(td) / "tb.sv"
            tb.write_text(
                "module tb;\ninitial begin\nBREADY = 1;\n"
                f"{stmt.replace('SIG', sig)}\nend\nendmodule\n",
                encoding="utf-8")
            return [i["code"] for i in scan_handshake(tb)]

    # wait() 与 while() 两个分支、UPPER/lower/裸名三种写法，全部应一致报 R1
    for sig in ("axi.BVALID", "axi.bvalid", "BVALID", "bvalid", "axi.Bvalid"):
        assert "R1" in _scan("wait (SIG);", sig), ("wait", sig)
        assert "R1" in _scan("while (!SIG) @(posedge clk);", sig), ("while", sig)
        # 绝不能因大小写差异产生假 R3 错误
        assert "R3" not in _scan("while (!SIG) @(posedge clk);", sig), ("while-false-R3", sig)


def test_handshake_r2_r4_case_insensitive() -> None:
    """Defect 105: R2/R4 规则正则只认大写，小写 TB 静默漏报。

    R4（等 VALID 后立刻拉低 READY）与 R2（if(VALID) 分支内拉低 READY）用
    axi\.([A-Z_]+VALID) 且 \b{READY}\b 大小写敏感 —— 真实第三方 TB 大量用小写
    （axi.bvalid / bready），这些握手 bug 被静默漏报。与缺陷 104（R1/R3）同根。
    """
    print("coverage: R2/R4 rules case-insensitive (lowercase TB no longer missed)")
    from fpga_toolbox.tb_linter import scan_handshake

    def _scan(src: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            tb = Path(td) / "tb.sv"
            tb.write_text(src, encoding="utf-8")
            return [i["code"] for i in scan_handshake(tb)]

    # R4：等 VALID 后立刻拉低 READY —— upper 与 lower 必须都报 R4
    r4_upper = _scan("module tb;\ninitial begin\n"
                     "while (!axi.BVALID) @(posedge clk);\n"
                     "axi.BREADY <= 1'b0;\nend\nendmodule\n")
    r4_lower = _scan("module tb;\ninitial begin\n"
                     "while (!axi.bvalid) @(posedge clk);\n"
                     "axi.bready <= 1'b0;\nend\nendmodule\n")
    assert "R4" in r4_upper, r4_upper
    assert "R4" in r4_lower, r4_lower  # 修复前漏报

    # R2：if(VALID) 分支内立刻拉低 READY —— upper 与 lower 必须都报 R2
    r2_upper = _scan("module tb;\nalways @(posedge clk) begin\n"
                     "if (axi.BVALID) begin\naxi.BREADY <= 1'b0;\nend\nend\nendmodule\n")
    r2_lower = _scan("module tb;\nalways @(posedge clk) begin\n"
                     "if (axi.bvalid) begin\naxi.bready <= 1'b0;\nend\nend\nendmodule\n")
    assert "R2" in r2_upper, r2_upper
    assert "R2" in r2_lower, r2_lower  # 修复前漏报

    # 不误伤：子串 valid/already、非 axi. 前缀不应触发 R2/R4
    fp = _scan("module tb;\ninitial begin\n"
               "x = invalid; y = already; while(!axi.bvalid) @(posedge clk);\n"
               "end\nendmodule\n")
    assert "R2" not in fp and "R4" not in fp, fp


def test_ready_raised_before_case_insensitive() -> None:
    """Defect 106: _ready_raised_before 大小写敏感，小写 READY 已拉高仍报假 R3。

    _ready_raised_before 被 R1/R3/R2 共用，ready_sig 是规范大写（BREADY），但
    源码里可能是小写（bready）。旧实现 \b{READY}\b 大小写敏感 —— 小写 TB 里
    「READY 已拉高」识别不到，R1/R3/R2 全部误判成「READY 未拉高」而报假错误。
    缺陷 104/105 同根（那两处改了映射与正则，漏了这个共用 helper）。
    """
    print("coverage: _ready_raised_before case-insensitive (lowercase READY=1 -> R1)")
    from fpga_toolbox.tb_linter import scan_handshake

    def _scan(ready_assign: str, sig: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            tb = Path(td) / "tb.sv"
            tb.write_text(
                "module tb;\ninitial begin\n"
                f"{ready_assign}\nwait ({sig});\nend\nendmodule\n",
                encoding="utf-8")
            return [i["code"] for i in scan_handshake(tb)]

    # READY 已拉高（大写 / 小写 / 非阻塞赋值）-> 都应报 R1（safe），不得假 R3
    assert "R1" in _scan("BREADY = 1;", "axi.BVALID")
    assert "R1" in _scan("bready = 1;", "axi.bvalid")   # 修复前报 R3
    assert "R1" in _scan("bready <= 1;", "axi.bvalid")  # 修复前报 R3
    for sig in ("axi.bvalid", "bvalid"):
        assert "R3" not in _scan("bready = 1;", sig), sig

    # 真风险：READY 被拉低 / 根本没有 -> 仍必须报 R3（不得因 IGNORECASE 变假通过）
    def _scan_src(src: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            tb = Path(td) / "tb.sv"
            tb.write_text(src, encoding="utf-8")
            return [i["code"] for i in scan_handshake(tb)]

    assert "R3" in _scan_src("module tb;\ninitial begin\n"
                             "BREADY <= 1'b0;\nwait (axi.BVALID);\nend\nendmodule\n")
    assert "R3" in _scan_src("module tb;\ninitial begin\n"
                             "bready <= 1'b0;\nwait (axi.bvalid);\nend\nendmodule\n")
    assert "R3" in _scan_src("module tb;\ninitial begin\n"
                             "wait (axi.bvalid);\nend\nendmodule\n")


def test_s1_sv_literal_constant_stub() -> None:
    """Defect 107: S1 只认纯整数 return 常量，漏掉 SV 地道常量 stub。

    旧正则 `^return [-]?\d+;` 只匹配 return 0 / -1。SV 里最地道的常量 stub
    写法 return 1'b0 / '0 / 8'hFF / 0.0 语义上同样是「helper 永远返回固定值、
    不含任何真实判定」，却全部漏报。扩成覆盖纯整数 / 位宽字面量 / 未位宽
    '0/'1 / 实数，同时保持「含标识符/运算符的表达式」不误报。
    """
    print("coverage: S1 catches SV literal constant stubs (1'b0 / '0 / 8'hFF / 0.0)")
    # S1 属 silent_pass 组（缺陷 113 从 scan_handshake 迁到 scan_sv_silent_pass）：
    # 用 scan_handshake 测它等于在测一个"规则路由错了"的实现。
    from fpga_toolbox.tb_linter import scan_sv_silent_pass

    def _scan(fn_body: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            tb = Path(td) / "tb.sv"
            tb.write_text("module tb;\n" + fn_body + "\nendmodule\n", encoding="utf-8")
            return [i["code"] for i in scan_sv_silent_pass(tb)]

    stubs = [
        ("function int chk();\nreturn 0;\nendfunction"),
        ("function int chk();\nreturn -1;\nendfunction"),
        ("function int chk();\nreturn 1;\nendfunction"),
        ("function bit chk();\nreturn 1'b0;\nendfunction"),      # 修复前漏报
        ("function logic chk();\nreturn '0;\nendfunction"),      # 修复前漏报
        ("function [7:0] chk();\nreturn 8'hFF;\nendfunction"),  # 修复前漏报
        ("function real chk();\nreturn 0.0;\nendfunction"),     # 修复前漏报
    ]
    for body in stubs:
        codes = _scan(body)
        assert "S1" in codes, (body, codes)

    # 不误伤：含标识符/运算符的表达式、含分支的函数，不是常量 stub
    legit = [
        ("function int chk(input int a);\nreturn a + 1;\nendfunction"),
        ("function bit chk(input [7:0] a);\nreturn a == 0;\nendfunction"),
        ("function bit chk(input [7:0] a);\nif (a == 0) return 1'b1;"
         "\nelse return 1'b0;\nendfunction"),
    ]
    for body in legit:
        codes = _scan(body)
        assert "S1" not in codes, (body, codes)



def test_pcie_tb_matches_dut_parameterization() -> None:
    """Defect 108: PCIe TB 模板写死参数覆盖与网线位宽，产出编译不过的 TB 却报 ok。

    三个同根变体（工具都报 ok=True，但 Verilator 直接 %Error）：
      1. DUT 未声明 GEN/LANES/DW（固定配置 endpoint 很常见）—— 模板仍无条件
         实例化 `dut #(.GEN(GEN), .LANES(LANES), .DW(DW))`，Verilator 报
         `Parameter not found: 'GEN'`（3 条 PINNOTFOUND），9 个 suite 全编译失败。
      2. DUT 声明 GEN/LANES 但 tdata 位宽固定、无 DW 参数 —— 仍覆盖不存在的 DW。
      3. cfg 端口不是 4/6 位（如 8 位）—— TB 网线写死 [3:0]/[5:0]，生成
         WIDTHEXPAND，而 Verilator 默认警告即错误，构建失败。

    修复口径：参数覆盖只对 DUT 真实声明的参数发出；网线位宽取 RTL 真实端口位宽；
    只有 GEN+LANES 都声明时才跑 9 配置矩阵，且仅在 DW 真的把 LANES 映射到 tdata
    位宽时才覆盖 DW；run 脚本的 -GGEN/-GLANES 与矩阵保持一致（固定配置不得传）。
    """
    print("coverage: pcie TB parameterization follows the DUT (no blind .GEN/.LANES/.DW)")
    from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench

    body = """(
  input  logic             clk,
  input  logic             rst_n,
  input  logic             pcie_rx_tvalid,
  output logic             pcie_rx_tready,
  input  logic [%(rwd)s]   pcie_rx_tdata,
  input  logic             pcie_rx_tlast,
  output logic             pcie_tx_tvalid,
  input  logic             pcie_tx_tready,
  output logic [%(rwd)s]   pcie_tx_tdata,
  output logic             pcie_tx_tlast,
  output logic             pcie_link_up,
  output logic [%(swd)s]   pcie_cfg_speed,
  output logic [%(lwd)s]   pcie_cfg_lanes
);
  logic [31:0] bar_mem [0:15];
  assign pcie_link_up   = rst_n;
  assign pcie_cfg_speed = %(spd)s;
  assign pcie_cfg_lanes = %(lns)s;
  assign pcie_rx_tready = !pcie_tx_tvalid;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      pcie_tx_tvalid <= 1'b0; pcie_tx_tdata <= '0; pcie_tx_tlast <= 1'b0;
    end else begin
      if (pcie_tx_tvalid && pcie_tx_tready) begin
        pcie_tx_tvalid <= 1'b0; pcie_tx_tlast <= 1'b0;
      end
      if (pcie_rx_tvalid && pcie_rx_tready) begin
        if (pcie_rx_tdata[63:56] == 8'h40) begin
          bar_mem[pcie_rx_tdata[5:2]] <= pcie_rx_tdata[31:0];
        end else if (pcie_rx_tdata[63:56] == 8'h00) begin
          pcie_tx_tvalid <= 1'b1; pcie_tx_tlast <= 1'b1; pcie_tx_tdata <= '0;
          pcie_tx_tdata[63:0] <= {8'h4A, pcie_rx_tdata[55:48],
                                  pcie_rx_tdata[47:32],
                                  bar_mem[pcie_rx_tdata[5:2]]};
        end
      end
    end
  end
endmodule
"""

    def _emit(mod: str, header: str, rwd: str, swd: str, lwd: str) -> tuple:
        src = "`timescale 1ns/1ps\nmodule " + mod + header + (
            body % {"rwd": rwd, "swd": swd, "lwd": lwd, "spd": "4'd3", "lns": "6'd1"}
        )
        with tempfile.TemporaryDirectory() as td:
            rtl = Path(td) / "dut.sv"
            rtl.write_text(src, encoding="utf-8")
            r = emit_protocol_testbench(str(rtl), str(Path(td) / "out"), "pcie")
            assert r["ok"], (mod, r.get("error"))
            tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
            sh = Path(r["data"]["artifacts"]["sim_script"]).read_text(encoding="utf-8")
            return r["data"]["suites"], tb, sh

    # 1) 固定配置：无任何 parameter 声明 —— 单 suite、零参数覆盖、零 -G 传参
    suites, tb, sh = _emit("pcie_fixed_ep", " ", "63:0", "3:0", "5:0")
    assert suites == ["pcie"], suites
    inst = [ln for ln in tb.splitlines() if " dut (" in ln][0]
    assert inst.strip() == "pcie_fixed_ep dut (", inst
    assert "-GGEN" not in sh and "pcie_g3x1" not in sh, sh[:300]
    assert "localparam int DW = 64" in tb, tb[:500]

    # 2) 有 GEN/LANES 但无 DW —— 9 配置矩阵 + 覆盖 GEN/LANES，但绝不覆盖 DW
    suites, tb, sh = _emit(
        "pcie_nodw_ep",
        " #(\n  parameter int GEN = 4,\n  parameter int LANES = 4\n)",
        "63:0", "3:0", "5:0",
    )
    assert len(suites) == 9, suites
    assert ".GEN(GEN)" in tb and ".LANES(LANES)" in tb, tb[:600]
    assert ".DW(" not in tb, tb[:600]
    assert "-GGEN=3" in sh and "-GLANES=1" in sh, sh[:400]

    # 3) cfg 端口 8 位 —— TB 网线必须跟着 8 位（旧模板写死 [3:0]/[5:0]）
    suites, tb, sh = _emit(
        "pcie_wide_ep",
        " #(\n  parameter int GEN = 4,\n  parameter int LANES = 4,\n"
        "  parameter int DW = 64\n)",
        "63:0", "7:0", "7:0",
    )
    assert "logic [7:0] pcie_cfg_speed" in tb, tb[:800]
    assert "logic [7:0] pcie_cfg_lanes" in tb, tb[:800]
    assert "logic [3:0] pcie_cfg_speed" not in tb, tb[:800]


def test_module_name_not_used_as_unsafe_filename() -> None:
    """Defect 109: 契约的 module_name 原样拼进产物文件名与 cocotb Makefile。

    `module_name` 看着只是个名字，实际被拼进每个产物路径
    （`<mod>_axil_wrap.sv` / `test_<mod>_axil.py`）和 Makefile 的
    `MODULE` / `TOPLEVEL`。契约 spec 由用户/AI 手写，于是两类真实后果：

      1. `module_name: ../../EVILMOD` —— Path 拼接里的 `..` 直接生效，4 个产物
         里 3 个被写到 out_dir **之外**（实测落在 out_dir 的祖父目录），
         工具仍返回 ok=True。写文件的位置由调用方数据决定，不是调用方能选的。
      2. `module_name: m$(shell touch PWNEDMK)_x` —— Makefile 里出现
         `MODULE = test_m$(shell touch PWNEDMK)_x_axil`，用户一执行 `make`
         就执行任意命令（实测文件被真实创建：这条是命令注入，不只是路径问题）。

    修复：validator 在契约层按 SV 标识符
    `[A-Za-z_][A-Za-z0-9_$]*` 收口（诊断 E016，覆盖 boundary 与
    apb-regfile-spec/v2 两个入口），tb_emitter 再按同一口径做一次写前
    纵深防御（防 build_and_validate_ir 被直接调用的路径）。
    """
    print("coverage: module_name is a safe SV identifier (no path traversal / make injection)")
    pm = {
        "awaddr": "s_axil_awaddr", "awvalid": "s_axil_awvalid", "awready": "s_axil_awready",
        "wdata": "s_axil_wdata", "wstrb": "s_axil_wstrb", "wvalid": "s_axil_wvalid",
        "wready": "s_axil_wready", "bresp": "s_axil_bresp", "bvalid": "s_axil_bvalid",
        "bready": "s_axil_bready", "araddr": "s_axil_araddr", "arvalid": "s_axil_arvalid",
        "arready": "s_axil_arready", "rdata": "s_axil_rdata", "rresp": "s_axil_rresp",
        "rvalid": "s_axil_rvalid", "rready": "s_axil_rready",
    }

    def _spec(mod_name: str) -> dict:
        return {
            "schema_version": "1.0",
            "boundary": {
                "module_name": mod_name, "clock": {"port": "clk"},
                "reset": {"port": "rst_n"}, "axi4_lite": {"port_map": pm},
            },
            "registers": [{"rule_id": "REG_001", "name": "CTRL", "offset": 0,
                           "access": "RW", "reset_value": 0}],
            "test_plan": {"regression": [{"id": "T1", "category": "reg_rw",
                                          "description": "rw"}]},
        }

    def _emit(mod_name: str):
        """返回 (result, created_labels, maketext)；临时目录销毁前把信息取出来。

        注意不能把 Path 直接返回出去 —— TemporaryDirectory 一退出目录就没了，
        后续 is_file() 会抛 FileNotFoundError（本测试第一版就这么踩的）。
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            sp = base / "spec.json"
            sp.write_text(json.dumps(_spec(mod_name)), encoding="utf-8")
            out = base / "deep" / "out"
            r = call_tool("emit_testbench", {"spec_path": str(sp), "out_dir": str(out)})
            out_r = out.resolve()
            files = [p for p in base.rglob("*") if p.is_file() and p.name != "spec.json"]
            escaped = [
                str(p.relative_to(base))
                for p in files
                if out_r not in p.resolve().parents
            ]
            mk = [p for p in base.rglob("Makefile")]
            mk_text = mk[0].read_text(encoding="utf-8") if mk else ""
            marker = [p.name for p in base.rglob("*PWNED*")]
            return r, escaped, mk_text, marker

    # 1) 路径穿越：必须拒绝，且绝不能有任何产物落到 out_dir 之外
    for bad in ("../../EVILMOD", "x/../../../../tmp/PWNED109", "../esc"):
        r, escaped, _mk, _marker = _emit(bad)
        assert r["ok"] is False, (bad, r.get("data"))
        assert "E016" in str(r.get("error")), (bad, str(r["error"])[:200])
        assert escaped == [], (bad, escaped)

    # 2) Makefile 命令注入：`$(shell ...)` 绝不能进 spec 产出的 Makefile
    r, escaped, mk_text, marker = _emit("m$(shell touch PWNEDMK)_x")
    assert r["ok"] is False, r.get("data")
    assert "E016" in str(r.get("error")), str(r["error"])[:200]
    assert mk_text == "", "Makefile was generated for a rejected module_name"
    assert "shell" not in mk_text, mk_text[:300]
    assert marker == [], f"injection marker created: {marker}"

    # 3) 合法 SV 标识符（含 $，SV 允许）仍照常生成，不能过度收紧
    for good in ("reg_slave", "apb_dut", "M1_x", "a$b"):
        r, escaped, mk_text, _marker = _emit(good)
        assert r["ok"] is True, (good, str(r.get("error"))[:200])
        assert escaped == [], (good, escaped)
        assert Path(r["data"]["artifacts"]["unit_isolation_wrapper"]).name == f"{good}_axil_wrap.sv", \
            (good, r["data"]["artifacts"])
        assert f"TOPLEVEL = {good}_axil_wrap" in mk_text, (good, mk_text[:400])

    # 4) v2 schema（apb-regfile-spec）入口同样收口：它的 module 也会进文件名
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        sp = base / "spec.json"
        sp.write_text(json.dumps({
            "$schema": "apb-regfile-spec/v2", "module": "../../V2EVIL",
            "registers": [{"name": "CTRL", "offset": 0, "attr": "RW", "reset": 0}],
        }), encoding="utf-8")
        r = call_tool("emit_testbench", {"spec_path": str(sp), "out_dir": str(base / "out")})
        assert r["ok"] is False and "E016" in str(r["error"]), str(r.get("error"))[:200]


def test_ddr_hbm_templates_follow_port_widths() -> None:
    """Defect 110: DDR3/DDR4/HBM 模板写死位宽，产出的 TB 因宽度告警编译失败却报 ok。

    Verilator 默认**警告即错误**，所以 WIDTHTRUNC / WIDTHEXPAND / WIDTHDIGITS
    会让构建直接失败 —— 而工具返回 ok=True，DUT 一点没验到。三个协议实测：

      * DDR3 `ddr3_ba` 写死 [2:0]、数据常量写死 8'hBEEF / 8'h1234 / %04h：
        DUT 用 2 位 BA 或 8 位 DQ 时报 4 条 WIDTHDIGITS + 1 条 WIDTHTRUNC。
      * DDR4 `ddr4_adr` 写死 17 位、dq 写死 64 位、dm_dbi_n 写死 8 位：
        DUT 用 14/8/1 位时报 4 条 WIDTHTRUNC。
      * HBM `wdata`/`rdata` 写死 256 位、ID 写死 6 位、addr 写死 28 位：
        DUT 用 64 位或 512 位数据时报 WIDTHEXPAND/WIDTHTRUNC。

    修复：新增 `_pw()` 从 `port_details` 取 RTL 真实端口位宽（缺失时退回
    addr_width/data_width 再退回标准默认），三个模板全部按它声明网线；
    数据常量按真实位宽缩放（hex 位数 + 掩码），ID/BA/BG 取值按位宽截断。
    """
    print("coverage: DDR3/DDR4/HBM templates follow real port widths")
    from fpga_toolbox.protocol_tb_emitter import _pw

    iface = {
        "port_details": {
            "ba": {"port": "ddr3_ba", "direction": "output", "width": 2},
            "dq": {"port": "ddr3_dq_out", "direction": "output", "width": 8},
            "addr": {"port": "ddr3_addr", "direction": "output", "width": 10},
        },
        "addr_width": 10,
        "data_width": 8,
    }
    assert _pw(iface, "ba", 3) == 2, "port_details width must win"
    assert _pw(iface, "dq", 16) == 8
    assert _pw(iface, "addr", 14) == 10
    # 缺失 port_details 时退回 addr_width / data_width
    assert _pw({"addr_width": 12, "data_width": 32}, "addr", 14) == 12
    assert _pw({"addr_width": 12, "data_width": 32}, "dq", 16) == 32
    # 两者都没有时用标准默认值，不能变成 0（否则生成 [-1:0]）
    assert _pw({}, "ba", 3) == 3
    assert _pw({}, "dq", 16) == 16

    def _emit(rtl_src: str, proto: str) -> tuple:
        with tempfile.TemporaryDirectory() as td:
            rtl = Path(td) / "dut.sv"
            rtl.write_text(rtl_src, encoding="utf-8")
            r = call_tool("emit_testbench", {
                "rtl_file": str(rtl), "out_dir": str(Path(td) / "out"),
                "protocol": proto,
            })
            tb = ""
            arts = (r.get("data") or {}).get("artifacts") or {}
            if arts.get("sv_testbench"):
                tb = Path(arts["sv_testbench"]).read_text(encoding="utf-8")
            return r, tb

    # DDR3：2 位 BA + 8 位 DQ + 10 位 addr —— TB 网线必须跟着变，常量不能溢出
    ddr3_rtl = """`timescale 1ns/1ps
module ddr3_narrow (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        ddr3_ck_p,
  input  logic        ddr3_ck_n,
  output logic        ddr3_cke,
  output logic        ddr3_cs_n,
  output logic        ddr3_ras_n,
  output logic        ddr3_cas_n,
  output logic        ddr3_we_n,
  output logic [1:0]  ddr3_ba,
  output logic [9:0]  ddr3_addr,
  input  logic [7:0]  ddr3_dq_in,
  output logic [7:0]  ddr3_dq_out,
  output logic        ddr3_rd_valid
);
  assign ddr3_cke = 1; assign ddr3_cs_n = 1; assign ddr3_ras_n = 1;
  assign ddr3_cas_n = 1; assign ddr3_we_n = 1; assign ddr3_ba = '0;
  assign ddr3_addr = '0; assign ddr3_dq_out = ddr3_dq_in;
  assign ddr3_rd_valid = 1'b1;
endmodule
"""
    r, tb = _emit(ddr3_rtl, "ddr3")
    assert r["ok"], str(r.get("error"))[:200]
    assert "logic [1:0]  ddr3_ba" in tb, tb[:800]
    assert "logic [9:0] ddr3_addr" in tb, tb[:800]
    assert "logic [7:0] ddr3_dq_in" in tb, tb[:800]
    # 8 位总线上 0xBEEF 是 Too many digits：常量必须被掩码到 0xEF
    assert "8'hEF" in tb, [ln for ln in tb.splitlines() if "ddr3_cmd(3'b100" in ln][:2]
    assert "8'hBEEF" not in tb
    assert "%04h" not in tb, "printf 位宽也必须跟随（8 位用 %02h）"

    # DDR4：14 位 adr + 8 位 dq + 1 位 dm_dbi_n + 2 位 bg/ba
    ddr4_rtl = """`timescale 1ns/1ps
module ddr4_narrow (
  input  logic         clk,
  input  logic         rst_n,
  input  logic         ddr4_ck_t,
  input  logic         ddr4_ck_c,
  output logic         ddr4_act_n,
  output logic [1:0]   ddr4_bg,
  output logic [1:0]   ddr4_ba,
  output logic [13:0]  ddr4_adr,
  output logic         ddr4_cs_n,
  output logic         ddr4_odt,
  input  logic [7:0]   ddr4_dq_in,
  output logic [7:0]   ddr4_dq_out,
  output logic [0:0]   ddr4_dm_dbi_n,
  output logic         ddr4_rd_valid
);
  assign ddr4_act_n = 1; assign ddr4_bg = '0; assign ddr4_ba = '0;
  assign ddr4_adr = '0; assign ddr4_cs_n = 1; assign ddr4_odt = 0;
  assign ddr4_dq_out = ddr4_dq_in; assign ddr4_dm_dbi_n = '0;
  assign ddr4_rd_valid = 1'b1;
endmodule
"""
    r, tb = _emit(ddr4_rtl, "ddr4")
    assert r["ok"], str(r.get("error"))[:200]
    assert "logic [13:0] ddr4_adr" in tb, tb[:900]
    assert "logic [7:0] ddr4_dq_in" in tb, tb[:900]
    assert "logic [0:0]  ddr4_dm_dbi_n" in tb, tb[:900]
    assert "logic [16:0] ddr4_adr" not in tb
    assert "logic [63:0] ddr4_dq_in" not in tb

    # HBM：64 位数据 / 4 位 ID / 32 位地址 —— 旧的 256/6/28 会 WIDTHEXPAND
    hbm_rtl = """`timescale 1ns/1ps
module hbm_narrow (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [3:0]   hbm_awid,
  input  logic [31:0]  hbm_awaddr,
  input  logic         hbm_awvalid,
  output logic         hbm_awready,
  input  logic [63:0]  hbm_wdata,
  input  logic         hbm_wvalid,
  output logic         hbm_wready,
  output logic [3:0]   hbm_bid,
  output logic         hbm_bvalid,
  input  logic         hbm_bready,
  input  logic [3:0]   hbm_arid,
  input  logic [31:0]  hbm_araddr,
  input  logic         hbm_arvalid,
  output logic         hbm_arready,
  output logic [3:0]   hbm_rid,
  output logic [63:0]  hbm_rdata,
  output logic         hbm_rvalid,
  input  logic         hbm_rready
);
  assign hbm_awready = 1; assign hbm_wready = 1; assign hbm_arready = 1;
  assign hbm_bid = hbm_awid; assign hbm_bvalid = 1'b1;
  assign hbm_rid = hbm_arid; assign hbm_rdata = '0; assign hbm_rvalid = 1'b1;
endmodule
"""
    r, tb = _emit(hbm_rtl, "hbm")
    assert r["ok"], str(r.get("error"))[:200]
    assert "logic [3:0]   hbm_awid" in tb, tb[:900]
    assert "logic [63:0] hbm_wdata" in tb, tb[:900]
    assert "logic [31:0]  hbm_awaddr" in tb, tb[:900]
    assert "logic [255:0] hbm_wdata" not in tb
    # 数据图案必须按 64 位构造，不能被十六进制字符截断成错值
    assert "64'h0123456789ABCDEF" in tb, tb[:1200]
    assert "64'hF123456789ABCDEF" not in tb


def test_axi4_template_follows_id_len_widths() -> None:
    """Defect 111: AXI4 模板把 ID/len/size/burst/resp 位宽写死。

    AXI4 允许 ID 任意位宽（常见 4/8/16 位），`awlen` 也可窄到 4 位（≤16 beats）。
    旧模板写死 `awid/bid/arid/rid` = [3:0]、`awlen/arlen` = [7:0]，DUT 一不同宽
    就是 WIDTHEXPAND / WIDTHTRUNC —— Verilator 默认警告即错误，构建失败却返回
    ok=True（实测 8 位 ID：4 条 WIDTHEXPAND；4 位 len：2 条 WIDTHTRUNC）。

    同时测试向量必须能装进实际位宽：旧模板直接把 `4'd9` / `16'h0200` /
    `32'hA000_0000` 写死，窄 ID / 窄地址上会变成 WIDTHDIGITS。
    """
    print("coverage: AXI4 template follows ID/len/size/burst/resp widths")
    from fpga_toolbox.protocol_tb_emitter import _pw

    iface = {"port_details": {
        "awid": {"port": "s_axi_awid", "direction": "input", "width": 8},
        "awlen": {"port": "s_axi_awlen", "direction": "input", "width": 4},
        "awsize": {"port": "s_axi_awsize", "direction": "input", "width": 3},
        "awburst": {"port": "s_axi_awburst", "direction": "input", "width": 2},
        "bresp": {"port": "s_axi_bresp", "direction": "output", "width": 2},
        "wdata": {"port": "s_axi_wdata", "direction": "input", "width": 64},
        "awaddr": {"port": "s_axi_awaddr", "direction": "input", "width": 12},
    }}
    assert _pw(iface, "awid", 4) == 8
    assert _pw(iface, "awlen", 8) == 4
    assert _pw(iface, "wdata", 32) == 64
    assert _pw(iface, "awaddr", 16) == 12

    def _emit(id_w: int, len_w: int, aw: int, dw: int) -> str:
        rtl = f"""`timescale 1ns/1ps
module axi4_w (
  input  logic           aclk,
  input  logic           aresetn,
  input  logic [{id_w - 1}:0] s_axi_awid,
  input  logic [{aw - 1}:0]   s_axi_awaddr,
  input  logic [{len_w - 1}:0] s_axi_awlen,
  input  logic [2:0]     s_axi_awsize,
  input  logic [1:0]     s_axi_awburst,
  input  logic           s_axi_awvalid,
  output logic           s_axi_awready,
  input  logic [{dw - 1}:0]   s_axi_wdata,
  input  logic [{max(1, dw // 8) - 1}:0] s_axi_wstrb,
  input  logic           s_axi_wlast,
  input  logic           s_axi_wvalid,
  output logic           s_axi_wready,
  output logic [{id_w - 1}:0] s_axi_bid,
  output logic [1:0]     s_axi_bresp,
  output logic           s_axi_bvalid,
  input  logic           s_axi_bready,
  input  logic [{id_w - 1}:0] s_axi_arid,
  input  logic [{aw - 1}:0]   s_axi_araddr,
  input  logic [{len_w - 1}:0] s_axi_arlen,
  input  logic [2:0]     s_axi_arsize,
  input  logic [1:0]     s_axi_arburst,
  input  logic           s_axi_arvalid,
  output logic           s_axi_arready,
  output logic [{id_w - 1}:0] s_axi_rid,
  output logic [{dw - 1}:0]   s_axi_rdata,
  output logic [1:0]     s_axi_rresp,
  output logic           s_axi_rlast,
  output logic           s_axi_rvalid,
  input  logic           s_axi_rready
);
  assign s_axi_awready = 1; assign s_axi_wready = 1; assign s_axi_arready = 1;
  assign s_axi_bid = s_axi_awid; assign s_axi_bresp = 2'b00; assign s_axi_bvalid = 1'b1;
  assign s_axi_rid = s_axi_arid; assign s_axi_rdata = '0; assign s_axi_rresp = 2'b00;
  assign s_axi_rlast = 1'b1; assign s_axi_rvalid = 1'b1;
endmodule
"""
        with tempfile.TemporaryDirectory() as td:
            p_ = Path(td) / "dut.sv"
            p_.write_text(rtl, encoding="utf-8")
            r = call_tool("emit_testbench", {
                "rtl_file": str(p_), "out_dir": str(Path(td) / "out"), "protocol": "axi4",
            })
            assert r["ok"], str(r.get("error"))[:300]
            return Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")

    # 8 位 ID + 4 位 len + 12 位地址 + 64 位数据：网线必须全部跟随
    tb = _emit(id_w=8, len_w=4, aw=12, dw=64)
    # 用「声明行 + 名字」匹配，不依赖模板里的对齐空格数
    def _decl(name: str) -> str:
        for ln in tb.splitlines():
            if ln.strip().startswith("logic") and name in ln and "=" in ln or (
                    ln.strip().startswith("logic") and name in ln):
                return ln
        return ""

    assert "[7:0]" in _decl("s_axi_awid"), _decl("s_axi_awid")
    assert "[7:0]" in _decl("s_axi_bid"), _decl("s_axi_bid")
    assert "[3:0]" in _decl("s_axi_awlen"), _decl("s_axi_awlen")
    assert "[11:0]" in _decl("s_axi_awaddr"), _decl("s_axi_awaddr")
    assert "[63:0]" in _decl("s_axi_wdata"), _decl("s_axi_wdata")
    # 旧模板的写死值不能残留
    assert "[3:0]" not in _decl("s_axi_awid"), _decl("s_axi_awid")
    assert "[7:0]" not in _decl("s_axi_awlen"), _decl("s_axi_awlen")
    # 测试向量按位宽缩放：ID 不能再是 4'd9，地址不能超出 12 位
    assert "16'h0200" not in tb, "地址常量未按 aw 缩放"
    assert "4'd9" not in tb, "ID 常量未按 id_w 缩放"
    # 6 个检查（含 SLVERR 用例）必须保留，不能因为参数化而砍掉覆盖
    assert tb.count("axi_wr_burst(") + tb.count("axi_rd_burst(") >= 6, "测试序列被削减"
    assert "SLVERR" in tb or "chk_resp" in tb, "resp 检查被丢弃"


def test_power_on_reset_names_detected() -> None:
    """Defect 112: `por_n` 这类 power-on-reset 端口名不被识别为复位。

    `por_n` 是工业界非常标准的 POR 端口名。仓库自带的
    `examples/multi_bus_ref/omni_hub/soc_omni_bus_hub.sv`（6 协议集成 DUT）就用
    `if (!por_n)`，且它是**唯一**复位 —— 但旧 `_RST_RE` 只认
    `rst/reset/nreset/nrst` 家族，`por` 完全不在其中。

    后果不是"少一条 warning"：`emit_testbench` 的每条协议模板都硬连复位脚，
    检测不到复位就直接拒绝生成 TB，于是一个 6 协议 DUT 的**所有协议路径**
    （pcie/ddr4/axi4/apb...）全部被挡掉，报错还是误导性的
    "no reset port detected in the RTL. Add a reset port
    (rst/rst_n/aresetn/hresetn/...)" —— 用户明明写了 `por_n`。

    修复：`_RST_RE` 增加 por / poreset / power_on_reset / pon 家族（含 `n` 前缀
    与 `_n`/`n`/`b` 后缀），`_reset_active_low` 同步覆盖（否则 `por_n` 会被判成
    高有效，生成 TB 的复位时序反相）。同时必须**不**误伤 `port` / `ponder` /
    `por_ready` / `por_cnt` 这类以 por 开头的普通信号（锚定 + 尾部可选后缀）。
    """
    print("coverage: power-on-reset names (por_n / npor / poresetn) are recognised")
    from fpga_toolbox.iface_extractor import _is_reset, _reset_active_low

    # 必须识别为复位，且极性正确
    must_detect = {
        "por_n": True, "por": False, "npor": True, "npor_n": True,
        "poresetn": True, "nporeset": True, "pon_n": True, "npon": True,
        "power_on_reset_n": True, "power_on_reset": False, "por_rst_n": True,
        # 既有家族不能回归
        "rst_n": True, "rst": False, "reset": False, "resetn": True,
        "s_axi_aresetn": True, "hresetn": True, "presetn": True,
        "arst_n": True, "nrst": True, "nreset": True, "resetb": True,
    }
    for name, want_low in must_detect.items():
        assert _is_reset(name), f"{name} must be detected as a reset"
        assert _reset_active_low(name) is want_low, (
            f"{name}: active_low={_reset_active_low(name)} want={want_low}")

    # 不能误伤：以 por 开头但不是复位、或带 _done/_ready 后缀的派生信号
    must_not = [
        "port", "ponder", "por_ready", "por_cnt", "por_init_guard",
        "por_cke_mrs_zqcl", "power_on_reset_done", "reset_done",
        "clk", "supports", "report",
    ]
    for name in must_not:
        assert not _is_reset(name), f"{name} must NOT be detected as a reset"

    # 端到端：6 协议集成 DUT 的复位必须被识别出来（修复前 reset=None，
    # 导致 emit_testbench 对每种协议都拒绝生成）
    omni = ROOT / "examples" / "multi_bus_ref" / "omni_hub" / "soc_omni_bus_hub.sv"
    assert omni.is_file(), omni
    ex = call_tool("extract_interfaces", {"rtl_file": str(omni)})
    assert ex["ok"], str(ex.get("error"))[:200]
    mod = ex["data"]["modules"][0]
    assert mod["reset"] is not None, "omni hub reset not detected (por_n missed)"
    assert mod["reset"]["port"] == "por_n", mod["reset"]
    assert mod["reset"]["active_low"] is True, mod["reset"]

    # 且现在能真的生成 PCIe TB（修复前被 "no reset port detected" 挡掉）
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        r = call_tool("emit_testbench", {
            "rtl_file": str(omni),
            "out_dir": str(Path(td) / "out"), "protocol": "pcie",
        })
        assert r["ok"], f"emit still blocked on por_n: {str(r.get('error'))[:200]}"


def test_review_fixes_mutation_baseline_and_accounting() -> None:
    """Defect 113: run_mutation 无 baseline 预检 + ERROR 计入 survived 分母。

    旧实现只看「变异后 test_cmd 非零」就判 KILLED。若 test_cmd 本身是坏的
    （写错名字 / 缺二进制 / 测试台本来就失败），每个变异都以非零退出 → 全部
    KILLED → **假 100% 变异得分**（分数满格但一个故障都没验）。另外
    survived = total - killed 把 status="ERROR"（缺文件/越界）也算成存活并
    留在分母里，污染「测试台没杀掉的故障」这一语义。
    """
    print("coverage: run_mutation baseline precheck + ERROR/SKIP excluded from score")
    from fpga_toolbox.mutation_runner import tool_run_mutation

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "dut.sv").write_text("assign q = clk;\n", encoding="utf-8")
        (wd / "m.yaml").write_text(
            'mutations:\n  - id: M1\n    file: "dut.sv"\n'
            '    old: "assign q = clk;"\n    new: "assign q = 0;"\n', encoding="utf-8")

        # 1) baseline 就失败 => 必须 fail-fast，且一个字节都不能改
        r = tool_run_mutation(str(wd), str(wd / "m.yaml"), ["false"], min_score=0.0)
        assert not r["ok"], "broken test_cmd must not produce a mutation score"
        assert "Baseline" in r["error"], r["error"]
        assert (wd / "dut.sv").read_text(encoding="utf-8") == "assign q = clk;\n"

        # 2) 命令不存在 => 结构化错误而不是 FileNotFoundError 裸堆栈
        r2 = tool_run_mutation(str(wd), str(wd / "m.yaml"),
                               ["definitely_not_a_binary_xyz_113"], min_score=0.0)
        assert not r2["ok"] and "not executable" in r2["error"], r2.get("error")

        # 3) 正常路径：baseline 通过 -> 变异后检测命令失败 -> 真 KILLED
        r3 = tool_run_mutation(str(wd), str(wd / "m.yaml"),
                               ["grep", "-q", "assign q = clk;", "dut.sv"], min_score=0.0)
        assert r3["ok"], r3.get("error")
        d3 = r3["data"]
        assert d3["killed"] == 1 and d3["evaluated"] == 1 and d3["survived"] == 0
        assert d3["mutation_score"] == 100.0
        assert d3["baseline"]["returncode"] == 0
        assert (wd / "dut.sv").read_text(encoding="utf-8") == "assign q = clk;\n"

        # 4) ERROR 的变异不进分母：1 个合法变异存活 + 1 个缺文件
        (wd / "m2.yaml").write_text(
            'mutations:\n'
            '  - id: OK\n    file: "dut.sv"\n'
            '    old: "assign q = clk;"\n    new: "assign q = 0;"\n'
            '  - id: BAD\n    file: "nope.sv"\n'
            '    old: "x"\n    new: "y"\n', encoding="utf-8")
        r4 = tool_run_mutation(str(wd), str(wd / "m2.yaml"), ["true"], min_score=0.0)
        d4 = r4["data"]
        assert d4["total"] == 2 and d4["errors"] == 1 and d4["evaluated"] == 1, d4
        assert d4["survived"] == 1, d4
        assert d4["mutation_score"] == 0.0, d4

        # 5) 全 ERROR => 分数未定义，必须 ok=False（不是 0% 或 100%）
        (wd / "m3.yaml").write_text(
            'mutations:\n  - id: B1\n    file: "nope.sv"\n'
            '    old: "x"\n    new: "y"\n', encoding="utf-8")
        r5 = tool_run_mutation(str(wd), str(wd / "m3.yaml"), ["true"], min_score=0.0)
        assert not r5["ok"] and "No mutation could be evaluated" in r5["error"], r5.get("error")


def test_review_fixes_index_repo_and_linter_routing() -> None:
    """Defect 114: index_repo include 覆盖 + lint_tb 规则路由/前缀盲区/S4 字符串绕过。

    * index_repo：`include 让子模块出现在多个文件的解析结果里，旧实现两个都处理，
      后者用顶层文件源码算 instantiated_modules/includes，把顶层的例化与 include
      **覆盖**到子模块上（实测 datapath 被错标 includes=["datapath.v"]）。
    * lint_tb：S1 写在 scan_handshake 里（`rules=["silent_pass"]` 跳过它、
      `rules=["handshake"]` 反而报它）；`_IFACE_RE` 的 `[sm]_[ap]x[i]` 匹配不了
      s_apb/m_apb；R2/R4 写死 `axi.` 前缀，s_axi./m_axi./裸信号全部漏报；
      S4 用朴素 `//` 剥离且不剥字符串，`$display("no error(s)")` 冒充判定。
    """
    print("coverage: index_repo include ownership + lint_tb rule routing/prefix/string")
    from fpga_toolbox.repo_indexer import tool_index_repo
    from fpga_toolbox.tb_linter import tool_lint_tb

    # ---- index_repo：include 不得污染子模块 ----
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "rtl").mkdir()
        (root / "rtl" / "datapath.v").write_text(
            "module datapath(input logic a, output logic y);\nassign y = ~a;\nendmodule\n",
            encoding="utf-8")
        (root / "rtl" / "sub.v").write_text(
            "module sub(input logic a, output logic y);\nassign y = a;\nendmodule\n",
            encoding="utf-8")
        (root / "rtl" / "top.v").write_text(
            '`include "datapath.v"\n'
            "module top(input logic a, output logic y);\n"
            "  logic md;\n  datapath u_dp(.a(a), .y(md));\n  sub u_sub(.a(md), .y(y));\n"
            "endmodule\n", encoding="utf-8")
        r = tool_index_repo(str(root))
        assert r["ok"], r.get("error")
        mods = r["data"]["modules"]
        assert mods["datapath"]["file_path"] == "rtl/datapath.v", mods["datapath"]
        assert mods["datapath"]["instantiated_modules"] == [], mods["datapath"]
        assert mods["datapath"]["includes"] == [], mods["datapath"]
        assert mods["top"]["instantiated_modules"] == ["datapath", "sub"], mods["top"]
        assert mods["top"]["includes"] == ["datapath.v"], mods["top"]

    # ---- lint_tb：S1 必须路由到 silent_pass ----
    def _lint(src: str, rules, suffix=".sv") -> list:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / f"tb{suffix}"
            f.write_text(src, encoding="utf-8")
            res = tool_lint_tb(str(f), rules=rules)
            return sorted({i["code"] for i in res["data"]["findings"]})

    stub = "module tb;\nfunction int chk();\nreturn 0;\nendfunction\nendmodule\n"
    assert "S1" in _lint(stub, ["silent_pass"]), "S1 missed when scanning silent_pass only"
    assert "S1" not in _lint(stub, ["handshake"]), "S1 wrongly emitted by handshake scanner"

    # ---- lint_tb：R2/R4 必须覆盖 s_axi./m_axi./裸信号 ----
    for sig in ("axi", "s_axi", "m_axi"):
        r4 = _lint(f"module tb;\ninitial begin\nwhile (!{sig}.bvalid) @(posedge clk);\n"
                   f"{sig}.bready <= 1'b0;\nend\nendmodule\n", ["handshake"])
        assert "R4" in r4, (sig, r4)
        r2 = _lint(f"module tb;\nalways @(posedge clk) begin\nif ({sig}.bvalid) begin\n"
                   f"{sig}.bready <= 1'b0;\nend\nend\nendmodule\n", ["handshake"])
        assert "R2" in r2, (sig, r2)

    # ---- lint_tb：S4 不能被字符串里的 "error(" 绕过 ----
    bypass = ('module tb;\ninitial begin\n'
              '$display("no error(s)");\n$finish;\nend\nendmodule\n')
    assert "S4" in _lint(bypass, ["silent_pass"]), "string literal bypassed S4"
    judged = ('module tb;\ninitial begin\n'
              'if (1) $display("x"); else $error("bad");\n$finish;\nend\nendmodule\n')
    assert "S4" not in _lint(judged, ["silent_pass"]), "real judge wrongly flagged S4"


def test_review_fixes_registry_parser_and_types() -> None:
    """Defect 115: call_tool 的 None/enum/bool 处理与 sv_parser 的整数类型宽度。

    * `{"protocol": None}`（MCP 客户端把省略的可选字段序列化成 null）被 enum
      校验拒成"非法枚举值"，而类型校验明确写了「显式 None 交给函数处理」。
    * `{"out_dir": None}` 同时通过类型校验（None 跳过）和 required 校验（key 存在），
      把 None 漏进函数体，报出与参数无关的下游错误。
    * `"number"` 类型接受 bool（isinstance(True, (int, float)) 为真）。
    * call_tool 原地改写调用方 dict（boolean 字符串强转）。
    * sv_parser：byte/shortint/int/longint 被当成 1 位；`$clog2(1)` 返回 1（应为 0）。
    """
    print("coverage: call_tool None/enum/bool + sv_parser int-type widths and $clog2")
    from fpga_toolbox.core.sv_parser import eval_int_expr, parse_sv_file

    # ---- call_tool ----
    r = call_tool("emit_testbench", {"rtl_file": "x.sv", "out_dir": "/tmp/o", "protocol": None})
    assert "Invalid value None" not in str(r.get("error")), str(r["error"])[:160]

    r = call_tool("emit_testbench", {"rtl_file": "x.sv", "out_dir": None, "protocol": "apb"})
    assert not r["ok"] and "out_dir" in r["error"] and "required" in r["error"], r.get("error")

    r = call_tool("run_mutation", {"workdir": "/tmp", "mutation_spec": "/tmp/x",
                                   "test_cmd": ["true"], "min_score": True})
    assert not r["ok"] and "boolean" in r["error"], r.get("error")

    args = {"rtl_file": "x.sv", "out_dir": "/tmp/o", "protocol": "apb"}
    snapshot = dict(args)
    call_tool("emit_testbench", args)
    assert args == snapshot, f"call_tool mutated caller dict: {args}"

    # ---- sv_parser 整数类型宽度 ----
    src = ("module intty(\n  input logic clk,\n  output int o1,\n  output byte o2,\n"
           "  output shortint o3,\n  output longint o4\n);\nendmodule\n")
    want = {"o1": 32, "o2": 8, "o3": 16, "o4": 64}
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(src, encoding="utf-8")
        for backend in ("builtin", "slang"):
            mods = parse_sv_file(f, backend=backend)
            if not mods:
                continue
            got = {p.name: p.width for p in mods[0].ports}
            for k, v in want.items():
                assert got.get(k) == v, (backend, k, got)

    # ---- $clog2 边界（IEEE 1800：clog2(0)=clog2(1)=0）----
    for n, exp_v in ((0, 0), (1, 0), (2, 1), (3, 2), (4, 2), (8, 3), (9, 4), (16, 4), (17, 5)):
        assert eval_int_expr(f"$clog2({n})", {}) == exp_v, (n, eval_int_expr(f"$clog2({n})", {}))



def test_review_fixes_artifact_escaping_and_polarity() -> None:
    """Defect 116: 生成产物里的插值未转义 + 复位极性靠脆弱字符串 replace。

    * `_emit_cocotb` 用 `json.loads(r` + 三引号 + `{regs}` + 三引号 + `)` 嵌入契约 JSON：寄存器描述里
      只要出现三引号（连续三个双引号）或结尾反斜杠，生成的 cocotb 测试台就是
      SyntaxError，
      而工具已报 ok=True。
    * `_emit_wrap` 把 `os.environ["WAVE_FILE"]` 原文插进 `$dumpfile("...")`：
      Windows 路径 `C:\tmp\w.vcd`（含反斜杠）或含引号的路径会生成非法 SV。
    * active-high 复位靠两处 `tb_src.replace(", rst = 0", ...)` 打补丁，
      模板缩进一变就静默匹配不到 —— TB 带着反相复位跑，表现为莫名 FAIL/超时。
    """
    print("coverage: artifact escaping (cocotb JSON / WAVE_FILE) + reset polarity")

    pm = {k: f"s_axil_{k}" for k in (
        "awaddr", "awvalid", "awready", "wdata", "wstrb", "wvalid", "wready",
        "bresp", "bvalid", "bready", "araddr", "arvalid", "arready",
        "rdata", "rresp", "rvalid", "rready")}

    def _emit_contract(regs: list) -> dict:
        spec = {
            "schema_version": "1.0",
            "boundary": {"module_name": "m", "clock": {"port": "clk"},
                         "reset": {"port": "rst_n"}, "axi4_lite": {"port_map": pm}},
            "test_plan": {"register_rules": regs},
        }
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "s.json"
            sp.write_text(json.dumps(spec), encoding="utf-8")
            r = call_tool("emit_testbench", {"spec_path": str(sp), "out_dir": str(Path(td) / "out")})
            arts = {}
            if r["ok"]:
                for k, v in r["data"]["artifacts"].items():
                    p_ = Path(v)
                    arts[k] = p_.read_text(encoding="utf-8") if p_.is_file() else ""
            return r, arts

    # 1) 描述里含三引号 + 结尾反斜杠：cocotb 源码必须仍是合法 Python
    r, arts = _emit_contract([
        {"rule_id": "R1", "name": "CTRL", "offset": 0, "access": "RW",
         "reset_value": 0, "description": 'evil """ quote'},
        {"rule_id": "R2", "name": "BACK", "offset": 4, "access": "RW",
         "reset_value": 0, "description": "ends with backslash \\"},
    ])
    assert r["ok"], str(r.get("error"))[:200]
    import ast as _ast
    _ast.parse(arts["cocotb_testbench"])  # SyntaxError 会直接让测试失败
    assert "json.loads(" in arts["cocotb_testbench"]

    # 2) WAVE_FILE 含反斜杠/引号：$dumpfile 的参数必须是合法 SV 字符串
    import os as _os
    _old = _os.environ.get("WAVE_FILE")
    try:
        _os.environ["WAVE_FILE"] = 'C:\\tmp\\w"x.vcd'
        _r2, _arts2 = _emit_contract(
            [{"rule_id": "R1", "name": "CTRL", "offset": 0, "access": "RW", "reset_value": 0}])
        assert _r2["ok"], str(_r2.get("error"))[:200]
        wrap = _arts2["unit_isolation_wrapper"]
        dump_lines = [ln.strip() for ln in wrap.splitlines() if "$dumpfile(" in ln]
        assert dump_lines, wrap[:400]
        for ln in dump_lines:
            if ln.startswith('$dumpfile("'):
                # 引号必须成对且中间没有未转义的裸引号/反斜杠。
                # 合法形态形如 $dumpfile("C:/tmp/wx.vcd"); —— 剥掉首尾的引号后
                # payload 里不能还有裸引号或反斜杠。
                payload = ln[len('$dumpfile("'):]
                payload = payload[:payload.rindex('"')]
                assert '"' not in payload, ln
                assert "\\" not in payload, ln
    finally:
        if _old is None:
            _os.environ.pop("WAVE_FILE", None)
        else:
            _os.environ["WAVE_FILE"] = _old

    # 3) active-high 复位：生成的 TB 必须「声明 =1 / 释放 =0」，且能真跑通
    rtl = (
        "`timescale 1ns/1ps\n"
        "module apb_ah (\n"
        "  input logic pclk, input logic preset,\n"
        + "".join(f"  input logic {s_},\n" for s_ in ("psel", "penable", "pwrite"))
        + "  input logic [7:0] paddr, input logic [31:0] pwdata,\n"
        "  input logic [3:0] pstrb,\n"
        "  output logic pready,\n  output logic [31:0] prdata,\n  output logic pslverr\n"
        ");\n"
        "  logic [31:0] r;\n"
        "  always_ff @(posedge pclk) begin\n"
        "    if (preset) r <= 32'h0;\n"
        "    else if (psel && penable && pwrite) r <= pwdata;\n"
        "  end\n"
        "  assign prdata = r; assign pready = 1'b1; assign pslverr = 1'b0;\n"
        "endmodule\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(rtl, encoding="utf-8")
        r3 = call_tool("emit_testbench", {
            "rtl_file": str(f), "out_dir": str(Path(td) / "out"), "protocol": "apb"})
        assert r3["ok"], str(r3.get("error"))[:300]
        tb = Path(r3["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        # 声明处：, preset = 1（断言态）；释放处：preset = 0;
        assert "preset = 1" in tb, tb[:600]
        assert "preset = 0;" in tb, tb[:600]


def test_suite_identity_requires_per_suite_summary() -> None:
    """Defect 117: 套件「打印过 PASS 之后崩溃」仍被判 PASS（两处静默通过）。

    正则/计数法都只看「日志里出现过 PASS」：

    1. `sim_runner.verify_suite_identities` 对任意含 `\bPASS\b` 的行判该套件
       PASS。套件打印 `[CHECK] id=C1 | status=PASS` 后在 `$finish` 之前崩溃
       （断言挂 / 段错误 / 超时被杀），仍被判 PASS。
    2. `result_normalizer` 的完成度只看**汇总行总数** >= 启动套件数。A 打印两条
       `PASS:`、B 崩溃一条都没有时，总数仍够 —— 实测 overall_status=PASS。

    正确口径：每个**启动过**的套件都必须有自己的 `PASS:` 结束汇总行；只有
    STARTED 没有汇总行 = 中途崩溃 = FAIL。
    """
    print("coverage: suite identity needs per-suite PASS summary (crash is not PASS)")
    from fpga_toolbox.result_normalizer import tool_normalize_sim_result
    from fpga_toolbox.sim_runner import verify_suite_identities

    # 1) 只在 CHECK 行里出现过 PASS、随后崩溃 => 不能算 PASS
    crash_log = ("== sim: apb (top=tb) ==\n"
                 "[CHECK] id=C1 | status=PASS\n%Error: Assertion failed\n")
    r = verify_suite_identities(crash_log, ["apb"])
    assert r["observed"].get("apb") == "FAIL", r
    assert r["all_verified"] is False, r

    # 正常跑完（有自身汇总行）=> PASS
    ok_log = ("== sim: apb (top=tb) ==\n"
              "[CHECK] id=C1 | status=PASS\nPASS: apb all 8 checks passed\n")
    r2 = verify_suite_identities(ok_log, ["apb"])
    assert r2["observed"].get("apb") == "PASS", r2
    assert r2["all_verified"] is True, r2

    # 2) 计数法绕过：A 两条 PASS、B 零条 => B 必须 FAIL
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "v.log"
        log.write_text(
            "== sim: A (top=t) ==\nPASS: a\nPASS: a duplicated\n"
            "[CHECK] id=A1 | status=PASS\n"
            "== sim: B (top=t) ==\n[CHECK] id=B1 | status=PASS\n%Error crash\n",
            encoding="utf-8")
        nr = tool_normalize_sim_result("m", verilator_log_path=str(log),
                                       expected_suites=["A", "B"])
        d = nr["data"]
        assert d["overall_status"] == "FAIL", d["overall_status"]
        assert any(c["rule_id"] == "VERILATOR_NO_SUMMARY" and c["status"] == "FAIL"
                   for c in d["checks"]), [c["rule_id"] for c in d["checks"]]

        # 两个套件都各自有汇总行 => 仍然是 PASS（不能因为加严而误伤）
        good = Path(td) / "good.log"
        good.write_text(
            "== sim: A (top=t) ==\n[CHECK] id=A1 | status=PASS\nPASS: A ok\n"
            "== sim: B (top=t) ==\n[CHECK] id=B1 | status=PASS\nPASS: B ok\n",
            encoding="utf-8")
        ng = tool_normalize_sim_result("m", verilator_log_path=str(good),
                                       expected_suites=["A", "B"])
        assert ng["data"]["overall_status"] == "PASS", ng["data"]["checks"]
        assert not any(c["rule_id"] == "VERILATOR_NO_SUMMARY"
                       for c in ng["data"]["checks"])


def test_mutation_rejects_empty_old_pattern() -> None:
    """Defect 118: 变异 spec 的 `old: ""` 被当作"匹配任意位置"静默执行。

    `b"" in bytes` 恒为真，`original.replace(b"", new, 1)` 会把 new 插到文件
    **开头**（实测）——变异"成功"、进入分母影响得分，却没有任何语义。这不是
    "匹配到所有位置"，是 spec 写错了，必须显式拒绝且不动文件。
    """
    print("coverage: run_mutation rejects empty 'old' pattern")
    from fpga_toolbox.mutation_runner import tool_run_mutation

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "a.txt").write_text("hello world\n", encoding="utf-8")
        (wd / "m.json").write_text(json.dumps({"mutations": [
            {"id": "E", "file": "a.txt", "old": "", "new": "ZZ", "desc": "empty"}]}),
            encoding="utf-8")
        r = tool_run_mutation(str(wd), str(wd / "m.json"), ["true"], min_score=0.0)
        assert not r["ok"], "empty 'old' must not produce a score"
        assert "No mutation could be evaluated" in r["error"], r["error"]
        res = r["data"]["results"][0]
        assert res["status"] == "ERROR" and "empty" in res["detail"].lower(), res
        assert (wd / "a.txt").read_text(encoding="utf-8") == "hello world\n"


def test_normalizer_rejects_malformed_and_unnamed_results() -> None:
    """Defect 119: normalize_sim_result 对畸形结果「静默 NO_RESULTS」或裸 AttributeError。

    模块 docstring 承诺 "Unrecognized input is reported as an error instead of
    silently yielding an empty PASS/NO_RESULTS report"，但旧实现有三处违背：

    1. `checks: [str/None]` → `c.get(...)` 抛 `AttributeError: 'str' object has
       no attribute 'get'`，用户看不出是 checks 数组混进了非对象。
    2. `tests: [str/None/42]` → 被 `if isinstance(t, dict)` **静默过滤**，
       变成 0 条检查 → summary.total=0 → overall_status=NO_RESULTS 且
       **ok=True**。调用方看到「成功 + 零检查」，真相是文件根本无法解析。
    3. JUnit XML 里既无 name 也无 classname 的 `<testcase/>`（零证据）被记成
       rule_id="UNKNOWN"、status=PASS → 整份报告 overall_status=PASS，
       比「零 testcase」（正确判 NO_RESULTS）还宽松。
    """
    print("coverage: normalize_sim_result rejects malformed items and unnamed testcases")
    from fpga_toolbox.result_normalizer import tool_normalize_sim_result

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # 1) checks 里混入非对象 => 可读错误，不是 AttributeError 裸文案
        for bad in (["oops"], [None], [{"rule_id": "R1"}, "x"]):
            f = base / f"c{abs(hash(str(bad)))}.json"
            f.write_text(json.dumps({"checks": bad}), encoding="utf-8")
            r = tool_normalize_sim_result("m", cocotb_results_json=str(f))
            assert r["ok"] is False, (bad, r.get("data"))
            err = str(r["error"])
            assert "must be a list of objects" in err, (bad, err[:200])
            assert "attribute 'get'" not in err, (bad, err[:200])

        # 2) tests 里混入非对象 => 报错，不能静默变成 NO_RESULTS + ok=True
        f = base / "t.json"
        f.write_text(json.dumps({"tests": ["oops", None, 42]}), encoding="utf-8")
        r = tool_normalize_sim_result("m", cocotb_results_json=str(f))
        assert not r["ok"], f"all-invalid tests silently passed: {r.get('data')}"
        assert "must be a list of objects" in str(r["error"]), str(r["error"])[:200]

        # 3) 无名 testcase => FAIL（零证据不算通过），而不是 PASS
        xml = base / "u.xml"
        xml.write_text('<?xml version="1.0"?><testsuites><testsuite>'
                       '<testcase/></testsuite></testsuites>', encoding="utf-8")
        r = tool_normalize_sim_result("m", cocotb_results_json=str(xml))
        assert r["data"]["overall_status"] == "FAIL", r["data"]["checks"]
        assert any(c["rule_id"] == "UNNAMED_TESTCASE" and c["status"] == "FAIL"
                   for c in r["data"]["checks"]), r["data"]["checks"]

        # 正常 JUnit（有名字、无 failure）仍 PASS —— 不能过度收紧
        ok_xml = base / "ok.xml"
        ok_xml.write_text('<?xml version="1.0"?><testsuites><testsuite>'
                          '<testcase name="T_OK"/></testsuite></testsuites>',
                          encoding="utf-8")
        r = tool_normalize_sim_result("m", cocotb_results_json=str(ok_xml))
        assert r["data"]["overall_status"] == "PASS", r["data"]["checks"]

        # 4) engine 版本不再写死：从日志抓真实版本，抓不到留空
        log = base / "v.log"
        log.write_text(
            "== sim: apb (top=t) ==\n"
            "[CHECK] id=A1 | proto=APB | exp=0x1 | got=0x1 | status=PASS\n"
            "- Verilator 5.020 2024-01-01 rev (Debian 5.020-1)\n"
            "PASS: apb ok\n", encoding="utf-8")
        r = tool_normalize_sim_result("m", verilator_log_path=str(log),
                                      expected_suites=["apb"])
        engines = {c.get("engine") for c in r["data"]["checks"] if c.get("engine")}
        assert engines == {"Verilator 5.020"}, engines


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


def test_ddr_required_ports_cover_dq_in() -> None:
    """Defect 121: DDR3/DDR4 模板硬连 `*_dq_in`，但 REQUIRED_TB_PORTS 漏了这一项。

    模板对 DUT 是双向语义：`dq_in` 由 TB 驱动（写入数据）、`dq_out` 由 DUT 驱动
    （读回数据）。旧必需端口表只列了 `dq_out`，于是**没有 dq_in 端口**的 DDR3
    DUT 通过了生成前校验，产出的 TB 带 `.ddr3_dq_in(...)` 连线：

        %Error-PINNOTFOUND: tb.sv:17:29: Pin not found: 'ddr3_dq_in'
        %Error: Exiting due to 1 error(s)

    而 `emit_testbench` 返回 ok=True —— DUT 一点没验到。

    另外加了一条结构自检 A19：从模板源码抽出**同名成对**的 `.foo(foo)` 连线，
    与 REQUIRED_TB_PORTS 做差集，保证「模板加连线而忘了同步表格」以后会被抓到
    （已验证：临时删掉表格里的 ddr3_dq_in，A19 立刻报
    `ddr3: template connects ['ddr3_dq_in'] not in REQUIRED_TB_PORTS`）。
    """
    print("coverage: DDR3/DDR4 required-port table covers dq_in (template really drives it)")
    from fpga_toolbox.protocol_tb_emitter import REQUIRED_TB_PORTS

    for proto, in_port, out_port in (("ddr3", "ddr3_dq_in", "ddr3_dq_out"),
                                     ("ddr4", "ddr4_dq_in", "ddr4_dq_out")):
        req = set(REQUIRED_TB_PORTS[proto])
        assert in_port in req, (proto, sorted(req))
        assert out_port in req, (proto, sorted(req))

    # 端到端：缺 dq_in 的 DUT 必须被拒，且错误里点名该端口
    ddr3_rtl = """`timescale 1ns/1ps
module ddr3_no_dqin (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        ddr3_ck_p,
  input  logic        ddr3_ck_n,
  output logic        ddr3_cke,
  output logic        ddr3_cs_n,
  output logic        ddr3_ras_n,
  output logic        ddr3_cas_n,
  output logic        ddr3_we_n,
  output logic [2:0]  ddr3_ba,
  output logic [13:0] ddr3_addr,
  output logic [15:0] ddr3_dq_out,
  output logic        ddr3_rd_valid
);
  assign ddr3_cke = 1; assign ddr3_cs_n = 1; assign ddr3_ras_n = 1;
  assign ddr3_cas_n = 1; assign ddr3_we_n = 1; assign ddr3_ba = '0;
  assign ddr3_addr = '0; assign ddr3_dq_out = '0; assign ddr3_rd_valid = 1'b1;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        rtl = Path(td) / "dut.sv"
        rtl.write_text(ddr3_rtl, encoding="utf-8")
        r = call_tool("emit_testbench", {
            "rtl_file": str(rtl), "out_dir": str(Path(td) / "out"), "protocol": "ddr3"})
        assert r["ok"] is False, "DUT without ddr3_dq_in must be rejected"
        assert "ddr3_dq_in" in str(r["error"]), str(r["error"])[:200]


def test_strobe_width_follows_real_port() -> None:
    """Defect 122: APB `pstrb` / AXI4 `wstrb` 位宽被推导成 `dw//8` 而非取真实端口。

    规范并不要求 strobe 位宽 == DATA/8。实测一个**合法**的 APB 从设备用
    16 位 `pwdata` + 4 位 `pstrb`（按字节方案），`extract_interfaces` 也如实
    报告 `data_width=16` 与 `pstrb.width=4`，但 TB 模板按 `16//8=2` 生成：

        logic [1:0] pstrb = '1;
        %Warning-WIDTHEXPAND: Input port connection 'pstrb' expects 4 bits
                              ... generates 2 bits
        %Error: Exiting due to 4 warning(s)

    Verilator 默认警告即错误 → 构建失败，而 `emit_testbench` 返回 ok=True。

    修复：strobe 位宽一律 `_pw(iface, "pstrb"/"wstrb", dw//8)` —— 真实端口位宽
    优先，`dw//8` 只作兜底。自审计 A19 同时检查这两个信号确实走了 `_pw`。
    """
    print("coverage: APB pstrb / AXI4 wstrb width follows the real port")
    from fpga_toolbox.protocol_tb_emitter import _pw

    # _pw 语义：port_details 优先，其次 addr/data_width，最后默认值
    assert _pw({"port_details": {"pstrb": {"width": 4}}}, "pstrb", 2) == 4
    assert _pw({"port_details": {"wstrb": {"width": 16}}}, "wstrb", 4) == 16
    assert _pw({}, "pstrb", 2) == 2

    # 端到端：16 位数据 + 4 位 pstrb 的 APB DUT，TB 网线必须是 4 位
    rtl = """`timescale 1ns/1ps
module apb_wide_strobe (
  input  logic        pclk,
  input  logic        presetn,
  input  logic        psel,
  input  logic        penable,
  input  logic        pwrite,
  input  logic [9:0]  paddr,
  input  logic [15:0] pwdata,
  input  logic [3:0]  pstrb,
  output logic        pready,
  output logic [15:0] prdata,
  output logic        pslverr
);
  assign pready = 1'b1;
  assign prdata = pwdata;
  assign pslverr = 1'b0;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(rtl, encoding="utf-8")
        r = call_tool("emit_testbench", {
            "rtl_file": str(f), "out_dir": str(Path(td) / "out"), "protocol": "apb"})
        assert r["ok"], str(r.get("error"))[:300]
        tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        decl = next(ln for ln in tb.splitlines() if "pstrb" in ln and ln.strip().startswith("logic"))
        assert "[3:0]" in decl, decl
        assert "[1:0]" not in decl, decl
        # 地址/数据网线也要取真实端口位宽
        paddr_decl = next(ln for ln in tb.splitlines()
                          if "paddr" in ln and ln.strip().startswith("logic"))
        assert "[9:0]" in paddr_decl, paddr_decl
        pwdata_decl = next(ln for ln in tb.splitlines()
                           if "pwdata" in ln and ln.strip().startswith("logic"))
        assert "[15:0]" in pwdata_decl, pwdata_decl


def test_ahb_trans_size_resp_widths_follow_port() -> None:
    """Defect 123: AHB `htrans` / `hsize` / `hresp` 位宽写死，不跟真实端口。

    AHB-Lite 规范给的是 HTRANS=2 / HSIZE=3 / HRESP=1 位，但真实 IP 常扩展它
    （例如用 2 位 HRESP 表达 SPLIT/RETRY）。旧模板写死：

        logic HREADYOUT, HRESP;          <- HRESP 当成 1 位标量
        logic [1:0] HTRANS = 0;
        logic [2:0] HSIZE = 3'd2;

    DUT 用 2 位 HRESP 时连线报：

        %Warning-WIDTHEXPAND: Output port connection 'HRESP' expects 2 bits
                              ... generates 1 bits
        %Error: Exiting due to N warning(s)

    Verilator 警告即错误 → 构建失败，而工具返回 ok=True。

    修复：htrans_w / hsize_w / hresp_w 全部由 `_pw()` 取真实端口位宽；task 形参
    与所有调用点的字面量同步改成 `{hresp_w}'dN`（`1'b0` 在 2 位形参上同样
    WIDTHEXPAND）；`!exp_err` 改成 `exp_err == 0`（LOGNOT 只接受 1 位）。
    """
    print("coverage: AHB htrans/hsize/hresp widths follow real ports")
    from fpga_toolbox.protocol_tb_emitter import _pw

    assert _pw({"port_details": {"hresp": {"width": 2}}}, "hresp", 1) == 2
    assert _pw({"port_details": {"htrans": {"width": 3}}}, "htrans", 2) == 3
    assert _pw({"port_details": {"hsize": {"width": 4}}}, "hsize", 3) == 4

    rtl = """`timescale 1ns/1ps
module ahb_wide (
  input  logic          HCLK,
  input  logic          HRESETn,
  input  logic          HSEL,
  input  logic [15:0]   HADDR,
  input  logic [1:0]    HTRANS,
  input  logic          HWRITE,
  input  logic [2:0]    HSIZE,
  input  logic [31:0]   HWDATA,
  output logic          HREADYOUT,
  output logic [31:0]   HRDATA,
  output logic [1:0]    HRESP
);
  assign HREADYOUT = 1'b1;
  assign HRDATA = HWDATA;
  assign HRESP = 2'b00;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(rtl, encoding="utf-8")
        r = call_tool("emit_testbench", {
            "rtl_file": str(f), "out_dir": str(Path(td) / "out"), "protocol": "ahb_lite"})
        assert r["ok"], str(r.get("error"))[:300]
        tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        # HRESP 必须声明为 2 位，而不是 1 位标量
        resp_decl = next(ln for ln in tb.splitlines()
                         if "HRESP" in ln and ln.strip().startswith("logic"))
        assert "[1:0]" in resp_decl, resp_decl
        # 所有 exp_err 实参必须是 2 位字面量，不能是 1'b0/1'b1
        call_lines = [ln for ln in tb.splitlines() if ln.strip().startswith(("ahb_read", "ahb_write"))]
        assert call_lines, "no AHB task calls generated"
        for ln in call_lines:
            assert "1'b0" not in ln and "1'b1" not in ln, ln
            assert "2'd" in ln, ln
        # 多位 exp_err 上不能再用 LOGNOT
        assert "!exp_err" not in tb, "LOGNOT on multi-bit exp_err"
        assert "exp_err == 0" in tb, "multi-bit zero test missing"

    # 参考 DUT（1 位 HRESP）仍然正常生成
    with tempfile.TemporaryDirectory() as td:
        r = call_tool("emit_testbench", {
            "rtl_file": str(ROOT / "examples" / "multi_bus_ref" / "ahb_lite_slave.sv"),
            "out_dir": str(Path(td) / "out"), "protocol": "ahb_lite"})
        assert r["ok"], str(r.get("error"))[:300]
        tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        resp_decl = next(ln for ln in tb.splitlines()
                         if "HRESP" in ln and ln.strip().startswith("logic"))
        assert "[0:0]" in resp_decl, resp_decl


def test_ddr_control_widths_follow_port() -> None:
    """Defect 124: DDR3/DDR4 的 CKE/CS_n/RAS_n/CAS_n/WE_n/ODT/ACT_n 位宽写死 1 位。

    多 rank 的 DDR 控制器会用多位 CKE / CS_n（每 rank 一位）。旧模板把这一整组
    控制信号塞进同一个 `logic` 标量声明：

        logic clk = 0, rst_n = 0, ddr3_cke = 1, ddr3_cs_n = 1, ..., ddr3_rd_valid;

    DUT 用 2 位 `ddr3_cke` 时钟线报：

        %Warning-WIDTHEXPAND: Input port connection 'ddr3_cke' expects 2 bits
                              ... generates 1 bits
        %Error: Exiting due to 2 warning(s)

    Verilator 警告即错误 → 构建失败，而工具返回 ok=True。

    修复：cke/cs_n/ras_n/cas_n/we_n/rd_valid（DDR3）与
    act_n/cs_n/odt/rd_valid（DDR4）全部由 `_pw()` 取真实端口位宽；task 内的
    驱动改用 `'0`/`'1` 与按位赋值（多位 CS_n 时「全部置有效」语义不变）。
    """
    print("coverage: DDR3/DDR4 control signals follow real port widths")
    from fpga_toolbox.protocol_tb_emitter import _pw

    assert _pw({"port_details": {"cke": {"width": 2}}}, "cke", 1) == 2
    assert _pw({"port_details": {"cs_n": {"width": 4}}}, "cs_n", 1) == 4
    assert _pw({}, "cke", 1) == 1

    # DDR3：2 位 cke —— TB 网线必须是 [1:0]，且不能再出现在 1 位标量串里
    ddr3 = """`timescale 1ns/1ps
module ddr3_multi_rank (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        ddr3_ck_p,
  input  logic        ddr3_ck_n,
  input  logic [1:0]  ddr3_cke,
  input  logic        ddr3_cs_n,
  input  logic        ddr3_ras_n,
  input  logic        ddr3_cas_n,
  input  logic        ddr3_we_n,
  input  logic [2:0]  ddr3_ba,
  input  logic [13:0] ddr3_addr,
  input  logic [15:0] ddr3_dq_in,
  output logic [15:0] ddr3_dq_out,
  output logic        ddr3_rd_valid
);
  assign ddr3_dq_out = ddr3_dq_in;
  assign ddr3_rd_valid = 1'b1;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(ddr3, encoding="utf-8")
        r = call_tool("emit_testbench", {
            "rtl_file": str(f), "out_dir": str(Path(td) / "out"), "protocol": "ddr3"})
        assert r["ok"], str(r.get("error"))[:300]
        tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        cke_decl = next(ln for ln in tb.splitlines()
                        if "ddr3_cke" in ln and ln.strip().startswith("logic"))
        assert "[1:0]" in cke_decl, cke_decl
        # 不能还有把 cke 塞进 1 位标量声明串的旧形态
        assert "logic        clk = 0, rst_n = 0, ddr3_cke" not in tb

    # DDR4：2 位 cs_n
    ddr4 = """`timescale 1ns/1ps
module ddr4_multi_rank (
  input  logic         clk,
  input  logic         rst_n,
  input  logic         ddr4_ck_t,
  input  logic         ddr4_ck_c,
  input  logic         ddr4_act_n,
  input  logic [1:0]   ddr4_bg,
  input  logic [1:0]   ddr4_ba,
  input  logic [16:0]  ddr4_adr,
  input  logic [1:0]   ddr4_cs_n,
  input  logic         ddr4_odt,
  input  logic [63:0]  ddr4_dq_in,
  output logic [63:0]  ddr4_dq_out,
  input  logic [7:0]   ddr4_dm_dbi_n,
  output logic         ddr4_rd_valid
);
  assign ddr4_dq_out = ddr4_dq_in;
  assign ddr4_rd_valid = 1'b1;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(ddr4, encoding="utf-8")
        r = call_tool("emit_testbench", {
            "rtl_file": str(f), "out_dir": str(Path(td) / "out"), "protocol": "ddr4"})
        assert r["ok"], str(r.get("error"))[:300]
        tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        cs_decl = next(ln for ln in tb.splitlines()
                       if "ddr4_cs_n" in ln and ln.strip().startswith("logic"))
        assert "[1:0]" in cs_decl, cs_decl


def test_no_user_data_destruction_and_valid_cocotb_json() -> None:
    """Defect 126: 三处会毁用户数据 / 必崩的缺陷。

    1. `_emit_cocotb` 曾把 Python `repr()`（单引号）塞进 `json.loads(...)`：
       只要 spec 里有任何寄存器，生成的 cocotb 测试一导入就
       `JSONDecodeError: Expecting property name enclosed in double quotes`。
       （我上一轮为修「描述含三引号」把 `json.dumps` 改成了 `repr`，引入了这个
       回归 —— 正确做法是 `repr(json.dumps(...))`：repr 只负责安全嵌入，
       JSON 合法性由 json.dumps 保证。）
    2. `tool_emit_testbench` 无条件 `shutil.rmtree(od / "inc")` 再拷贝。
       当 `out_dir == rtl_src.parent` 时 `od/"inc"` 就是**用户自己的源码
       inc/ 目录** —— 实测 `inc/defs.svh` 被永久删除，工具报 ok=True。
    3. `emit_protocol_testbench` 没做输入/产物同名检查：用户把上次生成的
       `tb_<mod>_<proto>.sv` 改名后当 RTL 传回、且 out_dir 同目录时，生成的 TB
       直接覆盖用户 RTL，工具报 ok=True。
    """
    print("coverage: cocotb JSON validity + no rmtree/overwrite of user sources")
    import ast as _ast

    pm = {k: f"s_axil_{k}" for k in (
        "awaddr", "awvalid", "awready", "wdata", "wstrb", "wvalid", "wready",
        "bresp", "bvalid", "bready", "araddr", "arvalid", "arready",
        "rdata", "rresp", "rvalid", "rready")}

    # ---- 1) 生成的 REGS/ILLS 必须是合法 JSON（且源码本身是合法 Python）----
    for name in ('evil """ desc', "ends with backslash \\", "plain"):
        with tempfile.TemporaryDirectory() as td:
            spec = {
                "schema_version": "1.0",
                "boundary": {"module_name": "m", "clock": {"port": "clk"},
                             "reset": {"port": "rst_n"}, "axi4_lite": {"port_map": pm}},
                "test_plan": {"register_rules": [
                    {"rule_id": "R1", "name": name, "offset": 0,
                     "access": "RW", "reset_value": 0}]},
            }
            sp = Path(td) / "s.json"
            sp.write_text(json.dumps(spec), encoding="utf-8")
            r = call_tool("emit_testbench", {"spec_path": str(sp), "out_dir": str(Path(td) / "out")})
            assert r["ok"], (name, str(r.get("error"))[:200])
            tb = Path(r["data"]["artifacts"]["cocotb_testbench"]).read_text(encoding="utf-8")
            _ast.parse(tb)  # 必须仍是合法 Python
            regs_line = next(ln for ln in tb.splitlines() if ln.startswith("REGS"))
            ns = {"json": json}
            exec(regs_line, ns)  # 必须真的是 json.loads 能解析的
            assert ns["REGS"][0]["name"] == name, (name, ns["REGS"])

    # ---- 2) out_dir == RTL 目录时，用户 inc/ 绝不能被删 ----
    rtl_lines = ['`include "defs.svh"', "module dut (", "  input logic clk,",
                 "  input logic rst_n,"]
    for n in ("awaddr", "awvalid", "wdata", "wstrb", "wvalid", "araddr", "arvalid",
              "bready", "rready"):
        w = "[7:0] " if n in ("awaddr", "wdata") else ("[3:0] " if n == "wstrb" else "")
        rtl_lines.append(f"  input  logic {w}s_axil_{n},")
    outs = [("awready", ""), ("wready", ""), ("bvalid", ""), ("bresp", "[1:0] "),
            ("arready", ""), ("rvalid", ""), ("rresp", "[1:0] "), ("rdata", "[7:0] ")]
    for n, w in outs:
        rtl_lines.append(f"  output logic {w}s_axil_{n},")
    rtl_lines[-1] = rtl_lines[-1].rstrip(",")
    rtl_lines += [");", "endmodule"]
    rtl_src = "\n".join(rtl_lines) + "\n"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "inc").mkdir()
        header = root / "inc" / "defs.svh"
        header.write_text("// USER PRECIOUS HEADER\n", encoding="utf-8")
        dut = root / "dut.sv"
        dut.write_text(rtl_src, encoding="utf-8")
        spec = {
            "schema_version": "1.0",
            "boundary": {"module_name": "dut", "clock": {"port": "clk"},
                         "reset": {"port": "rst_n"}, "axi4_lite": {"port_map": pm}},
            "test_plan": {"register_rules": [
                {"rule_id": "R1", "name": "C", "offset": 0, "access": "RW",
                 "reset_value": 0}]},
        }
        sp = root / "spec.json"
        sp.write_text(json.dumps(spec), encoding="utf-8")
        r = call_tool("emit_testbench", {
            "spec_path": str(sp), "out_dir": str(root), "rtl_file": str(dut)})
        assert r["ok"], str(r.get("error"))[:300]
        assert header.is_file(), "user's inc/defs.svh was DELETED by emit_testbench"
        assert header.read_text(encoding="utf-8") == "// USER PRECIOUS HEADER\n"

        # 正常路径（out_dir 不同于 RTL 目录）仍要把 inc 复制过去
        out2 = root / "out2"
        r2 = call_tool("emit_testbench", {
            "spec_path": str(sp), "out_dir": str(out2), "rtl_file": str(dut)})
        assert r2["ok"], str(r2.get("error"))[:300]
        assert (out2 / "inc" / "defs.svh").is_file(), "inc not copied to a separate out_dir"

    # ---- 3) 产物与输入 RTL 同名时必须拒绝，不能覆盖用户文件 ----
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = ROOT / "examples" / "multi_bus_ref" / "pcie_tlp_ep.sv"
        target = root / "tb_pcie_tlp_ep_pcie.sv"
        target.write_bytes(src.read_bytes())
        before = target.read_bytes()
        r = emit_protocol_testbench(str(target), str(root), "pcie")
        assert r["ok"] is False, "must refuse to overwrite the input RTL"
        assert "overwrite" in str(r["error"]).lower(), str(r["error"])[:200]
        assert target.read_bytes() == before, "user RTL was overwritten"


def test_security_hardening_injection_xss_verilator_hijack() -> None:
    """Defect 127: 命令注入绕道、/tmp verilator 劫持、审计报告存储型 XSS。

    1. `generate_remote_run_script` 的四个白名单用 `$` 结尾 —— Python 的 `$` 也
       匹配「末尾换行之前」，于是 `tag='t\\n'` / `host='h\\n'` 能通过校验，
       插入脚本后把 `HOST="..."` 的字符串字面量从换行处截断，后续内容成为独立
       shell 命令（实测生成的脚本里 `HOST="host1` + 换行）。
    2. 同一个函数只做**字符**白名单，不查路径语义：`remote_work_dir='/'` 生成
       `WORK="//t"`、`'~/work/..'` 生成 `~/work/../t`，而脚本末尾是
       `rm -rf $WORK` —— 「清理临时目录」会删掉主目录下的同名目录。
    3. `find_verilator()` 把 `/tmp/verilator/bin/verilator` 排在 `shutil.which`
       **之前**。`/tmp` 通常是 1777，多用户机器上他人可预置同路径二进制来劫持
       本工具实际调用的仿真器。
    4. `render_visual_report` 把 `extra_meta`（源自 coverage_summary /
       mutation_summary）未转义插入 HTML：`<img src=x onerror=...>` 原样落盘，
       构成存储型 XSS；且 `mutation_score` 为非数字时
       `round(100.0*float(...))` 抛 `TypeError: can't multiply sequence ...`
       让整个报告渲染失败。
    """
    print("coverage: injection ($ vs \\Z, '..' paths), /tmp verilator hijack, report XSS")
    from fpga_toolbox.eda_campaign import generate_remote_run_script
    from fpga_toolbox.eda_env import _is_owned_by_me, find_verilator

    # ---- 1) 末尾换行注入：四个参数都必须被拒 ----
    for args in (("host1\n", "t", "run.sh"), ("host1", "t\n", "run.sh"),
                 ("host1", "t", "run.sh\n"), ("host1\nrm -rf ~\n", "t", "run.sh")):
        try:
            generate_remote_run_script(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"trailing-newline injection accepted: {args!r}")

    # ---- 2) 路径语义：'..' 与根路径必须被拒，合法子目录仍可用 ----
    for wd in ("/", "//", "~", "~/work/..", "/dev/shm/..", "/tmp/../home"):
        try:
            generate_remote_run_script("h", "t", "run.sh", wd, use_dev_shm=False)
        except ValueError:
            pass
        else:
            raise AssertionError(f"dangerous remote_work_dir accepted: {wd!r}")
    for wd in ("~/work", "/tmp/foo", "~/a/b", "/dev/shm"):
        s = generate_remote_run_script("h", "t", "run.sh", wd, use_dev_shm=False)
        assert 'WORK="' in s, wd

    # ---- 3) /tmp verilator 劫持：不应优先于 ~/.local 或 PATH ----
    v = find_verilator()
    assert v != "/tmp/verilator/bin/verilator", v
    # _is_owned_by_me 对非 /tmp 路径直接放行；对不存在的路径返回 False
    assert _is_owned_by_me("/nonexistent/verilator") is False

    # ---- 4a) XSS：所有输入字段都不得原样落进 HTML ----
    with tempfile.TemporaryDirectory() as td:
        payload = "<img src=x onerror=alert(1)>"
        rep = {
            "module": payload, "overall_status": "PASS", "generated_at": payload,
            "summary": {"total": 1, "passed": 1, "failed": 0},
            "checks": [{"rule_id": payload, "status": "PASS", "category": payload,
                        "protocol": payload, "tx": payload, "expected": payload,
                        "observed": payload, "engine": payload, "detail": payload}],
            "coverage_summary": {"rtl_pct": payload},
            "mutation_summary": {"killed": 1, "total": 1, "results": []},
            "capability_matrix": [{"protocol": payload, "dut": payload,
                                   "extract": payload, "emit_tb": payload,
                                   "sim_engine": payload, "checks_summary": payload,
                                   "mutation": payload}],
            "raw_log_excerpt": payload,
        }
        r = call_tool("render_visual_report", {
            "report": rep, "dep_graph": {"dependency_closure": [payload]},
            "extracted_interfaces": [], "out_html_path": str(Path(td) / "x.html")})
        assert r["ok"], str(r.get("error"))[:200]
        page = (Path(td) / "x.html").read_text(encoding="utf-8")
        svg = (Path(td) / "x.svg").read_text(encoding="utf-8")
        for doc in (page, svg):
            # 判据是「未转义的标签」而不是裸文本：转义后的
            # `&lt;img src=x onerror=alert(1)&gt;` 里仍含 onerror 字样，
            # 但不会被执行。真正危险的是 `<img` / `<svg` 这类开标签。
            assert "<img src=x" not in doc, "unescaped <img> injected into output"
            assert "<svg onload" not in doc, "unescaped <svg> injected into output"

    # ---- 4b) mutation_score 非数字不能让渲染崩 ----
    with tempfile.TemporaryDirectory() as td:
        for bad in ("<svg onload=alert(1)>", "n/a", None, 42.5, {"x": 1}, [1]):
            rep = {"module": "m", "overall_status": "PASS",
                   "summary": {"total": 1, "passed": 1, "failed": 0},
                   "checks": [{"rule_id": "A", "status": "PASS"}],
                   "mutation_summary": {"mutation_score": bad, "killed": 1,
                                        "total": 1, "results": []}}
            r = call_tool("render_visual_report", {
                "report": rep, "dep_graph": {}, "extracted_interfaces": [],
                "out_html_path": str(Path(td) / "y.html")})
            assert r["ok"], (bad, str(r.get("error"))[:160])


def test_silent_pass_gates_uvm_fatal_zero_frames_completed_xml() -> None:
    """Defect 128: 四处静默假通过（UVM_FATAL / 零帧 / completed 绕过 / XML 结构）。

    1. `parse_uvm_log` 只读 `UVM_ERROR`，完全忽略 `UVM_FATAL`：
       一份打印 `UVM_FATAL : 3` 的日志被判 `passed=True`。
    2. 未传 `expected_frames` 时 `target_frames = frames_obs`，于是
       `delivered=0` + `frames=0` 得到 `0 == 0` → 一个**什么都没跑**的仿真通过。
    3. `result_normalizer` 的 `COCOTB_INCOMPLETE` 门禁用 `is False` 判定：
       `"completed": "false"` 与 `"completed": 0` 都能绕过；且走 `"tests"`
       分支时 `completed` 被整条丢弃，`{"tests": [...], "completed": false}`
       直接 PASS。
    4. `_from_xml` 不校验根标签、不读 suite 级 failures/errors、且无名检查用
       `if not name`（纯空白 name 是 truthy）：非 JUnit XML 得到
       `ok=True + NO_RESULTS`，suite 级 `<error>` / `failures="3"` 判 PASS，
       `name="   "` 判 PASS。
    """
    print("coverage: UVM_FATAL / zero-frame / completed bypass / JUnit XML structure")
    from fpga_toolbox.eda_campaign import parse_uvm_log

    base = ("[C3900] NP_LAT: frames=64 min=10 avg=12 max=20\n"
            "delivered=64\nfid_bad=0\nall_retry=0\nsend_nums=64\n"
            "win_span=5\nUVM_ERROR :    0\n")
    assert parse_uvm_log(base)["ok"] is True, "clean UVM log must pass"
    # 1) UVM_FATAL 必须判失败
    assert parse_uvm_log(base + "UVM_FATAL :    3\n")["ok"] is False, "UVM_FATAL ignored"
    assert parse_uvm_log(base.replace("UVM_ERROR :    0", "UVM_ERROR :    5"))["ok"] is False
    # 2) 零帧不是通过
    zero = ("[C3900] NP_LAT: frames=0 avg=0\ndelivered=0\nfid_bad=0\n"
            "all_retry=0\nsend_nums=0\nUVM_ERROR :    0\n")
    assert parse_uvm_log(zero)["ok"] is False, "zero-frame run treated as pass"

    # 3) completed 的三种绕过形态
    from fpga_toolbox.result_normalizer import tool_normalize_sim_result

    payloads = {
        "bool False": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": False},
        "str false": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": "false"},
        "int 0": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": 0},
        "tests + False": {"tests": [{"name": "T", "status": "PASS"}], "completed": False},
        "tests + str false": {"tests": [{"name": "T", "status": "PASS"}], "completed": "false"},
    }
    with tempfile.TemporaryDirectory() as td:
        for label, payload in payloads.items():
            f = Path(td) / f"{abs(hash(label))}.json"
            f.write_text(json.dumps(payload), encoding="utf-8")
            d = tool_normalize_sim_result("m", cocotb_results_json=str(f))["data"]
            assert d["overall_status"] == "FAIL", (label, d["overall_status"])
            assert any(c["rule_id"] == "COCOTB_INCOMPLETE" for c in d["checks"]), label
        # 显式 True / 缺省都不能被误伤
        for label, payload in {
            "bool True": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": True},
            "absent": {"checks": [{"rule_id": "A", "status": "PASS"}]},
        }.items():
            f = Path(td) / f"{abs(hash(label))}.json"
            f.write_text(json.dumps(payload), encoding="utf-8")
            d = tool_normalize_sim_result("m", cocotb_results_json=str(f))["data"]
            assert d["overall_status"] == "PASS", (label, d["checks"])

        # 4) XML 结构校验
        xml_cases = {
            "non-JUnit root": ('<?xml version="1.0"?><foo><bar/></foo>', False, None),
            "suite error node": (
                '<?xml version="1.0"?><testsuites><testsuite><testcase name="T"/>'
                '</testsuite><errors><error message="setup failed"/></errors></testsuites>',
                True, "FAIL"),
            "suite failures attr": (
                '<?xml version="1.0"?><testsuites><testsuite failures="3">'
                '<testcase name="T"/></testsuite></testsuites>', True, "FAIL"),
            "whitespace name": (
                '<?xml version="1.0"?><testsuites><testsuite>'
                '<testcase name="   "/></testsuite></testsuites>', True, "FAIL"),
            "clean JUnit": (
                '<?xml version="1.0"?><testsuites><testsuite>'
                '<testcase name="T_OK"/></testsuite></testsuites>', True, "PASS"),
            "real failure": (
                '<?xml version="1.0"?><testsuites><testsuite><testcase name="T">'
                '<failure message="boom"/></testcase></testsuite></testsuites>',
                True, "FAIL"),
        }
        for label, (body, want_ok, want_overall) in xml_cases.items():
            f = Path(td) / f"{abs(hash(label))}.xml"
            f.write_text(body, encoding="utf-8")
            r = tool_normalize_sim_result("m", cocotb_results_json=str(f))
            assert r["ok"] is want_ok, (label, r.get("error"))
            if want_overall is not None:
                assert r["data"]["overall_status"] == want_overall, (
                    label, r["data"]["overall_status"])


def test_residual_and_suite_rerun_gates() -> None:
    """Defect 129: 残留检测的删除型误报/重复模式漏报 + suite 重跑沿用旧状态。

    1. `check_residual_mutations` 用 `m["new"] in txt` 判残留。删除型变异
       （`new == ""`）时 `"" in txt` **恒真**，于是任何「old 已消失」的文件都
       被判残留 → 合法的删除型变异永远无法运行（实测已变异的
       `assign y =;` 对 old=' a & b' new='' 必报 E030）。
    2. 变异主循环用 `replace(..., 1)` 只替换**第一处**。若 old 在文件中出现多
       次、第一处已变异而后面仍是 old，则 `old in txt` 为真 → 旧判据认为没有
       残留，**漏报**真实未恢复的混合态（实测）。
    3. `verify_suite_identities` 用 `results.setdefault(current, "STARTED")`
       —— 同名 suite **重跑**时不重置状态：第一次 PASS、第二次被截断（无任何
       输出）时沿用第一次的 PASS 与 summaries，判 `all_verified=True`。
    """
    print("coverage: residual gates (deletion/duplicate) + suite rerun state reset")
    from fpga_toolbox.mutation_runner import check_residual_mutations
    from fpga_toolbox.sim_runner import verify_suite_identities

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 1) 删除型变异（new=""）不参与残留检查 —— 否则必然误报
        (root / "del.sv").write_text("assign y =;\n", encoding="utf-8")
        r = check_residual_mutations(root, [
            {"id": "D1", "file": "del.sv", "old": " a & b", "new": "", "desc": "delete"}])
        assert r == [], f"deletion-type mutation false-positive residual: {r}"

        # 干净文件 + 删除型变异也不该报
        (root / "clean.sv").write_text("assign y = a & b;\n", encoding="utf-8")
        r2 = check_residual_mutations(root, [
            {"id": "D2", "file": "clean.sv", "old": " a & b", "new": "", "desc": "d"}])
        assert r2 == [], r2

        # 2a) 完全未恢复（old 消失、new 在）必须报
        (root / "full.sv").write_text("assign y = (a | b);\n", encoding="utf-8")
        r3 = check_residual_mutations(root, [
            {"id": "M1", "file": "full.sv", "old": "a & b", "new": "a | b", "desc": "x"}])
        assert len(r3) == 1 and r3[0]["code"] == "E030", r3

        # 2b) 混合态（第一处已改、第二处仍是 old）必须报 —— 旧判据漏报
        (root / "mixed.sv").write_text(
            "assign a = x | y;\nassign b = x & y;\n", encoding="utf-8")
        r4 = check_residual_mutations(root, [
            {"id": "M2", "file": "mixed.sv", "old": "x & y", "new": "x | y", "desc": "d"}])
        assert len(r4) == 1 and r4[0]["code"] == "E030", r4

        # 2c) 完全干净（只有 old、没有 new）不能误报
        (root / "ok.sv").write_text("assign a = x & y;\n", encoding="utf-8")
        r5 = check_residual_mutations(root, [
            {"id": "M3", "file": "ok.sv", "old": "x & y", "new": "x | y", "desc": "d"}])
        assert r5 == [], r5

    # 3) suite 重跑：截断的重跑不能沿用第一次 PASS
    trunc = ("== sim: apb (top=t) ==\nPASS: apb ok\n"
             "== sim: apb (top=t) ==\n")
    rr = verify_suite_identities(trunc, ["apb"])
    assert rr["all_verified"] is False, rr
    assert rr["observed"]["apb"] == "STARTED", rr["observed"]

    # 重跑也完整通过 -> 仍 PASS
    both = ("== sim: apb (top=t) ==\nPASS: apb ok\n"
            "== sim: apb (top=t) ==\nPASS: apb ok again\n")
    rb = verify_suite_identities(both, ["apb"])
    assert rb["all_verified"] is True, rb

    # 重跑崩溃 -> FAIL
    crash = ("== sim: apb (top=t) ==\nPASS: apb ok\n"
             "== sim: apb (top=t) ==\n[CHECK] id=A | status=PASS\n%Error: crash\n")
    rc = verify_suite_identities(crash, ["apb"])
    assert rc["all_verified"] is False and rc["observed"]["apb"] == "FAIL", rc


def test_sv_parser_tokenizer_and_header_fixes() -> None:
    """Defect 130: builtin SV 解析器的 5 处缺陷（分词器 / header / 多维位宽）。

    1. 分词器的 `IDENT` 是 `[A-Za-z_][A-Za-z0-9_$]*`，**不允许 `$` 开头**，
       也不把 `$` 放进 `SYM` —— `$clog2(DEPTH)` 被静默丢成 `clog2(DEPTH)`，
       端口宽度退化成 1（slang 正确给 4）。builtin 是 slang 不可用时的后端，
       于是整体位宽推断全错。
    2. `<<` / `>>` 被拆成两个单字符 token，`_parse_params` 用
       `" ".join(t.value ...)` 重新拼成 `< <`，`eval_int_expr` 解析失败 →
       参数丢失（`parameter int W = 1 << 3` 得到 params={}）、端口宽度退化为 1。
    3. `module automatic foo (...)` —— 自动机标是生命周期修饰符，旧实现直接取
       下一个 IDENT 当模块名，于是模块被解析成名字 `automatic`、端口全空。
    4. `module foo import pkg::*; #(...) (...)` —— header 里的 import 声明让
       旧实现跳出，**参数与端口全部丢失**。
    5. 多维 packed 数组 `logic [1:0][7:0]` 总位宽是各维**乘积**（16）：builtin
       只解析第一维，slang 的 `re.search` 也只取第一维 → 分别得到 8 / 2。
    """
    print("coverage: SV tokenizer ($clog2, shifts), module header, multi-dim widths")
    from fpga_toolbox.core.sv_parser import parse_sv_file, sv_tokenize

    # 1) $ 前缀 token
    assert [t.value for t in sv_tokenize("$clog2(8)")][0] == "$clog2", \
        [t.value for t in sv_tokenize("$clog2(8)")]
    # 2) 多字符运算符必须是一个 token
    for expr, want in (("1 << 3", ["1", "<<", "3"]),
                       ("8 >> 2", ["8", ">>", "2"]),
                       ("a <= b", ["a", "<=", "b"]),
                       ("x == y", ["x", "==", "y"])):
        assert [t.value for t in sv_tokenize(expr)] == want, expr

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # $clog2 端口宽度
        f1 = root / "clog2.sv"
        f1.write_text(
            "module clog2_dut #(parameter int DEPTH = 16) (\n"
            "  input  logic clk,\n"
            "  output logic [$clog2(DEPTH)-1:0] addr\n);\n"
            "  assign addr = '0;\nendmodule\n", encoding="utf-8")
        m = parse_sv_file(f1, backend="builtin")[0]
        addr = next(p for p in m.ports if p.name == "addr")
        assert addr.width == 4, (addr.width_expr, addr.width)

        # 移位参数
        f2 = root / "shift.sv"
        f2.write_text(
            "module sp_dut #(parameter int W = 1 << 3) (\n"
            "  input  logic [W-1:0] d,\n  output logic [W-1:0] q\n);\n"
            "  assign q = d;\nendmodule\n", encoding="utf-8")
        m2 = parse_sv_file(f2, backend="builtin")[0]
        assert m2.parameters.get("W") == 8, m2.parameters
        assert all(p.width == 8 for p in m2.ports), [(p.name, p.width) for p in m2.ports]

        # module automatic
        f3 = root / "auto.sv"
        f3.write_text(
            "module automatic auto_dut (\n  input logic clk,\n"
            "  output logic q\n);\n  assign q = clk;\nendmodule\n", encoding="utf-8")
        m3 = parse_sv_file(f3, backend="builtin")[0]
        assert m3.name == "auto_dut", m3.name
        assert {p.name for p in m3.ports} == {"clk", "q"}, [p.name for p in m3.ports]

        # module ... import pkg::*; #(...) (...)
        f4 = root / "imp.sv"
        f4.write_text(
            "module imp_dut import mypkg::*; #(parameter int W = 8) (\n"
            "  input  logic [W-1:0] d,\n  output logic [W-1:0] q\n);\n"
            "  assign q = d;\nendmodule\n", encoding="utf-8")
        m4 = parse_sv_file(f4, backend="builtin")[0]
        assert m4.name == "imp_dut", m4.name
        assert m4.parameters.get("W") == 8, m4.parameters
        assert {p.name for p in m4.ports} == {"d", "q"}, [p.name for p in m4.ports]

        # 多维 packed：两个后端都要给乘积
        f5 = root / "multi.sv"
        f5.write_text(
            "module multi_dut (\n  input  logic [1:0][7:0] d2d,\n"
            "  output logic [3:0][3:0] d2d2,\n  output logic [7:0] flat\n);\n"
            "endmodule\n", encoding="utf-8")
        for backend in ("builtin", "slang"):
            mods = parse_sv_file(f5, backend=backend)
            if not mods:
                continue
            got = {p.name: p.width for p in mods[0].ports}
            assert got.get("d2d") == 16, (backend, got)
            assert got.get("d2d2") == 16, (backend, got)
            assert got.get("flat") == 8, (backend, got)


def test_iface_direction_prefix_and_ahb_ready_split() -> None:
    """Defect 131: 方向前缀拆组 + AHB HREADY/HREADYOUT 键冲突。

    1. `_norm_prefix` 原本 `re.sub(r"^(?:i_|o_|in_|out_)", ...)` —— 要求方向词
       后**必须跟下划线**。但各 `_*_PATS` 的模式是 `(?:^|_)sig$`，`m.start()`
       落在下划线**之后**，传进来的前缀是裸 `"i"` / `"o"`，正则匹配不到 →
       APB 被拆成 "i" 组与 "o" 组两组，识别出的接口只有一半信号：
       实测 i_psel/i_penable/... + o_pready/o_prdata/o_pslverr 的 APB 只认出
       group="i" 的 6 个信号，3 个输出口落进 out_of_scope_ports。
    2. `_AHB_PATS` 的 `hready` 模式是 `h_?ready(?:out|in)?$`，同时匹配
       HREADY 与 HREADYOUT，后遍历者覆盖前者。标准 AHB-Lite 从机同时暴露
       这两个端口（HREADY 输入、HREADYOUT 输出），实测
       `port_map["hready"]="HREADYOUT"` 而真正的输入 `HREADY` 落进
       out_of_scope_ports 并被 tie_off 到 `1'b0` —— TB 端永远等不到 ready，
       仿真永久挂起。
    """
    print("coverage: direction-prefix grouping + AHB HREADY/HREADYOUT split")
    from fpga_toolbox.iface_extractor import _norm_prefix

    # 1a) 裸方向词与带下划线形态都必须归一成 default
    for raw in ("i", "o", "i_", "o_", "in", "out", "in_", "out_"):
        assert _norm_prefix(raw) == "default", (raw, _norm_prefix(raw))
    # 1b) 非方向前缀不能被误伤
    for raw in ("s_axi", "m_axi", "ch0", "s_apb"):
        assert _norm_prefix(raw) == raw, (raw, _norm_prefix(raw))

    # 1c) 端到端：完整的 i_/o_ 前缀 APB 必须被认出全部 9 个信号
    apb = """module dirpref_dut (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        i_psel,
  input  logic        i_penable,
  input  logic        i_pwrite,
  input  logic [7:0]  i_paddr,
  input  logic [31:0] i_pwdata,
  input  logic [3:0]  i_pstrb,
  output logic        o_pready,
  output logic [31:0] o_prdata,
  output logic        o_pslverr
);
  assign o_pready = 1'b1; assign o_prdata = i_pwdata; assign o_pslverr = 1'b0;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(apb, encoding="utf-8")
        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        mod = ex["data"]["modules"][0]
        ifaces = mod["interfaces"]
        assert len(ifaces) == 1, [(i["protocol"], i["group_prefix"]) for i in ifaces]
        assert ifaces[0]["protocol"] == "apb", ifaces[0]["protocol"]
        assert len(ifaces[0]["port_map"]) == 9, sorted(ifaces[0]["port_map"])
        assert mod["out_of_scope_ports"] == [], mod["out_of_scope_ports"]

        # 2) AHB 同时有 HREADY + HREADYOUT：两者必须各归各位
        ahb = """module ahb_both_dut (
  input  logic        HCLK,
  input  logic        HRESETn,
  input  logic        HSEL,
  input  logic [15:0] HADDR,
  input  logic [1:0]  HTRANS,
  input  logic        HWRITE,
  input  logic [2:0]  HSIZE,
  input  logic [31:0] HWDATA,
  input  logic        HREADY,
  output logic        HREADYOUT,
  output logic [31:0] HRDATA,
  output logic        HRESP
);
  assign HREADYOUT = HREADY;
  assign HRDATA = HWDATA;
  assign HRESP = 1'b0;
endmodule
"""
        f2 = Path(td) / "ahb.sv"
        f2.write_text(ahb, encoding="utf-8")
        ex2 = call_tool("extract_interfaces", {"rtl_file": str(f2)})
        assert ex2["ok"], str(ex2.get("error"))[:200]
        m2 = ex2["data"]["modules"][0]
        pm = next(i for i in m2["interfaces"] if i["protocol"] == "ahb_lite")["port_map"]
        assert pm.get("hready") == "HREADY", pm
        assert pm.get("hreadyout") == "HREADYOUT", pm
        # 真正的输入 HREADY 绝不能被 tie 到 1'b0
        assert all(o["port"] != "HREADY" for o in m2["out_of_scope_ports"]), \
            m2["out_of_scope_ports"]

        # 端到端：标准 AHB-Lite 从机现在能正常生成 TB（修复前误报方向不符）
        r = call_tool("emit_testbench", {
            "rtl_file": str(f2), "out_dir": str(Path(td) / "ahb_tb"),
            "protocol": "ahb_lite"})
        assert r["ok"], str(r.get("error"))[:300]
        tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        assert ".HREADYOUT(HREADYOUT)" in tb, "template must connect the slave's HREADYOUT"


def test_resolve_deps_transitive_headers() -> None:
    """Defect 132: resolve_deps 必须展开 header 之间的传递 include。

    `include 是文本包含，header 内部还能再 include 别的 header。旧实现只收集
    模块**直接** include 的那一层，传递依赖全丢：top.sv -> c.svh -> b.svh ->
    a.svh 只返回 header_files=["inc/c.svh"]。调用方把 all_files 喂给
    copy_sandbox，沙箱里就缺 b.svh / a.svh，Verilator 报
    %Error: inc/c.svh:1:10: Cannot find include file: 'b.svh' —— 而整条链
    全程 ok=True（实测复现）。
    """
    print("coverage: resolve_deps transitive header closure")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inc = root / "inc"
        inc.mkdir()
        (inc / "a.svh").write_text("`define A 1\n", encoding="utf-8")
        (inc / "b.svh").write_text('`include "a.svh"\n`define B 2\n', encoding="utf-8")
        (inc / "c.svh").write_text(
            '`include "b.svh"\n`include "a.svh"\n`define C 3\n', encoding="utf-8")
        (root / "top.sv").write_text(
            '`include "c.svh"\n`include "c.svh"\nmodule top(input logic clk); endmodule\n',
            encoding="utf-8")

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "top"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        hdrs = data["header_files"]
        assert hdrs == ["inc/a.svh", "inc/b.svh", "inc/c.svh"], hdrs
        assert data["all_files"] == ["inc/a.svh", "inc/b.svh", "inc/c.svh", "top.sv"], \
            data["all_files"]
        # 传递依赖齐全时不应有任何 W101
        assert not [d for d in (r.get("diagnostics") or []) if d["code"] == "W101"], r["diagnostics"]

        # 沙箱复制必须带齐所有传递 header，并且真的能编译
        sb = root / "_sb"
        c = call_tool("copy_sandbox", {
            "project_root": str(root), "sandbox_dir": str(sb),
            "relative_files": data["all_files"]})
        assert c["ok"], str(c.get("error"))[:200]
        for rel in ("inc/a.svh", "inc/b.svh", "inc/c.svh", "top.sv"):
            assert (sb / rel).is_file(), f"missing in sandbox: {rel}"

        # 循环 include 不能死循环
        cyc = root / "_cyc"
        cyc.mkdir()
        (cyc / "x.svh").write_text('`include "y.svh"\n`define X 1\n', encoding="utf-8")
        (cyc / "y.svh").write_text('`include "x.svh"\n`define Y 2\n', encoding="utf-8")
        (cyc / "topc.sv").write_text(
            '`include "x.svh"\nmodule topc(input logic clk); endmodule\n', encoding="utf-8")
        rc = call_tool("resolve_deps", {"project_root": str(cyc), "target_module": "topc"})
        assert rc["ok"], str(rc.get("error"))[:200]
        assert rc["data"]["header_files"] == ["x.svh", "y.svh"], rc["data"]["header_files"]

        # 传递依赖里缺 header：必须报 W101，而不是静默给出不完整清单
        miss = root / "_miss"
        miss.mkdir()
        (miss / "outer.svh").write_text('`include "ghost.svh"\n`define O 1\n', encoding="utf-8")
        (miss / "topm.sv").write_text(
            '`include "outer.svh"\nmodule topm(input logic clk); endmodule\n', encoding="utf-8")
        rm = call_tool("resolve_deps", {"project_root": str(miss), "target_module": "topm"})
        assert rm["ok"], str(rm.get("error"))[:200]
        codes = [(d["code"], d["path"]) for d in (rm.get("diagnostics") or [])]
        assert ("W101", "ghost.svh") in codes, codes


def test_included_source_not_treated_as_compilation_unit() -> None:
    """Defect 133: 被 `include 的 .sv 不能被当成独立编译单元。

    ``include "defs.sv"`` 里放 helper module 是老项目常见写法。旧实现把
    defs.sv 和 top.sv 都列进 resolve_deps 的 rtl_files，调用方拿这份清单
    去编译 → module 被看到两次，Verilator 报
    ``%Error: Exiting due to 1 warning(s)``（MODDUP: Duplicate declaration of
    module）。这是默认配置下的**硬失败**（Verilator 默认 warning-as-error）。
    """
    print("coverage: included .sv source is not a compilation unit")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "defs.sv").write_text(
            "module helper_mod (input logic a, output logic b);\n"
            "  assign b = a;\n"
            "endmodule\n", encoding="utf-8")
        (root / "top.sv").write_text(
            '`include "defs.sv"\n'
            "module dupinc_top (input logic clk, output logic q);\n"
            "  helper_mod u (.a(clk), .b(q));\n"
            "endmodule\n", encoding="utf-8")

        idx = call_tool("index_repo", {"project_root": str(root)})
        assert idx["ok"], str(idx.get("error"))[:200]
        assert idx["data"]["included_files"] == ["defs.sv"], idx["data"]["included_files"]

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "dupinc_top"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        # 编译单元只有 top.sv；defs.sv 通过 include 展开拿到
        assert data["rtl_files"] == ["top.sv"], data["rtl_files"]
        # 但沙箱清单必须仍然包含 defs.sv（否则 include 找不到）
        assert data["all_files"] == ["defs.sv", "top.sv"], data["all_files"]
        assert data["dependency_closure"] == ["dupinc_top", "helper_mod"], \
            data["dependency_closure"]

        # .vh/.svh 天生是 header，不该被算进 included_files
        (root / "defs_h.svh").write_text("`define D 1\n", encoding="utf-8")
        (root / "top2.sv").write_text(
            '`include "defs_h.svh"\nmodule top2(input logic clk); endmodule\n', encoding="utf-8")
        idx2 = call_tool("index_repo", {"project_root": str(root)})
        assert idx2["data"]["included_files"] == ["defs.sv"], idx2["data"]["included_files"]


def test_rtl_files_keeps_independently_compilable_modules() -> None:
    """Defect 135: 剔除被 include 的源文件时必须保留「独立可编译」的目标。

    133 的修法「rtl_files = rtl_set - included_files」过于粗暴：当目标模块
    **本身只定义在被 include 的文件里**时（`dup_mod` 定义在 `real.sv`，而
    `real.sv` 恰好被 `wrapper.sv` include），rtl_files 会变成**空列表** ——
    调用方拿空清单编译必然失败。

    正确判据：只剔除「被闭包内其它编译单元 include 展开」的文件。
    - 目标 = dupinc_top（top.sv include defs.sv，defs.sv 定义 helper_mod）：
      defs.sv 应剔除（否则 MODDUP），rtl_files == ["top.sv"]
    - 目标 = dup_mod（只定义在 real.sv，real.sv 被 wrapper include 但
      wrapper 不在闭包内）：real.sv 必须保留，rtl_files == ["rtl/real.sv"]
    """
    print("coverage: rtl_files keeps independently compilable modules")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "defs.sv").write_text(
            "module helper_mod (input logic a, output logic b);\n  assign b = a;\nendmodule\n",
            encoding="utf-8")
        (root / "top.sv").write_text(
            '`include "defs.sv"\nmodule dupinc_top (input logic clk, output logic q);\n'
            "  helper_mod u (.a(clk), .b(q));\nendmodule\n", encoding="utf-8")

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "dupinc_top"})
        assert r["ok"], str(r.get("error"))[:200]
        assert r["data"]["rtl_files"] == ["top.sv"], r["data"]["rtl_files"]

        # 目标正是被 include 文件里定义的模块：不能把清单清空
        rh = call_tool("resolve_deps", {"project_root": str(root), "target_module": "helper_mod"})
        assert rh["ok"], str(rh.get("error"))[:200]
        assert rh["data"]["rtl_files"] == ["defs.sv"], rh["data"]["rtl_files"]

        # 子目录 + 目标在 include 目标文件中
        rtl = root / "rtl"
        rtl.mkdir()
        (rtl / "real.sv").write_text(
            "module dup_mod (input logic clk, output logic [7:0] q);\n"
            "  assign q = 8'hAA;\nendmodule\n", encoding="utf-8")
        (rtl / "wrapper.sv").write_text(
            '`include "real.sv"\nmodule wrapper (input logic clk, output logic [7:0] q);\n'
            "  dup_mod u (.clk(clk), .q(q));\nendmodule\n", encoding="utf-8")

        rw = call_tool("resolve_deps", {"project_root": str(root), "target_module": "wrapper"})
        assert rw["ok"], str(rw.get("error"))[:200]
        assert rw["data"]["rtl_files"] == ["rtl/wrapper.sv"], rw["data"]["rtl_files"]
        rm = call_tool("resolve_deps", {"project_root": str(root), "target_module": "dup_mod"})
        assert rm["ok"], str(rm.get("error"))[:200]
        assert rm["data"]["rtl_files"] == ["rtl/real.sv"], rm["data"]["rtl_files"]
        # 沙箱清单始终完整
        assert rm["data"]["all_files"] == ["rtl/real.sv"], rm["data"]["all_files"]


def test_tb_builds_for_rtl_without_timescale() -> None:
    """Defect 137: DUT 不写 `timescale 时 TB 构建必须仍然成功。

    TB 头部有 ``timescale 1ns/1ps``，但大量真实 RTL 把 timescale 交给编译
    选项、文件里不写。Verilator 5.x 对「部分模块有、部分没有」报
    TIMESCALEMOD，并在默认配置下当 error（"Exiting due to N warning(s)"）：
    一个完全合法的 DUT 直接构建失败，而 emit_testbench 返回 ok=True。

    修复方式是给 verilator 统一传 `--timescale 1ns/1ps`（与 tb_emitter 的
    COMPILE_ARGS 口径一致），而不是压制告警。
    """
    print("coverage: TB run script supplies --timescale for RTL without one")
    from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dut = root / "apb_nots.sv"
        dut.write_text(
            "module apb_nots (\n"
            "  input  logic        clk,\n"
            "  input  logic        rst_n,\n"
            "  input  logic        psel,\n"
            "  input  logic        penable,\n"
            "  input  logic        pwrite,\n"
            "  input  logic [7:0]  paddr,\n"
            "  input  logic [31:0] pwdata,\n"
            "  input  logic [3:0]  pstrb,\n"
            "  output logic        pready,\n"
            "  output logic [31:0] prdata,\n"
            "  output logic        pslverr\n"
            ");\n"
            "  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
            "endmodule\n", encoding="utf-8")
        r = emit_protocol_testbench(str(dut), str(root / "tb"), "apb")
        assert r["ok"], str(r.get("error"))[:300]
        script = Path(r["data"]["artifacts"]["sim_script"]).read_text(encoding="utf-8")
        assert "--timescale 1ns/1ps" in script, script[:400]
        # 不能靠压制告警掩饰：真正的 verilator 调用行里不该出现抑制 TIMESCALEMOD
        vlt_lines = [ln for ln in script.splitlines()
                     if '"$VLT"' in ln and "--binary" in ln]
        assert vlt_lines, script[:400]
        assert not any("Wno-TIMESCALEMOD" in ln for ln in vlt_lines), vlt_lines


def test_typedef_port_width_placeholders_are_legal() -> None:
    """Defect 138/139: typedef/interface 端口位宽未解析时不能生成非法或致败代码。

    纯文本解析拿不到 typedef（enum/struct）与 interface 端口的位宽，实测都是
    width=0。旧实现：

    - 138: `out_of_scope_ports` 的 tie_off 用 f"{width}'b0" → 产出非法字面量
      `0'b0`（零位宽）；tb_emitter 直接写进 `.port(0'b0)`，生成物必然编译失败。
      修法：位宽未知时用 SV 的 unsized `'0`，并附 warning 说明位宽未解析。
    - 139: 输出端口的观察变量按 `if o.width > 1` 三态声明，width=0 落到 1 位，
      而 DUT 端口实际可能多位（enum 常见 2/3 位）→ WIDTHTRUNC，Verilator 默认
      warning-as-error 直接构建失败。修法：该端口用**显式空连接** `.port()`
      （实测零告警），并通过 W054 诊断告知调用方未观察该端口。
    """
    print("coverage: unresolved typedef port widths use legal placeholders")
    from fpga_toolbox.registry import call_tool

    rtl = (
        "package tdpkg;\n"
        "  typedef enum logic [2:0] { T0, T1, T2 } dbg_e;\n"
        "endpackage\n"
        "module axil_typed\n"
        "  import tdpkg::*;\n"
        "(\n"
        "  input  logic        ACLK,\n"
        "  input  logic        ARESETn,\n"
        "  input  logic [7:0]  AWADDR,\n"
        "  input  logic        AWVALID,\n"
        "  output logic        AWREADY,\n"
        "  input  logic [31:0] WDATA,\n"
        "  input  logic [3:0]  WSTRB,\n"
        "  input  logic        WVALID,\n"
        "  output logic        WREADY,\n"
        "  output logic [1:0]  BRESP,\n"
        "  output logic        BVALID,\n"
        "  input  logic        BREADY,\n"
        "  input  logic [7:0]  ARADDR,\n"
        "  input  logic        ARVALID,\n"
        "  output logic        ARREADY,\n"
        "  output logic [31:0] RDATA,\n"
        "  output logic [1:0]  RRESP,\n"
        "  output logic        RVALID,\n"
        "  input  logic        RREADY,\n"
        "  input  dbg_e        dbg_mode_i,\n"
        "  output dbg_e        dbg_state_o\n"
        ");\n"
        "  assign AWREADY = 1'b1; assign WREADY = 1'b1; assign BVALID = 1'b0;\n"
        "  assign BRESP = 2'b00; assign ARREADY = 1'b1; assign RVALID = 1'b0;\n"
        "  assign RDATA = 32'h0; assign RRESP = 2'b00; assign dbg_state_o = T0;\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "axil_typed.sv"
        f.write_text(rtl, encoding="utf-8")

        # 138: 输入侧 tie_off 不能是 0'b0
        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        mod = ex["data"]["modules"][0]
        oos = {o["port"]: o for o in mod["out_of_scope_ports"]}
        assert "dbg_mode_i" in oos, sorted(oos)
        assert oos["dbg_mode_i"]["tie_off"] != "0'b0", oos["dbg_mode_i"]
        assert oos["dbg_mode_i"]["tie_off"] == "'0", oos["dbg_mode_i"]
        assert "warning" in oos["dbg_mode_i"], oos["dbg_mode_i"]

        # 139: 观察端口用显式空连接，且报 W054
        draft = root / "draft.yaml"
        dr = call_tool("extract_interfaces",
                       {"rtl_file": str(f), "emit_draft_yaml": str(draft)})
        assert dr["ok"], str(dr.get("error"))[:200]
        em = call_tool("emit_testbench", {
            "spec_path": str(draft), "rtl_file": str(f), "out_dir": str(root / "out")})
        assert em["ok"], str(em.get("error"))[:300]
        assert em["data"]["unconnected_observation_ports"] == ["dbg_state_o"], \
            em["data"].get("unconnected_observation_ports")
        codes = [(d["code"], d["path"]) for d in (em.get("diagnostics") or [])]
        assert ("W054", "wrapper[dbg_state_o]") in codes, codes
        wrap = Path(em["data"]["artifacts"]["unit_isolation_wrapper"]).read_text(
            encoding="utf-8")
        assert ".dbg_state_o()" in wrap, wrap
        assert "0'b0" not in wrap, "illegal zero-width literal in wrapper"
        # 输入侧 tie-off 必须是合法的 unsized '0
        assert ".dbg_mode_i('0)" in wrap, wrap


def test_unexpanded_sv_interface_ports_are_reported() -> None:
    """Defect 140: interface 打包的总线端口不能被报成「DUT 缺端口」。

    DUT 顶层只有 `apb_if.slave apb` 一个端口时，各平铺信号（psel/pready/...）
    都在 interface 内部。旧实现给 extract_interfaces 返回
    `protocol="apb"` + `port_map={}` + `missing_signals=[]`，**零诊断** ——
    调用方以为抽到了可用接口；下游 emit_testbench 则报
    「DUT is missing port(s) psel->psel, ...」，把「端口是 interface 类型」
    误述成「DUT 漏了端口」，排查方向完全错。
    """
    print("coverage: unexpanded SV interface ports are reported honestly")
    from fpga_toolbox.registry import call_tool

    src = (
        "interface apb_if (input logic clk, input logic rst_n);\n"
        "  logic psel, penable, pwrite, pready;\n"
        "  logic [7:0] paddr;\n"
        "  logic [31:0] pwdata, prdata;\n"
        "  logic pslverr;\n"
        "  modport slave (input psel, penable, pwrite, paddr, pwdata,\n"
        "                 output pready, prdata, pslverr);\n"
        "endinterface\n"
        "module iface_dut (\n"
        "  input logic clk,\n"
        "  input logic rst_n,\n"
        "  apb_if.slave apb\n"
        ");\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "iface_dut.sv"
        f.write_text(src, encoding="utf-8")
        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        mod = ex["data"]["modules"][0]
        # 如实标记：接口未展开
        assert mod["interfaces_expandable"] is False, mod.get("interfaces_expandable")
        unexp = mod["unexpanded_sv_interfaces"]
        assert len(unexp) == 1, unexp
        assert unexp[0]["port"] == "apb", unexp[0]
        assert unexp[0]["interface_type"] == "apb_if", unexp[0]
        assert unexp[0]["modport"] == "slave", unexp[0]

        # 下游错误信息必须指向 interface，而不是「DUT 缺端口」
        from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench
        r = emit_protocol_testbench(str(f), str(Path(td) / "tb"), "apb")
        assert r["ok"] is False
        err = str(r.get("error"))
        assert "SV interface port" in err, err[:300]
        assert "apb_if" in err, err[:300]
        # 不再把根因说成「DUT 缺少端口」
        assert "DUT is missing port(s)" not in err, err[:300]
        assert (r.get("data") or {}).get("sv_interface", {}).get("interface_type") == "apb_if"

        # 扁平端口的 DUT 必须仍然可解析且 expandable=True
        flat = Path(td) / "flat.sv"
        flat.write_text(
            "module flat_dut (\n"
            "  input  logic        clk,\n"
            "  input  logic        rst_n,\n"
            "  input  logic        psel,\n"
            "  input  logic        penable,\n"
            "  input  logic        pwrite,\n"
            "  input  logic [7:0]  paddr,\n"
            "  input  logic [31:0] pwdata,\n"
            "  input  logic [3:0]  pstrb,\n"
            "  output logic        pready,\n"
            "  output logic [31:0] prdata,\n"
            "  output logic        pslverr\n"
            ");\n"
            "  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
            "endmodule\n", encoding="utf-8")
        exf = call_tool("extract_interfaces", {"rtl_file": str(flat)})
        mf = exf["data"]["modules"][0]
        assert mf["interfaces_expandable"] is True, mf.get("interfaces_expandable")
        assert mf["unexpanded_sv_interfaces"] == [], mf["unexpanded_sv_interfaces"]


def test_lint_tb_zero_matches_is_not_silent_clean() -> None:
    """Defect 141: 目录模式扫到 0 个文件时必须区分「没检查」与「检查通过」。

    `_collect_targets` 在目录模式下只收「tb_ 前缀 / vip 目录 / 含 driver」的
    SV 文件（刻意的启发式，避免把 DUT 当 TB 检查）。于是 `my_testbench.sv`、
    `bad_tb.sv` 这类常见命名被静默跳过：一个只含 `bad_tb.sv` 的目录返回
    `scanned_sv_files=0, clean=true, findings=[]`，调用方以为 TB 已通过检查。
    """
    print("coverage: lint_tb distinguishes no-match from clean")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "bad_tb.sv").write_text(
            "module bad_tb;\n"
            "  initial begin $display(\"PASS: x\"); end\n"
            "endmodule\n", encoding="utf-8")

        # 目录模式：文件名不匹配启发式 → 必须显式说明「没扫到文件」
        r = call_tool("lint_tb", {"target_path": str(root)})
        d = r["data"]
        assert d["scanned_sv_files"] == 0, d
        assert d["scanned_anything"] is False, d
        codes = [(x["code"], x["severity"]) for x in (r.get("diagnostics") or [])]
        assert ("W055", "WARNING") in codes, codes
        # strict 模式下这是硬失败
        rs = call_tool("lint_tb", {"target_path": str(root), "strict": True})
        assert rs["ok"] is False, rs.get("error")

        # 直接传文件路径时必须照常检查（不受命名启发式限制）
        rf = call_tool("lint_tb", {"target_path": str(root / "bad_tb.sv")})
        df = rf["data"]
        assert df["scanned_sv_files"] == 1, df
        assert df["scanned_anything"] is True, df

        # 符合启发式的文件：照常扫描，不报 W055
        (root / "tb_good.sv").write_text(
            "module tb_good;\n"
            "  initial begin $display(\"PASS: x\"); $finish; end\n"
            "endmodule\n", encoding="utf-8")
        r2 = call_tool("lint_tb", {"target_path": str(root)})
        assert r2["data"]["scanned_sv_files"] >= 1, r2["data"]
        codes2 = [x["code"] for x in (r2.get("diagnostics") or [])]
        assert "W055" not in codes2, codes2


def test_normalizer_surfaces_verilator_errors_and_empty_verification() -> None:
    """Defect 142/143: 日志里的 %Error 与「零套件核对」都不能算正常。

    142: result_normalizer 完全不看 `%Error:` 行。一份「%Error: syntax error」
    混着「PASS: ...」的日志被归一成 overall_status=NO_RESULTS、零诊断 ——
    一个编译都没过的仿真既不失败也不提示。修法：把 %Error 行转成
    rule_id=VERILATOR_ERROR 的 FAIL 检查，使 overall_status=FAIL。

    143: `all_verified` 的语义是「预期套件没缺失/没失败」，expected_suites 为空
    时必然为 true。但它与 observed={} 并排出现会被误读成「核对通过」。
    新增 observed_any / verified_any 明确区分「没核对」与「核对通过」。
    （all_verified 语义保持不变：自审计 A6 依赖它对「无预期」不假报警。）
    """
    print("coverage: normalizer surfaces %Error and empty verification")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        log = root / "sim.log"
        log.write_text(
            "%Error: some_sv.sv:10:5: syntax error\n"
            "PASS: apb all 8 checks passed\n", encoding="utf-8")

        r = call_tool("normalize_sim_result", {
            "verilator_log_path": str(log), "module_name": "apb"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        # 142: %Error 必须让 overall_status 变 FAIL，且作为一条 FAIL 检查出现
        assert data["overall_status"] == "FAIL", data["overall_status"]
        assert data["summary"]["failed"] >= 1, data["summary"]
        rules = {c["rule_id"]: c["status"] for c in data["checks"]}
        assert rules.get("VERILATOR_ERROR") == "FAIL", rules

        # 143: 日志里没有 `== sim: ... ==` 头 → 没观察到任何套件
        ident = data["verilator_suite_identity"]
        assert ident["all_verified"] is True, ident          # 语义不变
        assert ident["observed_any"] is False, ident         # 但确实没核对
        assert ident["verified_any"] is False, ident

        # 对照组：有真实 suite 头 + PASS 汇总行 → observed_any / verified_any 为真
        ok_log = root / "ok.log"
        ok_log.write_text(
            "== sim: apb (top=tb_apb) ==\n"
            "PASS: apb all 8 checks passed\n", encoding="utf-8")
        ro = call_tool("normalize_sim_result", {
            "verilator_log_path": str(ok_log), "module_name": "apb",
            "expected_suites": ["apb"]})
        assert ro["ok"], str(ro.get("error"))[:200]
        io = ro["data"]["verilator_suite_identity"]
        assert io["observed_any"] is True, io
        assert io["verified_any"] is True, io
        assert ro["data"]["overall_status"] == "PASS", ro["data"]["overall_status"]

        # 纯干净日志（无 %Error）不得出现 VERILATOR_ERROR 假报
        rules_ok = {c["rule_id"] for c in ro["data"]["checks"]}
        assert "VERILATOR_ERROR" not in rules_ok, rules_ok


def test_numeric_bounds_are_enforced_at_entry() -> None:
    """Defect 144: schema 的 minimum/maximum 必须在 call_tool 入口强制执行。

    旧实现没有任何数值边界校验，`timeout_sec=-5` 被直接传进
    `subprocess.run(timeout=-5)`，Python 立刻抛 TimeoutExpired 并报出
    「Simulation timed out after -5s」——语义荒谬且把「参数非法」伪装成
    「仿真超时」。`timeout_sec=0` 同理，永远不可能跑完任何仿真。
    """
    print("coverage: numeric bounds enforced at call_tool entry")
    from fpga_toolbox.registry import call_tool, list_tool_schemas

    # schema 必须声明边界（否则校验无从谈起）
    sim_schema = next(s for s in list_tool_schemas() if s["name"] == "run_sim")
    assert sim_schema["inputSchema"]["properties"]["timeout_sec"].get("minimum") == 1, \
        sim_schema["inputSchema"]["properties"]["timeout_sec"]
    mut_schema = next(s for s in list_tool_schemas() if s["name"] == "run_mutation")
    assert mut_schema["inputSchema"]["properties"]["timeout_sec"].get("minimum") == 1, \
        mut_schema["inputSchema"]["properties"]["timeout_sec"]

    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "sim.sh"
        script.write_text(
            "#!/bin/bash\n"
            'echo "== sim: apb (top=tb_apb) =="\n'
            'echo "PASS: apb all 8 checks passed"\n', encoding="utf-8")
        script.chmod(0o755)
        base = {"sim_script": str(script), "expected_suites": ["apb"]}

        for bad in (0, -5, "-3"):
            r = call_tool("run_sim", {**base, "timeout_sec": bad})
            assert r["ok"] is False, (bad, r)
            err = str(r.get("error"))
            assert "must be >= 1" in err, (bad, err[:200])
            # 不能伪装成「仿真超时」
            assert "timed out" not in err, (bad, err[:200])

        # 合法值照常工作；字符串数字（MCP 客户端常见）仍被接受
        for good in (1, 300, "1700"):
            r = call_tool("run_sim", {**base, "timeout_sec": good})
            assert r["ok"] is True, (good, r.get("error"))

        # 省略该参数时默认生效
        r = call_tool("run_sim", base)
        assert r["ok"] is True, r.get("error")


def test_eda_remote_tag_cannot_escape_staging_dir() -> None:
    """Defect 145: tag='..'/'..' 这类纯点 tag 会越界删除远端目录。

    `_TAG_RE = ^[A-Za-z0-9_.-]+$` 允许 '.'，于是 tag='..' 或 tag='.' 通过校验。
    生成脚本把 tag 拼进 `WORK="/dev/shm/{tag}"`，末尾执行 `rm -rf $WORK`：

    - tag='..' → WORK="/dev/shm/.."  → rm -rf /dev/shm/..  (= /dev)
    - tag='.'  → WORK="/dev/shm/."   → rm -rf /dev/shm/.   (= /dev/shm 整目录)

    与已修的 remote_work_dir='/' 是同一类「字符白名单挡不住路径语义」问题，
    只是从 tag 侧绕进来。
    """
    print("coverage: eda_campaign tag cannot escape staging dir")
    from fpga_toolbox.eda_campaign import generate_remote_run_script

    for bad in ("..", ".", "../.."):
        try:
            script = generate_remote_run_script("host1", bad, "run.sh", "~/work")
        except ValueError:
            continue
        raise AssertionError(
            f"tag {bad!r} was accepted; generated WORK line: "
            + next(ln for ln in script.splitlines() if ln.startswith("WORK=")))

    # 正常 tag 必须仍可用
    for good in ("ver1", "v1_2-3", "a.b", "run-2024.11"):
        script = generate_remote_run_script("host1", good, "run.sh", "~/work")
        assert f'WORK="/dev/shm/{good}"' in script, good

    # 其他参数保持原防护
    for args in [
        ("host\nrm -rf /", "t", "run.sh", "~/work"),      # host 换行注入
        ("host", "t", "../evil.sh", "~/work"),             # script 带路径
        ("host", "t", "run.sh", "/"),                      # workdir 根
        ("host", "t", "run.sh", "~/work/.."),              # workdir ..
    ]:
        try:
            generate_remote_run_script(*args)
        except ValueError:
            continue
        raise AssertionError(f"injection accepted: {args}")


def test_package_imports_are_part_of_the_dependency_closure() -> None:
    """Defect 147: `import pkg::*` 依赖的 package 文件必须进 closure。

    `import cfg_pkg::*;` 与 `` `include `` 不同 —— package 通常在自己的文件里，
    必须作为独立编译单元一起编译。旧实现完全不索引 package：

        resolve_deps(top_mod) -> rtl_files=["top.sv","user.sv"]

    user.sv 有 `import cfg_pkg::*;` 但 pkg.sv 不在清单里，Verilator 报
    `%Error: user.sv:2:10: Import package not found: 'cfg_pkg'`，而工具报
    closure_is_complete=true / ok=true。仓库自带的
    examples/axi_apb_ref/axi/axi_apb_bridge.sv 单文件就复现同样错误。
    """
    print("coverage: package imports are part of the closure")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg.sv").write_text(
            "package cfg_pkg;\n  parameter int W = 8;\nendpackage\n", encoding="utf-8")
        (root / "user.sv").write_text(
            "module user_mod (input logic [7:0] d, output logic [7:0] q);\n"
            "  import cfg_pkg::*;\n"
            "  assign q = d;\nendmodule\n", encoding="utf-8")
        (root / "top.sv").write_text(
            "module top_mod (input logic [7:0] d, output logic [7:0] q);\n"
            "  user_mod u (.d(d), .q(q));\nendmodule\n", encoding="utf-8")

        idx = call_tool("index_repo", {"project_root": str(root)})
        assert idx["ok"], str(idx.get("error"))[:200]
        assert idx["data"]["packages"] == {"cfg_pkg": "pkg.sv"}, idx["data"]["packages"]
        assert idx["data"]["modules"]["user_mod"]["package_imports"] == ["cfg_pkg"], \
            idx["data"]["modules"]["user_mod"]

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "top_mod"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        assert data["rtl_files"] == ["pkg.sv", "top.sv", "user.sv"], data["rtl_files"]
        assert data["all_files"] == ["pkg.sv", "top.sv", "user.sv"], data["all_files"]

        # 沙箱必须包含 package 文件
        sb = root / "_sb"
        c = call_tool("copy_sandbox", {
            "project_root": str(root), "sandbox_dir": str(sb),
            "relative_files": data["all_files"]})
        assert c["ok"], str(c.get("error"))[:200]
        assert (sb / "pkg.sv").is_file(), "package file missing from sandbox"

        # 不 import package 的模块不应把 package 拖进来
        (root / "solo.sv").write_text(
            "module solo (input logic a, output logic b);\n"
            "  assign b = a;\nendmodule\n", encoding="utf-8")
        rs = call_tool("resolve_deps", {"project_root": str(root), "target_module": "solo"})
        assert rs["ok"], str(rs.get("error"))[:200]
        assert rs["data"]["rtl_files"] == ["solo.sv"], rs["data"]["rtl_files"]


def test_detected_but_unsupported_protocol_is_distinguished() -> None:
    """Defect 148: 区分「协议没识别到」与「识别到了但没有 TB 模板」。

    分类器能认 11 种协议，emitter 只有 7 种模板（apb/axi4/ahb_lite/ddr3/
    ddr4/hbm/pcie），`axi4_lite` 在分类器里但不在 emitter 里。旧错误信息对
    两种情况一律说「not found」：

      No supported multi-bus interface found ...
      modules: {'axi_master_dut': ['axi4_lite']}

    用户会以为分类器没认出总线，实际是认出了、只是没有对应模板。
    """
    print("coverage: detected-but-unsupported protocols are distinguished")
    from fpga_toolbox.protocol_tb_emitter import SUPPORTED_MULTI_PROTOCOLS, emit_protocol_testbench

    # axi4_lite 必须在分类器里但不在 emitter 支持列表里（前提校验）
    assert "axi4_lite" not in SUPPORTED_MULTI_PROTOCOLS, SUPPORTED_MULTI_PROTOCOLS

    rtl = (
        "module axi_master_dut (\n"
        "  input  logic        aclk,\n"
        "  input  logic        aresetn,\n"
        "  output logic [7:0]  awaddr,\n"
        "  output logic        awvalid,\n"
        "  input  logic        awready,\n"
        "  output logic [31:0] wdata,\n"
        "  output logic [3:0]  wstrb,\n"
        "  output logic        wvalid,\n"
        "  input  logic        wready,\n"
        "  input  logic [1:0]  bresp,\n"
        "  input  logic        bvalid,\n"
        "  output logic        bready,\n"
        "  output logic [7:0]  araddr,\n"
        "  output logic        arvalid,\n"
        "  input  logic        arready,\n"
        "  input  logic [31:0] rdata,\n"
        "  input  logic [1:0]  rresp,\n"
        "  input  logic        rvalid,\n"
        "  output logic        rready\n"
        ");\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "axi_master_dut.sv"
        f.write_text(rtl, encoding="utf-8")

        # auto 模式
        r = emit_protocol_testbench(str(f), str(root / "a"), None)
        assert r["ok"] is False
        err = str(r.get("error"))
        assert "no testbench template" in err, err[:300]
        assert "axi4_lite" in err, err[:300]
        assert "Supported protocols:" in err, err[:300]
        # 不能再说 "not found"（分类器明明找到了）
        assert "No supported multi-bus interface found" not in err, err[:300]
        d = r.get("data") or {}
        assert d["unsupported_protocols"] == ["axi4_lite"], d
        assert d["detected_protocols"] == ["axi4_lite"], d

        # 显式指定该协议也要给出同样清楚的解释
        r2 = emit_protocol_testbench(str(f), str(root / "b"), "axi4_lite")
        assert r2["ok"] is False
        err2 = str(r2.get("error"))
        assert "no testbench template" in err2, err2[:300]

        # 支持的协议照常工作
        ok_src = Path(__file__).resolve().parent.parent / "examples/multi_bus_ref/apb_slave.sv"
        if ok_src.is_file():
            r3 = emit_protocol_testbench(str(ok_src), str(root / "c"), "apb")
            assert r3["ok"] is True, str(r3.get("error"))[:300]


def test_master_role_is_explained_on_direction_mismatch() -> None:
    """Defect 149: 主设备配从设备模板时，错误信息必须指出是角色问题。

    分类器能判定 role="master"，而所有 TB 模板都是从设备视角（驱动请求信号、
    等待 DUT 响应）。对 APB 主设备生成 TB 时方向必然全部相反，旧错误只列
    「port direction(s) disagree with the template」，用户会以为 RTL 端口方向
    写错了，实际是「主设备不能配从设备模板」。
    """
    print("coverage: master role explained on direction mismatch")
    from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench
    from fpga_toolbox.registry import call_tool

    rtl = (
        "module apb_master_dut (\n"
        "  input  logic        clk,\n"
        "  input  logic        rst_n,\n"
        "  output logic        psel,\n"
        "  output logic        penable,\n"
        "  output logic        pwrite,\n"
        "  output logic [7:0]  paddr,\n"
        "  output logic [31:0] pwdata,\n"
        "  output logic [3:0]  pstrb,\n"
        "  input  logic        pready,\n"
        "  input  logic [31:0] prdata,\n"
        "  input  logic        pslverr\n"
        ");\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "apb_master_dut.sv"
        f.write_text(rtl, encoding="utf-8")

        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        ifaces = ex["data"]["modules"][0]["interfaces"]
        assert ifaces and ifaces[0]["role"] == "master", ifaces

        r = emit_protocol_testbench(str(f), str(root / "tb"), "apb")
        assert r["ok"] is False
        err = str(r.get("error"))
        assert "master" in err, err[:400]
        assert "slave" in err, err[:400]
        # 必须解释「模板是从设备视角」
        assert "template" in err.lower(), err[:400]
        d = r.get("data") or {}
        assert d.get("role") == "master", d
        assert d.get("direction_mismatches"), d

        # 从设备 DUT 不受影响
        slave = Path(__file__).resolve().parent.parent / "examples/multi_bus_ref/apb_slave.sv"
        if slave.is_file():
            rs = emit_protocol_testbench(str(slave), str(root / "tb2"), "apb")
            assert rs["ok"] is True, str(rs.get("error"))[:300]


def test_regfile_spec_type_and_value_validation() -> None:
    """Defect 150/151/152: 手写 regfile spec 的类型与数值错误不能静默/崩栈。

    150: `registers` 写成 mapping（`{"CTRL": {...}}`）时，归一化代码
         `for idx, r in enumerate(...)` 迭代到**键字符串**，随后 `r.get(...)`
         抛裸 `AttributeError: 'str' object has no attribute 'get'` —— 未捕获
         异常漏出，错误信息与真正原因（registers 类型不对）毫无关联。
    151: `offset: "zzz"` 经宽容 `_to_int` 静默回退到 `idx*4`，validate_contract
         返回 ok=true + 零诊断，寄存器被悄悄挪位。
    152: `reset: "zz"` 同样静默变成 0。
    """
    print("coverage: regfile spec type and value validation")
    from fpga_toolbox.registry import call_tool

    base = {"$schema": "apb-regfile-spec/v2", "module": "x",
            "bus": {"protocol": "APB", "addr_width": 8, "data_width": 32,
                    "wait_cycles": 0}}

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def _write(name: str, obj) -> str:
            fp = root / name
            fp.write_text(json.dumps(obj), encoding="utf-8")
            return str(fp)

        # 150: registers 不是列表 → 结构化错误，不能是裸 AttributeError
        p1 = _write("bad_regs.json", {**base, "registers": {"CTRL": {"offset": "0x0"}}})
        r1 = call_tool("validate_contract", {"spec_path": p1})
        assert r1["ok"] is False
        e1 = str(r1.get("error"))
        assert "must be a list" in e1, e1[:200]
        assert "AttributeError" not in e1, e1[:200]

        # 元素不是对象 → 也要有明确信息
        p1b = _write("bad_elem.json", {**base, "registers": ["oops"]})
        r1b = call_tool("validate_contract", {"spec_path": p1b})
        assert r1b["ok"] is False
        assert "must be an object" in str(r1b.get("error")), str(r1b.get("error"))[:200]

        # 151: 非法 offset → E005
        p2 = _write("bad_off.json", {**base, "registers": [
            {"name": "R", "offset": "zzz", "attr": "RW", "reset": "0x0"}]})
        r2 = call_tool("validate_contract", {"spec_path": p2})
        assert r2["ok"] is False, r2
        codes2 = [(d["code"], d["message"]) for d in (r2.get("diagnostics") or [])]
        assert any(c == "E005" and "offset" in m for c, m in codes2), codes2

        # 152: 非法 reset → E005
        p3 = _write("bad_rst.json", {**base, "registers": [
            {"name": "R", "offset": "0x0", "attr": "RW", "reset": "zz"}]})
        r3 = call_tool("validate_contract", {"spec_path": p3})
        assert r3["ok"] is False, r3
        codes3 = [(d["code"], d["message"]) for d in (r3.get("diagnostics") or [])]
        assert any(c == "E005" and "reset" in m for c, m in codes3), codes3

        # 合法 spec 不受影响
        p4 = _write("ok.json", {**base, "registers": [
            {"name": "VERSION", "offset": "0x00", "attr": "RO", "reset": "0x00010203"},
            {"name": "CTRL", "offset": "0x04", "attr": "RW", "reset": "0x0"}]})
        r4 = call_tool("validate_contract", {"spec_path": p4})
        assert r4["ok"] is True, str(r4.get("error"))[:200]
        assert r4["data"]["register_count"] == 2, r4["data"]


def test_json_results_unnamed_entries_are_not_pass_evidence() -> None:
    """Defect 153: cocotb JSON 里无名称的结果项不能算「通过证据」。

    XML 分支（缺陷 119）已经改成：`<testcase />` 没有 name 时判
    UNNAMED_TESTCASE FAIL。JSON 分支当时漏了 —— 用
    `t.get("name", t.get("rule_id", "UNKNOWN"))` 兜底成字符串 "UNKNOWN"：

        {"tests": [{"name":"t1","status":"passed"}, {"status":"passed"}]}
        → total=2, passed=2, overall_status=PASS

    一条无从归属的「通过」被当成真实证据。`checks` 分支同样问题。
    """
    print("coverage: unnamed JSON result entries are not pass evidence")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def _run(name: str, obj) -> dict:
            fp = root / name
            fp.write_text(json.dumps(obj), encoding="utf-8")
            return call_tool("normalize_sim_result", {
                "cocotb_results_json": str(fp), "module_name": "m"})

        # tests 分支：无名项 → FAIL
        r = _run("tests.json", {"tests": [
            {"name": "t1", "status": "passed"}, {"status": "passed"}]})
        assert r["ok"], str(r.get("error"))[:200]
        d = r["data"]
        assert d["overall_status"] == "FAIL", d["overall_status"]
        rules = {c["rule_id"]: c["status"] for c in d["checks"]}
        assert rules.get("UNNAMED_TESTCASE") == "FAIL", rules
        assert d["summary"]["failed"] >= 1, d["summary"]

        # 空白 name 同样算无名
        r2 = _run("blank.json", {"tests": [
            {"name": "t1", "status": "passed"}, {"name": "   ", "status": "passed"}]})
        d2 = r2["data"]
        assert d2["overall_status"] == "FAIL", d2["overall_status"]

        # checks 分支：同样语义
        r3 = _run("checks.json", {"checks": [
            {"rule_id": "c1", "status": "PASS"}, {"status": "PASS"}]})
        d3 = r3["data"]
        assert d3["overall_status"] == "FAIL", d3["overall_status"]
        rules3 = {c["rule_id"]: c["status"] for c in d3["checks"]}
        assert rules3.get("UNNAMED_TESTCASE") == "FAIL", rules3

        # 全部有名且通过 → 仍然 PASS（不能假报警）
        r4 = _run("ok.json", {"tests": [
            {"name": "t1", "status": "passed"}, {"name": "t2", "status": "passed"}]})
        d4 = r4["data"]
        assert d4["overall_status"] == "PASS", d4
        assert d4["summary"]["passed"] == 2, d4["summary"]

        # rule_id 作为标识是合法的（tests 分支）
        r5 = _run("ruleid.json", {"tests": [
            {"rule_id": "r1", "status": "passed"}]})
        d5 = r5["data"]
        assert d5["overall_status"] == "PASS", d5


def test_check_lines_and_suite_summary_precedence() -> None:
    """Defect 154/155: `[CHECK]` 明细行不能静默消失，也不能篡改套件结论。

    154: `_parse_verilator_checks` 对没有 `id` 的 `[CHECK]` 行直接 `continue`
         静默丢弃。`[CHECK] status=FAIL exp=1 got=2` 这条明确的失败证据凭空
         消失（与 XML/JSON 无名项口径不一致）。
    155: `verify_suite_identities` 把 `\bFAIL\b` 关键词检查放在**汇总行之前**，
         于是逐事务明细 `[CHECK] ... | status=FAIL | ...` 会让整个套件判 FAIL，
         即使套件自己的汇总行是 `PASS:` —— 实测得到
         observed={'apb':'FAIL'} 而 summaries={'apb':'PASS'} 的自相矛盾。
         汇总行才是套件级结论的权威；无汇总行时 CHECK FAIL 仍是失败证据。
    """
    print("coverage: CHECK lines and suite summary precedence")
    from fpga_toolbox.result_normalizer import _parse_verilator_checks
    from fpga_toolbox.sim_runner import verify_suite_identities

    # 154: 无 id 的 CHECK 行必须保留为 UNNAMED_TESTCASE FAIL
    checks = _parse_verilator_checks("[CHECK] status=FAIL exp=1 got=2\n")
    assert len(checks) == 1, checks
    assert checks[0]["rule_id"] == "UNNAMED_TESTCASE", checks[0]
    assert checks[0]["status"] == "FAIL", checks[0]
    # 有 id 的正常行不受影响
    ok_checks = _parse_verilator_checks("[CHECK] id=C1 | status=PASS\n")
    assert [c["rule_id"] for c in ok_checks] == ["C1"], ok_checks

    # 155: 明细 FAIL + 汇总 PASS → 套件判 PASS（汇总权威）
    log = ("== sim: apb (top=tb_apb) ==\n"
           "[CHECK] id=C1 | status=FAIL | exp=1 | got=2\n"
           "PASS: apb all 8 checks passed\n")
    r = verify_suite_identities(log, ["apb"])
    assert r["observed"]["apb"] == "PASS", r
    assert r["all_verified"] is True, r

    # 明细 FAIL + 汇总 FAIL → FAIL
    log2 = ("== sim: apb ==\n"
            "[CHECK] id=C1 | status=FAIL\n"
            "FAIL: apb 1 errors\n")
    r2 = verify_suite_identities(log2, ["apb"])
    assert r2["observed"]["apb"] == "FAIL", r2

    # 明细 FAIL 且**无**汇总行 → 仍是失败证据（不能因重排而放行）
    log3 = "== sim: apb ==\n[CHECK] id=C1 | status=FAIL\n"
    r3 = verify_suite_identities(log3, ["apb"])
    assert r3["observed"]["apb"] == "FAIL", r3

    # 崩溃痕迹 + 无汇总行 → FAIL
    log4 = "== sim: apb ==\n[CHECK] id=C1 | status=PASS\nSegmentation fault\n"
    r4 = verify_suite_identities(log4, ["apb"])
    assert r4["observed"]["apb"] == "FAIL", r4

    # 干净日志 → PASS
    r5 = verify_suite_identities("== sim: apb ==\nPASS: apb all\n", ["apb"])
    assert r5["all_verified"] is True, r5


def test_mutation_spec_non_object_entries_are_rejected() -> None:
    """Defect 156: mutation spec 里的非对象元素不能被静默丢弃。

    `load_mutations` 对非 dict 元素是 `continue`：

        {"mutations": ["oops", {"id":"m1","file":"dut.sv","old":...,"new":...}]}
        → total=1, evaluated=1, ok=True

    `"oops"` 这条变异凭空消失，用户会以为 spec 里所有变异都跑了。全部是
    `"oops"` 时更糟：total=0/evaluated=0，错误信息说
    "0 error(s), 0 unmatched out of 0"，完全没提元素类型不对。
    """
    print("coverage: mutation spec non-object entries rejected")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "dut.sv").write_text(
            "module dut (input logic a, output logic b);\n"
            "  assign b = a;\nendmodule\n", encoding="utf-8")

        def _run(name: str, spec) -> dict:
            sp = root / name
            sp.write_text(json.dumps(spec), encoding="utf-8")
            return call_tool("run_mutation", {
                "workdir": str(root), "mutation_spec": str(sp),
                "test_cmd": ["true"], "min_score": 0})

        # 全部是非对象 → 必须明确报错，不能说「0 个变异」
        r = _run("all_bad.json", {"mutations": ["oops"]})
        assert r["ok"] is False, r
        err = str(r.get("error"))
        assert "must be objects" in err, err[:250]
        assert "silently skipped" in err, err[:250]
        # 不能伪装成「没有可评估的变异」
        assert "No mutation could be evaluated" not in err, err[:250]

        # 混合：有效项 + 非对象 → 同样拒绝（否则非对象项静默消失）
        r2 = _run("mixed.json", {"mutations": [
            "oops",
            {"id": "m1", "file": "dut.sv", "old": "assign b = a;", "new": "assign b = ~a;"}]})
        assert r2["ok"] is False, r2
        assert "must be objects" in str(r2.get("error")), str(r2.get("error"))[:250]

        # 合法 spec 正常执行
        r3 = _run("ok.json", {"mutations": [
            {"id": "m1", "file": "dut.sv", "old": "assign b = a;", "new": "assign b = ~a;"}]})
        assert r3["ok"] is True, str(r3.get("error"))[:250]
        assert r3["data"]["total"] == 1, r3["data"]
        assert r3["data"]["evaluated"] == 1, r3["data"]

        # 省略 id 时仍自动生成确定性 id（合法用法，不误伤）
        r4 = _run("noid.json", {"mutations": [
            {"file": "dut.sv", "old": "assign b = a;", "new": "assign b = ~a;"}]})
        assert r4["ok"] is True, str(r4.get("error"))[:250]
        assert r4["data"]["results"][0]["id"] == "M01", r4["data"]["results"]


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
    import re

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


def test_min_score_bounds_are_enforced() -> None:
    """Defect 159: min_score 没有 0-100 边界。

    `min_score` 是「可接受的变异杀伤率百分比」，上限必然是 100。旧 schema 只有
    `type: number`：
    - `min_score=150` → 永远不可能达到（分数上限 100%），每次调用都必然失败；
    - `min_score=-1` → 无意义的阈值，任何分数都「达标」。
    两者都应作为非法参数在入口拒绝，而不是伪装成「分数未达标」。
    """
    print("coverage: min_score bounds enforced")
    from fpga_toolbox.registry import call_tool, list_tool_schemas

    schema = next(s for s in list_tool_schemas() if s["name"] == "run_mutation")
    ms_spec = schema["inputSchema"]["properties"]["min_score"]
    assert ms_spec.get("minimum") == 0, ms_spec
    assert ms_spec.get("maximum") == 100, ms_spec

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "dut.sv").write_text(
            "module dut (input logic a, output logic b);\n  assign b = a;\nendmodule\n",
            encoding="utf-8")
        sp = root / "spec.json"
        sp.write_text(json.dumps({"mutations": [
            {"id": "m1", "file": "dut.sv", "old": "assign b = a;",
             "new": "assign b = ~a;"}]}), encoding="utf-8")
        base = {"workdir": str(root), "mutation_spec": str(sp),
                "test_cmd": ["true"]}

        for bad in (-1, -0.5, 101, 150):
            r = call_tool("run_mutation", {**base, "min_score": bad})
            assert r["ok"] is False, (bad, r)
            err = str(r.get("error"))
            assert "must be >=" in err or "must be <=" in err, (bad, err[:200])
            # 不能把「参数非法」伪装成「分数未达标」
            assert "below threshold" not in err, (bad, err[:200])

        # 合法值照常工作
        for good in (0, 50, 100):
            r = call_tool("run_mutation", {**base, "min_score": good})
            # score=0 时 >=0 通过、<50/<100 不通过，两种都算「参数被接受」
            err = str(r.get("error") or "")
            assert "must be >=" not in err and "must be <=" not in err, (good, err[:200])


def test_parser_backend_enum_is_enforced() -> None:
    """Defect 160: parser_backend 描述列了 auto/builtin/slang，却没有 enum。

    旧 schema 只有 `type: string`，于是 `parser_backend="BOGUS"` 与 `""` 都
    返回 ok=true —— 工具静默回退到默认后端，调用方以为进行了后端选择。
    这与 protocol 参数早已加 enum 的做法不一致（拼错的协议名会立即报错）。
    """
    print("coverage: parser_backend enum enforced")
    from fpga_toolbox.registry import call_tool, list_tool_schemas

    schema = next(s for s in list_tool_schemas() if s["name"] == "extract_interfaces")
    spec = schema["inputSchema"]["properties"]["parser_backend"]
    assert spec.get("enum") == ["auto", "builtin", "slang"], spec

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rtl = root / "dut.sv"
        rtl.write_text(
            "module dut (\n"
            "  input  logic        clk,\n"
            "  input  logic        rst_n,\n"
            "  input  logic        psel,\n"
            "  input  logic        penable,\n"
            "  input  logic        pwrite,\n"
            "  input  logic [7:0]  paddr,\n"
            "  input  logic [31:0] pwdata,\n"
            "  input  logic [3:0]  pstrb,\n"
            "  output logic        pready,\n"
            "  output logic [31:0] prdata,\n"
            "  output logic        pslverr\n"
            ");\n  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
            "endmodule\n", encoding="utf-8")

        for bad in ("BOGUS", "", "slan", "builtin2"):
            r = call_tool("extract_interfaces", {"rtl_file": str(rtl),
                                                 "parser_backend": bad})
            assert r["ok"] is False, (bad, r)
            err = str(r.get("error"))
            assert "Allowed values" in err, (bad, err[:200])

        for good in ("auto", "builtin", "slang"):
            r = call_tool("extract_interfaces", {"rtl_file": str(rtl),
                                                 "parser_backend": good})
            # slang 可能因环境缺失而失败，但错误不能是「非法枚举值」
            err = str(r.get("error") or "")
            assert "Allowed values" not in err, (good, err[:200])


def test_staged_rtl_files_reach_the_parser() -> None:
    """Defect 161: `staged_rtl_files` 过去从未传给解析器。

    参数描述承诺 "Optional additional RTL files compiled together (e.g.
    packages) for cross-file imports."，但实现里它**只**出现在返回值的
    `rtl_files` 字段中，没有传给 parse_sv_file / slang 上下文：

        grep staged_rtl_files iface_extractor.py
        → 418: 形参
        → 468: "rtl_files": staged_rtl_files or [...]

    后果：不存在的路径、目录都能通过校验；即使传了真实文件，对解析结果也是
    零影响。slang 的自动收集只认 `*_pkg.sv` / `*_if.sv` 命名，命名不规范的
    package（如 `types.sv`）只能靠这个参数补充 —— 而它没生效。
    """
    print("coverage: staged_rtl_files reach the parser")
    from fpga_toolbox.core.sv_parser import _find_slang_context
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        proj = root / "proj"
        (proj / "rtl").mkdir(parents=True)
        (proj / "inc").mkdir()
        (proj / "inc" / "defs.svh").write_text("`define W 8\n", encoding="utf-8")
        # package 名不规范，slang 自动收集（*_pkg.sv / *_if.sv）抓不到
        (proj / "types.sv").write_text(
            "package types_named_pkg;\n  typedef enum logic [1:0] { A1, A2 } mode_e;\n"
            "endpackage\n", encoding="utf-8")
        rtl = proj / "rtl" / "dut.sv"
        rtl.write_text(
            "module dut\n  import types_named_pkg::*;\n(\n"
            "  input  logic clk,\n  input  logic rst_n,\n"
            "  input  logic psel,\n  input  logic penable,\n  input  logic pwrite,\n"
            "  input  logic [7:0] paddr,\n  input  logic [31:0] pwdata,\n"
            "  input  logic [3:0] pstrb,\n"
            "  output logic pready,\n  output logic [31:0] prdata,\n"
            "  output logic pslverr\n);\n"
            "  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
            "endmodule\n", encoding="utf-8")

        # (a) staged 文件必须真正进入 slang 编译上下文
        auto_extra, _ = _find_slang_context(rtl)
        assert "types.sv" not in " ".join(auto_extra), auto_extra
        staged_extra, _ = _find_slang_context(rtl, [str(proj / "types.sv")])
        assert str((proj / "types.sv").resolve()) in staged_extra, staged_extra

        # (b) 不存在的 staged 条目必须被拒绝（旧实现静默接受）
        r_bad = call_tool("extract_interfaces", {
            "rtl_file": str(rtl), "staged_rtl_files": [str(root / "nope.sv")]})
        assert r_bad["ok"] is False, r_bad
        assert "Invalid staged_rtl_files" in str(r_bad.get("error")), str(r_bad.get("error"))[:200]

        # 目录同样拒绝
        r_dir = call_tool("extract_interfaces", {
            "rtl_file": str(rtl), "staged_rtl_files": [str(proj)]})
        assert r_dir["ok"] is False, r_dir
        assert "directory" in str(r_dir.get("error")), str(r_dir.get("error"))[:200]

        # (c) 相对路径支持三种基准：rtl 目录 / project root / cwd
        for rel in ("types.sv", "proj/types.sv"):
            rr = call_tool("extract_interfaces", {
                "rtl_file": str(rtl), "staged_rtl_files": [rel]})
            # 只有能从某个基准解析到才算合法；解不到就该明确报错（不能静默）
            if not rr["ok"]:
                assert "Invalid staged_rtl_files" in str(rr.get("error")), rr["error"]

        # project-root 相对路径：当 rtl 位于 <root>/rtl/ 时，基准之一是
        # rtl.parent.parent（= <root>），于是 "types.sv" 可直接解析到。
        # 上面已用绝对路径覆盖「合法 staged 可传」；这里补 <root> 相对路径。
        r_ok = call_tool("extract_interfaces", {
            "rtl_file": str(rtl),
            "staged_rtl_files": ["types.sv", "inc/defs.svh"]})
        assert r_ok["ok"] is True, str(r_ok.get("error"))[:250]


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
    from fpga_toolbox.registry import call_tool

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
    from fpga_toolbox.registry import call_tool

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
    test_sv_parser_non_ansi_and_bitfield_width()
    test_protocol_tb_emitter_axi4_ahb_ddr4()
    test_sandbox_clear_dir_marked_nested()
    test_sim_runner_fst_probe()
    test_visual_renderer_rich_project_pages_and_highlight()
    test_all_11_protocol_classifiers_and_clean_pt_registry()
    test_bug_hunt_regressions()
    test_verify_invariants_layer3_tolerates_missing_ruff()
    test_install_git_hook_uses_git_toplevel()
    test_mutation_target_containment()
    test_remote_work_dir_injection_blocked()
    test_cli_atfile_errors_structured()
    test_resolve_deps_path_prefixed_include()
    test_validator_bad_offset_no_crash()
    test_handshake_while_case_insensitive_ready()
    test_handshake_r2_r4_case_insensitive()
    test_ready_raised_before_case_insensitive()
    test_s1_sv_literal_constant_stub()
    test_pcie_tb_matches_dut_parameterization()
    test_module_name_not_used_as_unsafe_filename()
    test_ddr_hbm_templates_follow_port_widths()
    test_axi4_template_follows_id_len_widths()
    test_power_on_reset_names_detected()
    test_review_fixes_mutation_baseline_and_accounting()
    test_review_fixes_index_repo_and_linter_routing()
    test_review_fixes_registry_parser_and_types()
    test_review_fixes_artifact_escaping_and_polarity()
    test_suite_identity_requires_per_suite_summary()
    test_mutation_rejects_empty_old_pattern()
    test_normalizer_rejects_malformed_and_unnamed_results()
    test_visual_renderer_consistency_and_shape_validation()
    test_ddr_required_ports_cover_dq_in()
    test_strobe_width_follows_real_port()
    test_ahb_trans_size_resp_widths_follow_port()
    test_ddr_control_widths_follow_port()
    test_no_user_data_destruction_and_valid_cocotb_json()
    test_security_hardening_injection_xss_verilator_hijack()
    test_silent_pass_gates_uvm_fatal_zero_frames_completed_xml()
    test_residual_and_suite_rerun_gates()
    test_sv_parser_tokenizer_and_header_fixes()
    test_iface_direction_prefix_and_ahb_ready_split()
    test_resolve_deps_transitive_headers()
    test_included_source_not_treated_as_compilation_unit()
    test_rtl_files_keeps_independently_compilable_modules()
    test_tb_builds_for_rtl_without_timescale()
    test_typedef_port_width_placeholders_are_legal()
    test_unexpanded_sv_interface_ports_are_reported()
    test_lint_tb_zero_matches_is_not_silent_clean()
    test_normalizer_surfaces_verilator_errors_and_empty_verification()
    test_numeric_bounds_are_enforced_at_entry()
    test_eda_remote_tag_cannot_escape_staging_dir()
    test_package_imports_are_part_of_the_dependency_closure()
    test_detected_but_unsupported_protocol_is_distinguished()
    test_master_role_is_explained_on_direction_mismatch()
    test_regfile_spec_type_and_value_validation()
    test_json_results_unnamed_entries_are_not_pass_evidence()
    test_check_lines_and_suite_summary_precedence()
    test_mutation_spec_non_object_entries_are_rejected()
    test_tool_schema_descriptions_match_signature_defaults()
    test_min_score_bounds_are_enforced()
    test_parser_backend_enum_is_enforced()
    test_staged_rtl_files_reach_the_parser()
    test_netlist_cleaner_preserves_comments()
    test_netlist_cleaner_ties_off_blackbox_outputs()
    print("\nALL FULL-COVERAGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
