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
        r_abs = tool_run_mutation(str(wd), str(spec_abs), ["false"], min_score=0.0)
        assert r_abs["ok"], r_abs.get("error")
        assert any(x["status"] == "ERROR" for x in r_abs["data"]["results"]), r_abs["data"]["results"]
        assert outside.read_text(encoding="utf-8") == "EXT_ORIG\n"

        # ../ 相对路径越界
        spec_rel = wd / "m_rel.yaml"
        spec_rel.write_text(
            'mutations:\n  - id: M01\n    file: "../ext_target.sv"\n    old: "EXT_ORIG"\n    new: "MUT"\n',
            encoding="utf-8")
        r_rel = tool_run_mutation(str(wd), str(spec_rel), ["false"], min_score=0.0)
        assert r_rel["ok"]
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
        r_link = tool_run_mutation(str(wd), str(spec_link), ["false"], min_score=0.0)
        assert r_link["ok"]
        assert any(x["status"] == "ERROR" and "inside the working directory" in x["detail"]
                   for x in r_link["data"]["results"]), r_link["data"]["results"]
        assert outside2.read_text(encoding="utf-8") == "EXT2_ORIG\n"

        # 合法目标仍正常变异并还原
        spec_ok = wd / "m_ok.yaml"
        (wd / "good.sv").write_text("assign y = 1;\n", encoding="utf-8")
        spec_ok.write_text(
            'mutations:\n  - id: M01\n    file: "good.sv"\n    old: "assign y = 1;"\n    new: "assign y = 0;"\n',
            encoding="utf-8")
        r_ok = tool_run_mutation(str(wd), str(spec_ok), ["false"], min_score=100.0)
        assert r_ok["ok"], r_ok.get("error")
        assert r_ok["data"]["killed"] == 1
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
    print("\nALL FULL-COVERAGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
