"""Unit tests for fpga_toolbox.eda_campaign (Synopsys VCS/UVM, DC K-2015, PrimeTime K-2015)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpga_toolbox.eda_campaign import (
    aggregate_seed_medians,
    clean_netlist_for_pt,
    generate_remote_run_script,
    parse_dc_pt_qor,
    parse_uvm_log,
    tool_clean_pt_netlist,
    verify_paired_baseline_reproducibility,
)


def test_parse_uvm_log_and_medians() -> None:
    sample_log = """
[C3900] FID: delivered=10000 fid_bad=0
[C3900] NP_LAT: frames=10000 min=4 avg=5 max=5
all_retry=190 send_nums=10190
win_span=29
win_span=61
pcs_words=61224 flips_exp=60.0 flips_act=6
UVM_ERROR :    0
"""
    r = parse_uvm_log(sample_log, expected_frames=10000)
    assert r["ok"] and r["data"]["passed"] is True
    assert r["data"]["lat_min"] == 4 and r["data"]["lat_avg"] == 5 and r["data"]["lat_max"] == 5
    assert r["data"]["max_win_span"] == 61
    assert r["data"]["calibration"]["act_over_exp"] == 0.1

    # Stale simv (1-line log without UVM_ERROR summary) must fail with E050
    r_stale = parse_uvm_log("Chronologic VCS simulator copyright...\n", expected_frames=1000)
    assert not r_stale["ok"]
    assert any(d["code"] == "E050" for d in r_stale["diagnostics"])

    # 5-seed median aggregation
    agg = aggregate_seed_medians([r["data"]] * 5)
    assert agg["n_passed"] == 5 and agg["lat_avg_median"] == 5 and agg["calib_act_over_exp"] == 0.1


def test_parse_dc_pt_qor_and_pitfalls() -> None:
    good_qor = """
  Timing Path Group 'pcs_clk'
  Critical Path Slack:              0.00
  Total Negative Slack:             0.00
  No. of Violating Paths:           0
  Leaf Cell Count:                  74265
  Combinational Area:               9361.371
  Noncombinational Area:            41294.758

  Timing Path Group '**async_default**' (max_delay/setup)
  Critical Path Slack:              0.0042
  Total Negative Slack:             0.0000
  No. of Violating Paths:           0
"""
    r = parse_dc_pt_qor(good_qor)
    assert r["ok"] and r["data"]["timing_clean"] is True
    assert r["data"]["leaf_cell_count"] == 74265
    assert abs(r["data"]["total_cell_area"] - 50656.129) < 1e-3
    assert r["data"]["async_default"]["wns"] == 0.0042

    # Paired baseline bit-exact check
    pb = verify_paired_baseline_reproducibility(r["data"], dict(r["data"]))
    assert pb["ok"] and pb["data"]["bit_exact"] is True

    # Pitfall E046: DC fake-success (<700B + No paths. + UID-423)
    bad_dc = parse_dc_pt_qor("No paths.\n", "Error: Invalid path group name 'async_default' (UID-423)\n")
    assert not bad_dc["ok"]
    assert any(d["code"] == "E046" for d in bad_dc["diagnostics"])

    # Pitfall E047 (UID-119) + E048 (UID-530) + E049 (Signal 15)
    bad_ops = parse_dc_pt_qor("No paths.\n", "UID-119\nUID-530\nReceived Signal 15\n")
    codes = {d["code"] for d in bad_ops["diagnostics"]}
    assert {"E046", "E047", "E048", "E049"}.issubset(codes)


def test_clean_netlist_for_pt() -> None:
    raw_v = """
module sub_cfg (input clk, output [1:0] q_a, output q_b);
  wire n0, n1, n2;
  RDBUS u_rd0 (.i_bus({n0, n1}), .o_bus_sample({q_a[1], q_a[0]}));
  CLKCROSSDFF u_cdc0 (.i_d(n2), .o_d_dout(q_b));
endmodule
"""
    cleaned, stats = clean_netlist_for_pt(raw_v)
    assert stats["removed_instances"] == 2
    assert stats["tied_off_nets_count"] == 3
    assert "RDBUS u_rd0" not in cleaned and "CLKCROSSDFF u_cdc0" not in cleaned
    # Must emit scalar assigns (NOT assign {a,b}=0 which PT rejects)
    assert "assign q_a[1] = 1'b0;" in cleaned
    assert "assign q_a[0] = 1'b0;" in cleaned
    assert "assign q_b = 1'b0;" in cleaned
    assert "assign {" not in cleaned

    with tempfile.TemporaryDirectory() as td:
        in_p = Path(td) / "in.v"
        out_p = Path(td) / "out.v"
        in_p.write_text(raw_v, encoding="utf-8")
        res = tool_clean_pt_netlist(str(in_p), str(out_p))
        assert res["ok"] and out_p.is_file()


def test_generate_remote_run_script() -> None:
    sh = generate_remote_run_script("lzc@192.168.108.128", "p1_base", "run_base.sh", use_dev_shm=True)
    assert "+ssh-rsa" in sh
    assert "bash -c" in sh
    assert "/dev/shm/p1_base" in sh


if __name__ == "__main__":
    test_parse_uvm_log_and_medians()
    test_parse_dc_pt_qor_and_pitfalls()
    test_clean_netlist_for_pt()
    test_generate_remote_run_script()
    print("ALL EDA CAMPAIGN TESTS PASSED")
