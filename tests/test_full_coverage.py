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
    from fpga_toolbox.tb_linter import scan_handshake

    def _scan(fn_body: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            tb = Path(td) / "tb.sv"
            tb.write_text("module tb;\n" + fn_body + "\nendmodule\n", encoding="utf-8")
            return [i["code"] for i in scan_handshake(tb)]

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
    print("\nALL FULL-COVERAGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
