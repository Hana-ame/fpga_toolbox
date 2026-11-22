"""Tool 12: Self-audit tool verifying that verification guardrails fail loudly (no silent passes).

Ported in spirit from agent/verilog/audit.sh: injects synthetic defects into isolated
temp fixtures and confirms that lint_tb, run_mutation (residual guard), normalize_sim_result,
and copy_sandbox all reject invalid/broken inputs with ok=False.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result
from .mutation_runner import tool_run_mutation
from .result_normalizer import tool_normalize_sim_result
from .sandbox_copier import tool_copy_sandbox
from .tb_linter import tool_lint_tb


def tool_audit_harness(strict: bool = True) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="fpga_audit_") as td:
        root = Path(td)

        # A1: lint_tb must catch C1 clocking race & R2 handshake drop
        bad_tb = root / "tb_bad.sv"
        bad_tb.write_text(
            "module tb_bad;\n"
            "  initial begin\n"
            "    @(posedge ACLK);\n"
            "    axi.AWVALID = 1'b1;\n"
            "    if (axi.BVALID) begin axi.BREADY = 1'b0; end\n"
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )
        r_lint = tool_lint_tb(str(bad_tb))
        codes = {d.get("code") for d in r_lint.get("diagnostics", [])}
        a1_ok = (not r_lint["ok"]) and ("C1" in codes) and ("R2" in codes)
        checks.append({"id": "A1_LINT_FAULT_CATCH", "passed": a1_ok, "detail": f"ok={r_lint['ok']} codes={sorted(codes)}"})

        # A2: run_mutation must refuse residual unrestored mutation (E030)
        mut_dir = root / "mut_proj"
        mut_dir.mkdir()
        (mut_dir / "dut.sv").write_text("assign y = a + b;\n", encoding="utf-8")
        spec = root / "muts.json"
        spec.write_text('[{"id":"M01","file":"dut.sv","old":"a - b","new":"a + b","desc":"residual"}]', encoding="utf-8")
        r_mut = tool_run_mutation(str(mut_dir), str(spec), ["python3", "-c", "pass"])
        m_codes = {d.get("code") for d in r_mut.get("diagnostics", [])}
        a2_ok = (not r_mut["ok"]) and ("E030" in m_codes)
        checks.append({"id": "A2_MUTATION_RESIDUAL_GUARD", "passed": a2_ok, "detail": f"ok={r_mut['ok']} codes={sorted(m_codes)}"})

        # A3: normalize_sim_result must reject unrecognized JSON instead of silent PASS
        junk = root / "junk.json"
        junk.write_text('{"foo": "bar"}', encoding="utf-8")
        r_norm = tool_normalize_sim_result("audit_mod", cocotb_results_json=str(junk))
        a3_ok = not r_norm["ok"]
        checks.append({"id": "A3_NORMALIZER_REJECT_JUNK", "passed": a3_ok, "detail": f"ok={r_norm['ok']}"})

        # A4: copy_sandbox must refuse targeting project root
        proj = root / "proj"
        proj.mkdir()
        (proj / "a.sv").write_text("module a; endmodule\n", encoding="utf-8")
        r_sb = tool_copy_sandbox(str(proj), ["a.sv"], str(proj))
        a4_ok = not r_sb["ok"]
        checks.append({"id": "A4_SANDBOX_ROOT_GUARD", "passed": a4_ok, "detail": f"ok={r_sb['ok']}"})

    failed = [c for c in checks if not c["passed"]]
    data = {"total": len(checks), "passed": len(checks) - len(failed), "failed": len(failed), "checks": checks}
    if failed and strict:
        return err_result(
            "audit_harness",
            f"Self-audit failed on {len(failed)} check(s): {[c['id'] for c in failed]}",
            diagnostics=[{"severity": "ERROR", "code": "E040", "path": c["id"], "message": c["detail"]} for c in failed],
        )
    return ok_result("audit_harness", data)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("audit_harness"))
