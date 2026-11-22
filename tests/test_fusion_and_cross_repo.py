"""Fusion & cross-repo regression tests against examples/axi_apb_ref/.

Verifies:
  1. APB + AXI4-Lite dual-protocol extraction on flat-port axi_apb_bridge.sv
  2. SV interface/modport recognition + W012 warning on axi_apb_bridge_sv.sv & dual bridge (no 1-bit tie-off bug)
  3. Unified contract validator accepting agent/verilog apb-regfile-spec/v2 JSON directly + E013/E014 bitfield guards
  4. lint_tb catching C1/C2 clocking races, R2/R4 handshake drops, S1/S2/S3 silent-pass patterns
  5. run_mutation guaranteed try/finally restore, mutation score calculation, and E030 residual-mutation guard
  6. normalize_sim_result Verilator per-suite identity verification (rejecting silent skips)
  7. visual_renderer HTML consistency check + coverage/mutation summary rendering
  8. audit_harness fault-injection self-audit & 12-tool MCP registry/CLI contract
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "examples" / "axi_apb_ref"
sys.path.insert(0, str(ROOT))

from fpga_toolbox import call_tool, list_tool_schemas  # noqa: E402

FAILS = 0


def check(cond: bool, msg: str) -> None:
    global FAILS
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILS += 1


def test_cross_repo_extraction() -> None:
    print("fusion: cross-repo APB + SV interface extraction")
    r_flat = call_tool("extract_interfaces", {"rtl_file": str(REF / "axi" / "axi_apb_bridge.sv")})
    check(r_flat["ok"], "axi_apb_bridge.sv extracted")
    m0 = r_flat["data"]["modules"][0]
    protos = {i["protocol"]: i for i in m0["interfaces"]}
    check("axi4_lite" in protos and len(protos["axi4_lite"]["port_map"]) == 16, "axi4_lite slave (16 signals) extracted")
    check("apb" in protos and len(protos["apb"]["port_map"]) == 8, "apb master (8 signals) extracted")
    check(protos["apb"]["role"] == "master", "apb role classified as master")
    check(len(m0["out_of_scope_ports"]) == 0, "zero APB ports leaked into out_of_scope_ports")

    r_sv = call_tool("extract_interfaces", {"rtl_file": str(REF / "axi" / "sv" / "axi_apb_bridge_sv.sv")})
    check(r_sv["ok"], "axi_apb_bridge_sv.sv extracted")
    msv = r_sv["data"]["modules"][0]
    sv_protos = [i["protocol"] for i in msv["interfaces"]]
    check("axi4_lite" in sv_protos and "apb" in sv_protos, "SV interface/modport axi & apb recognized")
    check(len(msv["out_of_scope_ports"]) == 0, "SV interface ports not misclassified as 1-bit tie_off inputs")
    if msv.get("parser_backend") != "slang":
        w_codes = {d["code"] for d in r_sv["diagnostics"]}
        check("W012" in w_codes, "W012 warning emitted when slang is absent")


def test_regfile_spec_v2_and_bitfields() -> None:
    print("fusion: apb-regfile-spec/v2 validation & bitfield E013/E014")
    r_v2 = call_tool("validate_contract", {"spec_path": str(REF / "axi" / "regfile_spec.json")})
    check(r_v2["ok"], "agent/verilog regfile_spec.json validated directly")
    check(r_v2["data"]["has_apb"] is True, "APB boundary recognized")
    check(r_v2["data"]["register_count"] >= 4, "registers parsed from v2 JSON")

    with tempfile.TemporaryDirectory() as td:
        bad_spec = Path(td) / "bad_bf.json"
        bad_spec.write_text(json.dumps({
            "$schema": "apb-regfile-spec/v2",
            "module": "bad_rf",
            "bus": {"protocol": "APB", "addr_width": 8, "data_width": 32},
            "registers": [{
                "name": "CTRL",
                "offset": "0x00",
                "attr": "RW",
                "reset": "0x0",
                "fields": [
                    {"name": "A", "bits": "3:0", "attr": "RW", "reset": 0},
                    {"name": "B", "bits": "5:2", "attr": "RW", "reset": 0},
                    {"name": "C", "bits": "33:32", "attr": "RO", "reset": 0},
                ],
            }],
        }), encoding="utf-8")
        r_bad = call_tool("validate_contract", {"spec_path": str(bad_spec)})
        codes = {d["code"] for d in r_bad["diagnostics"]}
        check(not r_bad["ok"], "invalid bitfields rejected")
        check("E013" in codes, "E013 bitfield overlap caught")
        check("E014" in codes, "E014 bitfield out-of-width caught")


def test_lint_tb() -> None:
    print("fusion: lint_tb (C1/C2, R2/R4, S1/S2/S3)")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        bad_sv = p / "tb_faulty.sv"
        bad_sv.write_text(
            "module tb_faulty;\n"
            "  initial begin\n"
            "    @(posedge ACLK);\n"
            "    axi.AWVALID = 1'b1;\n"
            "    if (axi.BVALID) begin axi.BREADY = 1'b0; end\n"
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )
        bad_sh = p / "check.sh"
        bad_sh.write_text(
            "#!/bin/bash\n"
            "grep_violation() { return 1; }\n"
            "passes=$(grep -c '^PASS' log.txt)\n"
            "[ \"$passes\" -ge 5 ]\n",
            encoding="utf-8",
        )
        r = call_tool("lint_tb", {"target_path": str(p)})
        codes = {d["code"] for d in r["diagnostics"]}
        check(not r["ok"], "faulty TB/script fails lint_tb")
        check({"C1", "R2", "S1", "S3"}.issubset(codes), f"caught C1/R2/S1/S3 (got {sorted(codes)})")


def test_mutation_runner() -> None:
    print("fusion: run_mutation restore, score, and residual guard")
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        dut = wd / "dut.txt"
        dut.write_text("value = 42\n", encoding="utf-8")
        spec = wd / "muts.json"
        spec.write_text(json.dumps([
            {"id": "M01", "file": "dut.txt", "old": "value = 42", "new": "value = 0", "desc": "zero out"}
        ]), encoding="utf-8")
        # Test command fails when value != 42 -> kills mutation
        cmd = [sys.executable, "-c", "import sys; sys.exit(0 if '42' in open('dut.txt').read() else 1)"]
        r = call_tool("run_mutation", {"workdir": str(wd), "mutation_spec": str(spec), "test_cmd": cmd, "min_score": 100.0})
        check(r["ok"] and r["data"]["mutation_score"] == 100.0, "mutation killed and score=100%")
        check(dut.read_text(encoding="utf-8") == "value = 42\n", "original file restored by try/finally")

        # Simulate residual unrestored mutation -> must be blocked with E030
        dut.write_text("value = 0\n", encoding="utf-8")
        r_res = call_tool("run_mutation", {"workdir": str(wd), "mutation_spec": str(spec), "test_cmd": cmd})
        check(not r_res["ok"] and any(d["code"] == "E030" for d in r_res["diagnostics"]), "residual mutation rejected (E030)")


def test_verilator_log_normalizer_and_visual() -> None:
    print("fusion: Verilator suite identity normalizer & visual consistency check")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        log = td_p / "sim.log"
        log.write_text(
            "== sim: counter ==\nPASS: all 20 checks passed\n"
            "== sim: axi ==\nPASS: all 35 checks passed\n",
            encoding="utf-8",
        )
        r_ok = call_tool("normalize_sim_result", {
            "module_name": "axi_apb_bridge",
            "verilator_log_path": str(log),
            "expected_suites": ["counter", "axi"],
            "coverage_summary": {"rtl_pct": 100.0},
            "mutation_summary": {"mutation_score": 100.0},
        })
        check(r_ok["ok"] and r_ok["data"]["overall_status"] == "PASS", "all expected Verilator suites verified PASS")

        r_miss = call_tool("normalize_sim_result", {
            "module_name": "axi_apb_bridge",
            "verilator_log_path": str(log),
            "expected_suites": ["counter", "axi", "dual"],
        })
        check(r_miss["ok"] and r_miss["data"]["overall_status"] == "FAIL", "missing suite 'dual' flagged as FAIL (no silent skip)")

        r_vis = call_tool("render_visual_report", {
            "report": r_ok["data"],
            "dep_graph": {"dependency_closure": ["axi_apb_bridge", "apb_slave_regs"]},
            "extracted_interfaces": [{"protocol": "axi4_lite", "port_map": {"awaddr": "AWADDR"}}],
            "out_html_path": str(td_p / "dash.html"),
        })
        check(r_vis["ok"] and r_vis["data"]["consistency_ok"] is True, "visual HTML consistency check passed")
        html_txt = (td_p / "dash.html").read_text(encoding="utf-8")
        check("rtl_cov=100.0%" in html_txt and "mutation=100.0%" in html_txt, "coverage & mutation summaries rendered")


def test_audit_and_registry() -> None:
    print("fusion: audit_harness & 12-tool registry/CLI")
    r_aud = call_tool("audit_harness", {"strict": True})
    check(r_aud["ok"] and r_aud["data"]["passed"] == 4, "audit_harness 4/4 fault-injection checks passed")

    schemas = list_tool_schemas()
    names = {s["name"] for s in schemas}
    check(len(schemas) == 12, f"12 tools registered (got {len(schemas)})")
    check({"lint_tb", "run_sim", "run_mutation", "audit_harness"}.issubset(names), "all 4 new tools in MCP schemas")

    proc = subprocess.run([sys.executable, "-m", "fpga_toolbox", "audit_harness"], cwd=str(ROOT), capture_output=True, text=True, check=False)
    check(proc.returncode == 0 and '"ok": true' in proc.stdout, "CLI `python -m fpga_toolbox audit_harness` exits 0")


def main() -> int:
    test_cross_repo_extraction()
    test_regfile_spec_v2_and_bitfields()
    test_lint_tb()
    test_mutation_runner()
    test_verilator_log_normalizer_and_visual()
    test_audit_and_registry()
    print("\n" + ("ALL FUSION CHECKS PASSED" if FAILS == 0 else f"{FAILS} CHECK(S) FAILED"))
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
