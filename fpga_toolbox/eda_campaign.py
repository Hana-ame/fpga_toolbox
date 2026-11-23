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
_UVM_DELIVERED_RE = re.compile(r"\[C3900\]\s+FID:\s+delivered=(\d+)")
_UVM_FID_BAD_RE = re.compile(r"fid_bad=(\d+)")
_UVM_LAT_RE = re.compile(r"\[C3900\]\s+NP_LAT:\s+frames=(\d+)(?:\s+min=(\d+))?\s+avg=(\d+)(?:\s+max=(\d+))?")
_UVM_RETRY_RE = re.compile(r"all_retry=(\d+)")
_UVM_SENT_RE = re.compile(r"send_nums=(\d+)")
_UVM_ERR_RE = re.compile(r"UVM_ERROR\s*:\s+(\d+)")
_UVM_WIN_SPAN_RE = re.compile(r"win_span=(\d+)")
_UVM_CALIB_RE = re.compile(r"pcs_words=(\d+).*?flips_exp=([0-9.]+).*?flips_act=(\d+)")

# ---- DC / PT QoR Regexes (§2.1, §3.2) ----
_QOR_SLACK_RE = re.compile(r"Critical Path Slack:\s*([-+]?\d+(?:\.\d+)?)")
_QOR_TNS_RE = re.compile(r"Total Negative Slack:\s*([-+]?\d+(?:\.\d+)?)")
_QOR_VIOL_RE = re.compile(r"No\. of Violating Paths:\s*(\d+)")
_QOR_LEAF_RE = re.compile(r"Leaf Cell Count:\s*(\d+)")
_QOR_COMB_RE = re.compile(r"Combinational Area:\s*([0-9.]+)")
_QOR_NONCOMB_RE = re.compile(r"Noncombinat(?:ional|orial) Area:\s*([0-9.]+)")


def parse_uvm_log(log_text: str, expected_frames: Optional[int] = None) -> Dict[str, Any]:
    """Parse a VCS/UVM simulation log and apply strict pass/stale-simv gates."""
    lines = [ln for ln in log_text.splitlines() if ln.strip()]
    diags: List[Dict[str, Any]] = []

    def _last_int(pat: re.Pattern[str]) -> Optional[int]:
        matches = pat.findall(log_text)
        return int(matches[-1]) if matches else None

    delivered = _last_int(_UVM_DELIVERED_RE)
    fid_bad = _last_int(_UVM_FID_BAD_RE)
    all_retry = _last_int(_UVM_RETRY_RE)
    send_nums = _last_int(_UVM_SENT_RE)
    uvm_error = _last_int(_UVM_ERR_RE)

    lat_matches = _UVM_LAT_RE.findall(log_text)
    frames_obs: Optional[int] = None
    lat_min: Optional[int] = None
    lat_avg: Optional[int] = None
    lat_max: Optional[int] = None
    if lat_matches:
        f_s, mn_s, av_s, mx_s = lat_matches[-1]
        frames_obs = int(f_s)
        lat_min = int(mn_s) if mn_s else None
        lat_avg = int(av_s)
        lat_max = int(mx_s) if mx_s else None

    win_spans = [int(x) for x in _UVM_WIN_SPAN_RE.findall(log_text)]
    max_win_span = max(win_spans) if win_spans else None

    calib = _UVM_CALIB_RE.findall(log_text)
    pcs_words = int(calib[-1][0]) if calib else None
    flips_exp = float(calib[-1][1]) if calib else None
    flips_act = int(calib[-1][2]) if calib else None

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
        "max_win_span": max_win_span,
        "calibration": {
            "pcs_words": pcs_words,
            "flips_exp": flips_exp,
            "flips_act": flips_act,
            "act_over_exp": round(flips_act / flips_exp, 4) if (flips_act is not None and flips_exp) else None,
            "actual_rate": (flips_act / pcs_words) if (flips_act is not None and pcs_words) else None,
        } if pcs_words is not None else None,
    }
    return ok_result("parse_uvm_log", data, diagnostics=diags) if passed else err_result(
        "parse_uvm_log",
        f"UVM run gate failed (delivered={delivered}/{target_frames}, fid_bad={fid_bad}, uvm_error={uvm_error}).",
        diagnostics=diags,
    ) | {"data": data}


def aggregate_seed_medians(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute Table-3 style 5-seed medians and aggregate calibration rates."""
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
        return float(m.group(1)) if m else None

    def _first_int(pat: re.Pattern[str], txt: str) -> Optional[int]:
        m = pat.search(txt)
        return int(m.group(1)) if m else None

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

    data = {
        "wns": _first_float(_QOR_SLACK_RE, report_text),
        "tns": _first_float(_QOR_TNS_RE, report_text),
        "violating_paths": _first_int(_QOR_VIOL_RE, report_text),
        "leaf_cell_count": _first_int(_QOR_LEAF_RE, report_text),
        "combinational_area": comb_area,
        "noncombinational_area": noncomb_area,
        "total_cell_area": total_area,
        "async_default": async_sec,
        "timing_clean": (_first_float(_QOR_SLACK_RE, report_text) is not None and _first_float(_QOR_SLACK_RE, report_text) >= 0.0 and _first_int(_QOR_VIOL_RE, report_text) == 0),
    }
    if diags:
        res = err_result("parse_dc_pt_qor", f"Detected {len(diags)} Synopsys DC/PT pitfall(s).", diagnostics=diags)
        res["data"] = data
        return res
    return ok_result("parse_dc_pt_qor", data)


def verify_paired_baseline_reproducibility(run_a: Dict[str, Any], run_b: Dict[str, Any]) -> Dict[str, Any]:
    """Verify bit-exact reproducibility between two baseline DC runs (§2.4 & §8.1)."""
    exact_cells = run_a.get("leaf_cell_count") == run_b.get("leaf_cell_count")
    exact_area = run_a.get("total_cell_area") == run_b.get("total_cell_area")
    ok = exact_cells and exact_area and run_a.get("leaf_cell_count") is not None
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
    """Flatten a Verilog port connection (including concatenations `{a, b}`) into scalar net names."""
    cleaned = expr.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        cleaned = cleaned[1:-1]
    nets: List[str] = []
    for part in cleaned.split(","):
        tok = part.strip()
        if not tok or tok.startswith("1'b") or tok.startswith("0") or tok.isdigit():
            continue
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
    inst_re = re.compile(rf"^[ \t]*(?:{bb_alt})\s+[A-Za-z0-9_\\$]+\s*\((.*?)\)\s*;", re.M | re.S)
    port_re = re.compile(r"\.([A-Za-z0-9_]+)\s*\(([^()]*)\)")
    mod_re = re.compile(r"(module\s+[A-Za-z0-9_\\$]+.*?endmodule)", re.S)

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
            new_block = re.sub(r"\bendmodule\b", f"{assigns}\nendmodule", new_block, count=1)
        return new_block

    cleaned = mod_re.sub(_clean_module, verilog_text)
    return cleaned, {
        "removed_instances": removed_instances,
        "tied_off_nets_count": len(tied_nets_total),
        "tied_off_nets": tied_nets_total,
        "blackbox_modules": list(blackbox_modules),
    }


def generate_remote_run_script(
    host: str,
    tag: str,
    local_script_name: str,
    remote_work_dir: str = "~/work",
    use_dev_shm: bool = True,
) -> str:
    """Generate a csh-safe remote SSH/SCP launcher with /dev/shm staging & progress tracking (§0.1, §9)."""
    shm_line = (
        f'  mkdir -p /dev/shm/{tag} && cd /dev/shm/{tag}\n'
        if use_dev_shm
        else f'  mkdir -p {remote_work_dir}/{tag} && cd {remote_work_dir}/{tag}\n'
    )
    return f"""#!/usr/bin/env bash
# Auto-generated by fpga_toolbox.eda_campaign (csh-safe + /dev/shm tmpfs discipline)
set -euo pipefail
HOST="{host}"
TAG="{tag}"
SCRIPT="{local_script_name}"
SSH_CMD="ssh -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa $HOST"

scp "$SCRIPT" "$HOST:/tmp/$(basename "$SCRIPT")"
$SSH_CMD "bash -c '
{shm_line}  nohup bash /tmp/$(basename "$SCRIPT") > /tmp/${{TAG}}.nohup 2>&1 &
  echo PID=\\$!
'"
echo "Launched $TAG on $HOST. Collect reports after completion:"
echo "  scp -r $HOST:/dev/shm/$TAG/report ./${{TAG}}_report"
echo "  $SSH_CMD \\"bash -c 'rm -rf /dev/shm/$TAG'\\""
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
    cleaned, stats = clean_netlist_for_pt(
        inp.read_text(encoding="utf-8"),
        blackbox_modules=tuple(blackbox_modules) if blackbox_modules else ("RDBUS", "CLKCROSSBUS", "CLKCROSSDFF"),
    )
    outp = Path(out_verilog)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(cleaned, encoding="utf-8")
    stats["out_verilog"] = str(outp.resolve())
    return ok_result("clean_pt_netlist", stats)
