"""Agent 控制面：eda_campaign 解析工具 + run_sim 后端分发。

这组用例全部使用**夹具日志**，不依赖任何商业 EDA（vcs / dc_shell / pt_shell）
许可证，也不依赖 Verilator —— 因为它们验证的是「拿别人跑出来的日志做解析」，
这正是无许可证机器与 Agent 场景的常态。

覆盖：
  1. 4 个新注册工具能被 registry / MCP 看到（控制面缺口回归）。
  2. parse_uvm_log：干净日志通过、UVM_FATAL / 帧数不符 / 缺字段 全部拦下。
  3. parse_dc_pt_qor：UID-423 与迷你 "No paths." 报告报 E046。
  4. aggregate_seed_medians：中位数聚合 + 非列表输入拒绝。
  5. generate_remote_run_script：注入串/危险路径拒绝，合法输入产出脚本。
  6. run_sim 后端分发：非法 backend、形态不符（Makefile vs shell）都被拒绝；
     iverilog 真实运行闭环（若装了 iverilog）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fpga_toolbox.registry import TOOL_REGISTRY, call_tool, list_tool_schemas  # noqa: E402

#: 一份**完整**的 UVM 汇总日志。注意门禁字段必须齐全：
#: fid_bad 缺失会被解析成 None（"不知道"），而门禁要求 fid_bad == 0，
#: 所以不完整的日志**不应该**通过 —— 这是解析器的防假通过设计。
CLEAN_UVM_LOG = (
    "UVM_INFO @ 0: reporter [RNTST] Running test case0...\n"
    "UVM_INFO : scoreboard delivered=1000 send_nums=1000 fid_bad=0 all_retry=0\n"
    # 延迟行必须用真实报告的 [C3900] NP_LAT: 前缀（解析器按该格式抽取）
    "[C3900] NP_LAT: frames=1000 min=12 avg=20 max=48\n"
    "UVM_ERROR : 0\n"
    "UVM_FATAL : 0\n"
    "PASSED: all frames delivered\n"
)

#: 真实风格的 DC QoR 报告：含 UID-423 陷阱（async_default 组在 DC 里不支持）
DC_QOR_WITH_TRAP = (
    "****************************************\n"
    "Report : timing\n"
    "Warning: UID-423 Invalid path group name 'async_default' (ignored).\n"
    + "Path group: **async_default**\n" * 40
)


def test_eda_tools_are_exposed_to_agents() -> None:
    """4 个解析工具必须同时出现在 registry 与 MCP tools/list 里。

    控制面缺口的表现是「工具函数存在、Agent 调不到」：注册表与 MCP 是两个
    独立出口，只改一个就会重现问题。
    """
    print("coverage: eda_campaign tools are exposed via registry and MCP")
    expected = {
        "parse_uvm_log", "parse_dc_pt_qor",
        "aggregate_seed_medians", "generate_remote_run_script",
    }
    assert expected <= set(TOOL_REGISTRY), sorted(expected - set(TOOL_REGISTRY))
    exposed = {t["name"] for t in list_tool_schemas()}
    assert exposed == set(TOOL_REGISTRY), (
        f"tools/list mismatch: only-registry={sorted(set(TOOL_REGISTRY) - exposed)}, "
        f"only-mcp={sorted(exposed - set(TOOL_REGISTRY))}")
    assert expected <= exposed, sorted(expected - exposed)
    # 每个工具都必须能通过 call_tool 分发（名字打错会得到 Unknown tool）
    for name in sorted(expected):
        r = call_tool(name, {"runs": []} if name == "aggregate_seed_medians"
                      else {"host": "h", "tag": "t", "local_script_name": "r.sh"}
                      if name == "generate_remote_run_script" else {})
        assert r["tool"] == name, (name, r["tool"])


def test_parse_uvm_log_fixtures() -> None:
    print("coverage: parse_uvm_log accepts clean logs and rejects bad ones")
    ok = call_tool("parse_uvm_log", {"log_text": CLEAN_UVM_LOG, "expected_frames": 1000})
    assert ok["ok"], ok.get("error")
    d = ok["data"]
    assert d["delivered"] == 1000, d
    assert d["fid_bad"] == 0 and d["uvm_error"] == 0 and d["uvm_fatal"] == 0, d
    assert d["lat_min"] == 12 and d["lat_avg"] == 20 and d["lat_max"] == 48, d

    # UVM_FATAL 必须判失败（致命错误后仿真结果不可信）
    fat = call_tool("parse_uvm_log", {"log_text": CLEAN_UVM_LOG.replace(
        "UVM_FATAL : 0", "UVM_FATAL : 2")})
    assert fat["ok"] is False, fat

    # 交付帧数与预期不符必须判失败（静默少跑是典型的假通过）
    short = call_tool("parse_uvm_log", {"log_text": CLEAN_UVM_LOG, "expected_frames": 2000})
    assert short["ok"] is False, short

    # 缺 fid_bad 字段：解析为 None，不能当成 0 通过
    partial = call_tool("parse_uvm_log", {"log_text": CLEAN_UVM_LOG.replace(
        " fid_bad=0", "")})
    assert partial["ok"] is False, "incomplete log must not pass"

    # 空输入：必须给出结构化错误（说明要 log_text 或 log_path），不是崩栈
    empty = call_tool("parse_uvm_log", {})
    assert empty["ok"] is False and "log_text" in (empty.get("error") or ""), empty

    # 文件路径输入
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "uvm.log"
        f.write_text(CLEAN_UVM_LOG, encoding="utf-8")
        byp = call_tool("parse_uvm_log", {"log_path": str(f), "expected_frames": 1000})
        assert byp["ok"], byp.get("error")
        assert byp["data"]["delivered"] == 1000, byp["data"]
        # 不存在的路径要给明确错误
        miss = call_tool("parse_uvm_log", {"log_path": str(Path(td) / "nope.log")})
        assert miss["ok"] is False and "not found" in (miss.get("error") or ""), miss


def test_parse_dc_pt_qor_trap_detection() -> None:
    print("coverage: parse_dc_pt_qor flags K-2015.06 fake-success pitfalls")
    r = call_tool("parse_dc_pt_qor", {"report_text": DC_QOR_WITH_TRAP})
    codes = {d.get("code") for d in (r.get("diagnostics") or [])}
    # UID-423（async_default 组在 DC 里不支持）必须被识别为 E046
    assert "E046" in codes, (codes, r.get("data"))
    # 迷你 "No paths." 报告同样是假成功信号
    tiny = call_tool("parse_dc_pt_qor", {"report_text": "No paths.\n"})
    tiny_codes = {d.get("code") for d in (tiny.get("diagnostics") or [])}
    assert "E046" in tiny_codes, tiny_codes
    # 空输入要结构化报错
    empty = call_tool("parse_dc_pt_qor", {})
    assert empty["ok"] is False, empty
    # 通过 report_path 读取
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "qor.rpt"
        f.write_text(DC_QOR_WITH_TRAP, encoding="utf-8")
        byp = call_tool("parse_dc_pt_qor", {"report_path": str(f)})
        assert "E046" in {d.get("code") for d in (byp.get("diagnostics") or [])}, byp


def test_aggregate_seed_medians_tool() -> None:
    print("coverage: aggregate_seed_medians tool")
    # 入参是 parse_uvm_log(...)["data"] 的**内层数据**（不是 ToolResult 信封）
    runs = [
        {"passed": True, "lat_avg": 20, "retry_rate": 0.0},
        {"passed": True, "lat_avg": 22, "retry_rate": 0.01},
        {"passed": True, "lat_avg": 24, "retry_rate": 0.02},
    ]
    r = call_tool("aggregate_seed_medians", {"runs": runs})
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["n_runs"] == 3 and d["n_passed"] == 3, d
    assert d["lat_avg_median"] == 22, d
    # 误传整条 ToolResult 信封时，必须给出**可操作**的错误而不是静默算出
    # n_passed=0（旧实现的表现）
    envelope = {"ok": True, "tool": "parse_uvm_log", "data": runs[0]}
    misleading = call_tool("aggregate_seed_medians", {"runs": [envelope]})
    assert misleading["ok"] is False, misleading
    msg = (misleading.get("error") or "").lower()
    assert "inner data" in msg or "envelope" in msg, msg
    # 非列表必须被拒绝（把 dict 当 runs 静默算错是最危险的情形）。
    # 注册层的 schema 类型校验会比工具内部更早拦住，信息也更直接
    # （"expected array, got str"），这里接受任一层给出的明确错误。
    bad = call_tool("aggregate_seed_medians", {"runs": "not-a-list"})
    assert bad["ok"] is False, bad
    msg_l = (bad.get("error") or "").lower()
    assert "list" in msg_l or "array" in msg_l, bad
    # 列表元素不是对象也要拒绝并指出下标
    bad2 = call_tool("aggregate_seed_medians", {"runs": [{"seed": 1}, "oops"]})
    assert bad2["ok"] is False and "runs[1]" in (bad2.get("error") or ""), bad2


def test_generate_remote_run_script_tool() -> None:
    print("coverage: generate_remote_run_script tool")
    ok = call_tool("generate_remote_run_script", {
        "host": "user@host1", "tag": "run01", "local_script_name": "run.sh"})
    assert ok["ok"], ok.get("error")
    d = ok["data"]
    assert d["script"].startswith("#!") or "#!/" in d["script"], d["script"][:60]
    assert d["bytes"] > 100 and len(d["sha256_prefix"]) == 16, d

    # shell 注入必须被拒（不只是"过滤"，而是拒绝生成）
    for host in ("h;rm -rf /", "h`id`", "h$(id)", "h\nrm -rf /"):
        r = call_tool("generate_remote_run_script", {
            "host": host, "tag": "t", "local_script_name": "run.sh"})
        assert r["ok"] is False, f"injection accepted: {host!r}"
        assert (r.get("diagnostics") or [{}])[0].get("code") == "E024", r

    # 纯点 tag 会让 `rm -rf $WORK` 删掉超范围目录，必须拒绝。
    # 注意 `a..b` 是**安全**的：它作为单个路径段解析成 /dev/shm/a..b，
    # 不会发生目录穿越（`..` 只有单独成段时才有语义），因此不在此列 ——
    # 过度拒绝合法 tag 同样是缺陷。
    for tag in (".", ".."):
        r = call_tool("generate_remote_run_script", {
            "host": "h", "tag": tag, "local_script_name": "run.sh"})
        assert r["ok"] is False, f"dangerous tag accepted: {tag!r}"
    safe_dots = call_tool("generate_remote_run_script", {
        "host": "h", "tag": "a..b", "local_script_name": "run.sh"})
    assert safe_dots["ok"], safe_dots.get("error")

    # 危险 remote_work_dir 同样拒绝
    for wd in ("/", "~/work/.."):
        r = call_tool("generate_remote_run_script", {
            "host": "h", "tag": "t", "local_script_name": "run.sh", "remote_work_dir": wd})
        assert r["ok"] is False, f"dangerous remote_work_dir accepted: {wd!r}"


def test_run_sim_backend_dispatch() -> None:
    """run_sim 必须按后端校验工具链，并拒绝「入口形态与后端不符」。"""
    print("coverage: run_sim backend dispatch")
    with tempfile.TemporaryDirectory() as td:
        mk = Path(td) / "Makefile"
        mk.write_text("SIM ?= verilator\nall:\n\t@echo hi\n", encoding="utf-8")
        sh = Path(td) / "run.sh"
        sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sh.chmod(0o755)

        # 非法 backend
        r = call_tool("run_sim", {"sim_script": str(sh), "backend": "questa"})
        assert r["ok"] is False, r

        # Makefile 交给 verilator/iverilog 后端：必须拒绝，并把用户指向 cocotb
        for be in ("verilator", "iverilog"):
            r = call_tool("run_sim", {"sim_script": str(mk), "backend": be})
            assert r["ok"] is False, (be, r)
            err = (r.get("error") or "").lower()
            assert "cocotb" in err, (be, r.get("error"))

        # shell 脚本交给 cocotb 后端：必须拒绝（make 会报 "No targets"）
        r = call_tool("run_sim", {"sim_script": str(sh), "backend": "cocotb"})
        assert r["ok"] is False, r
        assert "makefile" in (r.get("error") or "").lower(), r.get("error")

        # auto 检测：Makefile -> cocotb
        r = call_tool("run_sim", {"sim_script": str(mk), "backend": "auto"})
        # 环境里没装 cocotb 时应给 E020 说明缺什么；装了则应尝试运行。
        if not r["ok"]:
            codes = {d.get("code") for d in (r.get("diagnostics") or [])}
            assert "E020" in codes or "cocotb" in (r.get("error") or "").lower(), r


def test_run_sim_iverilog_backend_real_run() -> None:
    """iverilog 后端真实闭环（装了 iverilog 才跑）。"""
    print("coverage: run_sim iverilog backend real run")
    if not (shutil.which("iverilog") and shutil.which("vvp")):
        print("  (skipped: iverilog not installed)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "tb.v").write_text(
            "`timescale 1ns/1ps\n"
            "module tb;\n"
            "  logic clk = 0;\n"
            "  int errs = 0;\n"
            "  always #5 clk = ~clk;\n"
            "  initial begin\n"
            "    repeat (3) @(posedge clk);\n"
            "    if (errs == 0) begin $display(\"PASS: iverilog smoke ok\"); $finish; end\n"
            "  end\n"
            "endmodule\n", encoding="utf-8")
        (root / "run_iv.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            'iverilog -g2012 -o "$HERE/tb.vvp" "$HERE/tb.v"\n'
            'vvp "$HERE/tb.vvp"\n', encoding="utf-8")
        (root / "run_iv.sh").chmod(0o755)
        r = call_tool("run_sim", {
            "sim_script": str(root / "run_iv.sh"),
            "backend": "iverilog",
            "log_dir": str(root / "logs"),
            "timeout_sec": 120,
        })
        assert r["ok"], (r.get("error"), r.get("diagnostics"))
        tail = (r["data"].get("stdout_tail") or "")
        assert "PASS: iverilog smoke ok" in tail, tail[-400:]
        # 全量日志必须落盘（否则事后无法回溯）
        assert r["data"].get("log_files"), r["data"].keys()


def main() -> int:
    test_eda_tools_are_exposed_to_agents()
    test_parse_uvm_log_fixtures()
    test_parse_dc_pt_qor_trap_detection()
    test_aggregate_seed_medians_tool()
    test_generate_remote_run_script_tool()
    test_run_sim_backend_dispatch()
    test_run_sim_iverilog_backend_real_run()
    print("\nALL EDA TOOL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
