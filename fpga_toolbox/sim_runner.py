"""Tool 10: Verilator + slang simulation runner with stdbuf log retention and suite identity checks."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result
from .eda_env import probe_eda_env


def verify_suite_identities(log_text: str, expected_suites: List[str]) -> Dict[str, Any]:
    """Ported from agent/verilog/suite_verify.py: verify each named suite passed, not just PASS line count."""
    results: Dict[str, str] = {}
    current: Optional[str] = None
    for line in log_text.splitlines():
        m_hdr = re.search(r"==\s*sim:\s*([A-Za-z0-9_-]+)\s*==", line)
        if m_hdr:
            current = m_hdr.group(1)
            results.setdefault(current, "STARTED")
        if current:
            if re.search(r"\bFAIL\b|\$fatal|ERROR:", line):
                results[current] = "FAIL"
            elif re.search(r"\bPASS\b", line) and results.get(current) != "FAIL":
                results[current] = "PASS"
    missing = [s for s in expected_suites if s not in results]
    failed = [s for s in expected_suites if results.get(s) != "PASS" and s not in missing]
    return {
        "expected": expected_suites,
        "observed": results,
        "missing": missing,
        "failed": failed,
        "all_verified": not missing and not failed,
    }


def tool_run_sim(
    sim_script: str,
    suite: str = "all",
    expected_suites: Optional[List[str]] = None,
    timeout_sec: int = 300,
    seed: Optional[int] = None,
    wait_cycles: Optional[int] = None,
    require_slang: bool = False,
) -> Dict[str, Any]:
    script_p = Path(sim_script)
    if not script_p.is_file():
        return err_result("run_sim", f"Simulation entry script not found: {sim_script}")

    env_info = probe_eda_env()
    if not env_info["verilator"]["meets_min_version"]:
        return err_result(
            "run_sim",
            f"Verilator requirement unmet: {env_info['verilator']['message']}",
            diagnostics=[{"severity": "ERROR", "code": "E020", "path": "eda_env.verilator", "message": env_info["verilator"]["message"]}],
        )
    if require_slang and not env_info["slang"]["available"]:
        return err_result(
            "run_sim",
            "slang is required (require_slang=true) but not found on PATH.",
            diagnostics=[{"severity": "ERROR", "code": "E021", "path": "eda_env.slang", "message": "slang binary not found."}],
        )

    cmd: List[str] = []
    if env_info["stdbuf"]["available"]:
        cmd.extend(["stdbuf", "-oL", "-eL"])
    cmd.extend(["bash", str(script_p.name), suite])

    import os

    run_env = os.environ.copy()
    run_env["VERILATOR"] = str(env_info["verilator"]["path"])
    if seed is not None:
        run_env["SEED"] = str(seed)
    if wait_cycles is not None:
        run_env["WAIT"] = str(wait_cycles)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(script_p.parent),
            env=run_env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return err_result("run_sim", f"Simulation timed out after {timeout_sec}s: {exc}")

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    identity = verify_suite_identities(combined, expected_suites) if expected_suites else None
    data = {
        "script": str(script_p.resolve()),
        "suite": suite,
        "returncode": proc.returncode,
        "eda_env": env_info,
        "suite_identity": identity,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }
    if proc.returncode != 0:
        return err_result("run_sim", f"Simulation exited with code {proc.returncode}.", diagnostics=[{"severity": "ERROR", "code": "E022", "path": suite, "message": data["stderr_tail"][:300] or "non-zero exit"}])
    if identity and not identity["all_verified"]:
        return err_result(
            "run_sim",
            f"Suite identity check failed (missing={identity['missing']}, failed={identity['failed']}).",
            diagnostics=[{"severity": "ERROR", "code": "E023", "path": suite, "message": "Silent skip or failed suite detected."}],
        )
    return ok_result("run_sim", data)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("run_sim"))
