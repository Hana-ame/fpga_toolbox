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




def test_clean_netlist_param_and_comment_edges() -> None:
    """参数化 blackbox 与注释中的 endmodule（审计中发现的 2 个真实缺陷）。

    缺陷 A：实例正则用 `\\((.*?)\\)` 非贪婪，遇到参数化覆盖
    `RDBUS #(.WIDTH(8)) u (...)` 里 `#(` 的第一个 `)` 就截断，导致这类实例
    完全匹配不到（removed_instances 恒为 0）——而这正是文档要解决的主场景。

    缺陷 B：`endmodule` 出现在注释里时，模块切分在注释处提前截断，且插入
    assign 时用 re.sub(r"\\bendmodule\\b") 会命中注释内的 endmodule，把
    assign 语句插进注释中间，产出语法错误的网表。
    """
    # 缺陷 A：参数化实例必须被清理
    param_v = (
        "module t(input a, output [7:0] o);\n"
        "  RDBUS #(.WIDTH(8), .DEPTH(4)) u (.i_data(a), .o_data(o));\n"
        "endmodule\n"
    )
    c1, s1 = clean_netlist_for_pt(param_v)
    assert s1["removed_instances"] == 1, f"parameterized instance not removed: {s1}"
    assert "RDBUS" not in c1
    assert "assign o = 1'b0;" in c1

    # 缺陷 B：注释里的 endmodule 不得破坏网表
    comment_v = (
        "module t(input a, output o);\n"
        "  // comment mentions endmodule but it is not real\n"
        "  RDBUS u (.i_data(a), .o_data(o));\n"
        "endmodule\n"
    )
    c2, s2 = clean_netlist_for_pt(comment_v)
    assert s2["removed_instances"] == 1, f"instance after comment-endmodule not removed: {s2}"
    assert "// comment mentions endmodule but it is not real" in c2, "comment was mutated"
    # assign 必须在真正的 endmodule 之前，而不是插进注释里
    body = c2.split("// comment mentions endmodule but it is not real")[1]
    assert body.lstrip().startswith("\n  RDBUS") is False or "assign o" in body
    assert "assign o = 1'b0;" in c2
    # 注释行内不得出现 assign
    for line in c2.splitlines():
        if "mentions endmodule" in line:
            assert "assign" not in line, f"assign injected into comment: {line}"

    # 缺陷 C：DC 导出的网表常把整个模块压成一行。旧正则锚定行首 `^[ \t]*`，
    # 此时实例前面是 `; ` 而非行首，完全匹配不到（removed=0）。
    oneline_v = "module t(o); output o; RDBUS #(.W(8)) u (.o_data(o)); endmodule"
    c3, s3 = clean_netlist_for_pt(oneline_v)
    assert s3["removed_instances"] == 1, f"one-line module instance not removed: {s3}"
    assert "RDBUS" not in c3

    # 锚点放宽后不得误伤名字里含 RDBUS 的其它模块
    lookalike = (
        "module t(o);\n"
        "  output o;\n"
        "  my_RDBUS_wrapper u (.o_data(o));\n"
        "endmodule\n"
    )
    _, s4 = clean_netlist_for_pt(lookalike)
    assert s4["removed_instances"] == 0, f"lookalike module wrongly removed: {s4}"




def test_aggregate_rejects_toolresult_envelope() -> None:
    """aggregate_seed_medians 误传 ToolResult 外层时必须报错，不能静默返回空。

    回归 51：入参约定是 parse_uvm_log(...)["data"]，但误传整条 ToolResult
    （{ok, tool, data, ...}）时，旧实现静默得出 n_passed=0 / lat_avg_median=None，
    调用方会误以为「所有种子都失败」或「没数据」。
    """
    good_log = (
        "[C3900] FID: delivered=100 fid_bad=0\n"
        "[C3900] NP_LAT: frames=100 min=4 avg=5 max=6\n"
        "UVM_ERROR : 0\n"
    )
    r = parse_uvm_log(good_log, expected_frames=100)
    assert r["ok"]
    # 正确用法
    agg = aggregate_seed_medians([r["data"]] * 5)
    assert agg["n_passed"] == 5 and agg["lat_avg_median"] == 5
    # 错误用法必须显式报错
    try:
        aggregate_seed_medians([r] * 5)
    except ValueError as exc:
        assert "inner data" in str(exc) or "ToolResult" in str(exc)
    else:
        raise AssertionError("wrong-level input silently accepted")


def test_paired_baseline_requires_both_metrics() -> None:
    """bit-exact 必须要求 leaf 与 area 都有值且相等。

    回归 52：旧实现只要求 leaf_cell_count 非 None，于是 total_cell_area 双方
    缺失时 `None == None` 被判为「逐位一致」，bit_exact=True——用不完整数据
    证明核心结论（文档要求两个指标共同证明可复现性）。
    """
    only_leaf = verify_paired_baseline_reproducibility(
        {"leaf_cell_count": 74265}, {"leaf_cell_count": 74265},
    )
    assert only_leaf["ok"] is False, "missing area must not be bit-exact"
    assert only_leaf["data"]["bit_exact"] is False

    full = {"leaf_cell_count": 74265, "total_cell_area": 50656.129}
    both = verify_paired_baseline_reproducibility(full, dict(full))
    assert both["ok"] is True and both["data"]["bit_exact"] is True


def test_remote_script_rejects_shell_injection() -> None:
    """生成脚本会被 scp 到远端执行，参数含 shell 元字符即构成命令注入。

    回归 53：实测 host='x"; touch /tmp/PWNED; "' 会让生成脚本真的执行 touch。
    现在 host/tag/local_script_name 都做白名单校验。
    """
    ok = generate_remote_run_script(
        host="lzc@192.168.108.128", tag="p1_base", local_script_name="run_base.sh",
    )
    assert "lzc@192.168.108.128" in ok and "/dev/shm/p1_base" in ok

    bad_calls = [
        dict(host='x"; touch /tmp/PWNED; "', tag="t", local_script_name="s.sh"),
        dict(host="h$(id)", tag="t", local_script_name="s.sh"),
        dict(host="h`id`", tag="t", local_script_name="s.sh"),
        dict(host="h", tag='t"; touch /tmp/PWNED; "', local_script_name="s.sh"),
        dict(host="h", tag="t", local_script_name="../../etc/passwd"),
        dict(host="h", tag="t", local_script_name="s.sh; rm -rf /"),
    ]
    for kwargs in bad_calls:
        try:
            generate_remote_run_script(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"injection not rejected: {kwargs}")

    # 合法边界仍应工作（IPv6 / 域名 / 下划线 / 连字符）
    for host in ("lzc@[::1]", "user@host.example.com", "h_1", "a.b-c"):
        generate_remote_run_script(host=host, tag="t-1", local_script_name="a.sh")


if __name__ == "__main__":
    test_parse_uvm_log_and_medians()
    test_parse_dc_pt_qor_and_pitfalls()
    test_clean_netlist_for_pt()
    test_clean_netlist_param_and_comment_edges()
    test_aggregate_rejects_toolresult_envelope()
    test_paired_baseline_requires_both_metrics()
    test_remote_script_rejects_shell_injection()
    test_generate_remote_run_script()
    print("ALL EDA CAMPAIGN TESTS PASSED")
