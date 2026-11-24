"""Tool 12: Self-audit tool verifying that verification guardrails fail loudly (no silent passes).

Ported in spirit from agent/verilog/audit.sh: injects synthetic defects into isolated
temp fixtures and confirms that lint_tb, run_mutation (residual guard), normalize_sim_result,
and copy_sandbox all reject invalid/broken inputs with ok=False.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result
from .eda_env import probe_eda_env
from .mutation_runner import tool_run_mutation
from .result_normalizer import tool_normalize_sim_result
from .sandbox_copier import tool_copy_sandbox
from .sim_runner import tool_run_sim, verify_suite_identities
from .tb_emitter import tool_emit_testbench
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

        # A5: verify_suite_identities 必须解析 sim.sh 真实格式日志，且漏跑被精确抓出。
        # 这是 run_sim / normalize 的守门逻辑。历史缺陷：正则要求套件名后紧跟 `==`，
        # 而真实日志是 `== sim: <name> (top=<top>) ==`，导致所有套件被误判 missing，
        # 反静默通过护栏形同虚设。此检查锁死：真实格式能解析、漏跑能识别。
        real_log = (
            "== sim: counter (top=tb_counter) ==\nPASS: all 20 checks passed\n"
            "== sim: sv (top=tb_axi_apb_sv) ==\nPASS: sv testbench\n"
        )
        a5 = verify_suite_identities(real_log, ["counter", "sv", "gaps"])
        a5_ok = (
            a5["observed"].get("counter") == "PASS"
            and a5["observed"].get("sv") == "PASS"
            and a5["missing"] == ["gaps"]
            and a5["all_verified"] is False
        )
        checks.append({
            "id": "A5_SUITE_IDENTITY_REAL_FORMAT",
            "passed": a5_ok,
            "detail": f"observed={a5['observed']} missing={a5['missing']}",
        })

        # A6: 全部预期套件齐且都 PASS 时必须 all_verified=True（不能假报警）
        a6 = verify_suite_identities(real_log, ["counter", "sv"])
        a6_ok = a6["all_verified"] is True and a6["missing"] == [] and a6["failed"] == []
        checks.append({
            "id": "A6_SUITE_IDENTITY_ALL_PASS",
            "passed": a6_ok,
            "detail": f"all_verified={a6['all_verified']} missing={a6['missing']}",
        })

        # A7: run_sim 必须在 Verilator 版本不达标时显式拒绝（E020），不能静默跑。
        # 伪造一个 meets_min_version=False 的 EDA 环境，确认 E020 守卫生效。
        import fpga_toolbox.sim_runner as _sr
        _orig_probe = _sr.probe_eda_env
        r_sim = None
        e020 = False

        def _fake_env():
            return {
                "verilator": {"path": "/nonexistent", "version": "5.020",
                              "meets_min_version": False, "message": "Verilator 5.020 < 5.040"},
                "slang": {"path": None, "available": False},
                "stdbuf": {"path": "/usr/bin/stdbuf", "available": True},
            }

        try:
            _sr.probe_eda_env = _fake_env
            _sim_dir = root / "simproj"
            _sim_dir.mkdir()
            _sim_script = _sim_dir / "sim.sh"
            _sim_script.write_text("#!/bin/bash\necho dummy\n", encoding="utf-8")
            r_sim = tool_run_sim(str(_sim_script), suite="counter", timeout_sec=10)
            e020 = any(d.get("code") == "E020" for d in (r_sim.get("diagnostics") or []))
            a7_ok = (not r_sim["ok"]) and e020
        finally:
            _sr.probe_eda_env = _orig_probe
        checks.append({
            "id": "A7_RUN_SIM_VERSION_GUARD",
            "passed": a7_ok,
            "detail": f"ok={r_sim['ok']} e020={e020}",
        })

        # A8: emit_testbench 在 spec 缺 AXI4-Lite boundary 时必须 fail-fast，
        # 而不是带着空 port_map 往下 emit 到 KeyError('awaddr')（历史缺陷）。
        apb_only = root / "apb_only.json"
        apb_only.write_text(
            '{"$schema":"apb-regfile-spec/v2","module":"m","bus":{"protocol":"APB",'
            '"addr_width":8,"data_width":32,"wait_cycles":2},"registers":[]}',
            encoding="utf-8",
        )
        r_emit = tool_emit_testbench(str(apb_only), str(root / "tb_out"))
        a8_ok = (not r_emit["ok"]) and ("awaddr" not in str(r_emit.get("error") or "")) \
            and ("axi4_lite" in str(r_emit.get("error") or "").lower()
                 or "boundary" in str(r_emit.get("error") or "").lower())
        checks.append({
            "id": "A8_EMIT_FAIL_FAST",
            "passed": a8_ok,
            "detail": f"ok={r_emit['ok']} error={str(r_emit.get('error') or '')[:60]}",
        })

        # A9: slang 不可用（或解析失败）时必须对调用方可见（W013 诊断），
        # 而不是静默退回 builtin——否则「可选 slang AST 后端」就是虚报。
        # 伪造 slang 不在 PATH，确认 builtin 回退会产出 W013。
        import fpga_toolbox.core.sv_parser as _sp
        _orig_which = _sp.shutil.which
        try:
            _sp.shutil.which = lambda name: None if name == "slang" else _orig_which(name)
            _rtl = root / "probe.sv"
            _rtl.write_text("module probe(input logic a, output logic y);\nassign y = a;\nendmodule\n",
                            encoding="utf-8")
            diags: list = []
            _sp.parse_sv_file(_rtl, backend="auto", diagnostics=diags)
            a9_ok = any("slang" in d for d in diags)
        finally:
            _sp.shutil.which = _orig_which
        checks.append({
            "id": "A9_SLANG_FALLBACK_VISIBLE",
            "passed": a9_ok,
            "detail": f"slang-missing diagnostics={len(diags)} visible={a9_ok}",
        })

        # A10: clean_pt_netlist 必须在「黑盒无法清理」时报错，而不是静默成功。
        # 背景：缺分号/截断语句的实例不会被正则匹配，旧实现仍返回 ok=True +
        # removed_instances=0，输出网表里黑盒原样保留，PrimeTime 仍会 link 失败。
        # 护栏的价值在于「产出物可断言」——所以这里要求它自检产物并显式报错。
        from .eda_campaign import tool_clean_pt_netlist

        _pt = root / "pt"
        _pt.mkdir()
        _bad_nl = _pt / "bad.v"
        _bad_nl.write_text(
            "module t(o);\n  output [7:0] o;\n  RDBUS u (.o_data(o))\nendmodule\n",
            encoding="utf-8",
        )
        _r_pt = tool_clean_pt_netlist(str(_bad_nl), str(_pt / "bad_out.v"))
        _residual_codes = {d.get("code") for d in (_r_pt.get("diagnostics") or [])}
        a10_ok = (not _r_pt["ok"]) and ("E051" in _residual_codes)
        checks.append({
            "id": "A10_PT_NETLIST_RESIDUAL_GUARD",
            "passed": a10_ok,
            "detail": f"ok={_r_pt['ok']} codes={sorted(_residual_codes)}",
        })

        # A11: lint_tb 必须能检出 SV 测试台的「只 $display 不判定」（S4）。
        # 背景：scan_silent_pass 原本只被喂 .sh/.py，SV 文件不进这个扫描器，
        # 于是 README 宣传的头号症状（打印几句 $display 就 $finish）返回 ok=True。
        # 同时要求它不误报带真实判定的 TB —— 一个只会说「坏」不会说「好」的
        # 检查器同样不能用。
        _s4 = root / "tb_s4.sv"
        _s4.write_text(
            "module tb_s4;\n"
            "  initial begin\n"
            "    #100;\n"
            "    $display(\"TEST DONE\");\n"
            "    $finish;\n"
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )
        _r_s4 = tool_lint_tb(str(_s4), rules=["silent_pass"])
        _s4_codes = {d.get("code") for d in (_r_s4.get("diagnostics") or [])}

        _good = root / "tb_good.sv"
        _good.write_text(
            "module tb_good;\n"
            "  initial begin\n"
            "    assert (d == 8'h12) else $error(\"bad\");\n"
            "    $display(\"PASS\");\n"
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )
        _r_good = tool_lint_tb(str(_good), rules=["silent_pass"])
        a11_ok = (not _r_s4["ok"]) and ("S4" in _s4_codes) and _r_good["ok"]
        checks.append({
            "id": "A11_SV_SILENT_PASS_DETECT",
            "passed": a11_ok,
            "detail": (f"silent ok={_r_s4['ok']} codes={sorted(_s4_codes)} "
                       f"judged_ok={_r_good['ok']}"),
        })

        # A12: 波形/日志抓取必须有产物断言，不能凭空报成功。
        # 背景：run_sim 原先完全不落盘完整日志、也没有波形支持；加了功能后
        # 最危险的失败模式是「说抓到了，其实没有」：
        #   * Verilator 缺 --trace 时静默忽略 $dumpvars，仿真 PASS 但无波形
        #   * 上一次运行遗留的旧 .vcd 被当成这次的证据
        # 因此这里要求：矛盾组合报 E024、旧波形不算数、以及完整日志真的落盘。
        import os as _os
        import stat as _stat

        _wl = root / "wave_log"
        _wl.mkdir(exist_ok=True)
        _fake = _wl / "run_plain.sh"
        _fake.write_text('#!/usr/bin/env bash\nset -euo pipefail\necho "PASS: fake"\n',
                         encoding="utf-8")
        _os.chmod(_fake, _os.stat(_fake).st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)

        # (a) require_waveform 但不启用 waveform → 必须失败
        _r_w1 = tool_run_sim(str(_fake), "apb", waveform=False, require_waveform=True)
        _w1_codes = {d.get("code") for d in (_r_w1.get("diagnostics") or [])}

        # (b) 磁盘上有旧波形，但本次没产生 → 不得算数
        (_wl / "wave_stale.vcd").write_text("$version stale $end\n", encoding="utf-8")
        _r_w2 = tool_run_sim(str(_fake), "apb", waveform=True, require_waveform=True)
        _w2_codes = {d.get("code") for d in (_r_w2.get("diagnostics") or [])}
        _w2_files = (_r_w2.get("data") or {}).get("waveform", {}).get("files", [])

        # (c) 正常路径：完整日志必须落盘且含首行
        _logd = _wl / "logs"
        _r_w3 = tool_run_sim(str(_fake), "apb", log_dir=str(_logd))
        _log_ok = bool(_r_w3.get("ok")) and bool((_r_w3.get("data") or {}).get("log"))
        _log_path = (_r_w3.get("data") or {}).get("log", {}).get("path") if _log_ok else None
        _log_full = bool(_log_path) and Path(_log_path).is_file()

        # 环境依赖：(b)/(c) 要真正跑一次脚本，而 run_sim 在跑之前会检查
        # Verilator >= 5.040（E020）。裸容器 / 精简 CI job 里没有 verilator 时，
        # 这两项无法验证 —— 不能让整个自审计因此失败（那会把「环境缺失」
        # 误报成「护栏失效」），也不能假装通过。策略：
        #   * (a) 矛盾组合的校验位于版本检查之前 → 永远可验证，必须通过；
        #   * (b)/(c) 在无 verilator 时跳过，并在 detail 里明示 skipped，
        #     strict 模式下仍要求 (a) 成立。
        _has_vlt = False
        try:
            _has_vlt = bool(probe_eda_env()["verilator"]["meets_min_version"])
        except Exception:  # noqa: BLE001
            _has_vlt = False

        _a12_a = (not _r_w1.get("ok")) and "E024" in _w1_codes
        if _has_vlt:
            _a12_rest = (
                (not _r_w2.get("ok")) and "E024" in _w2_codes and _w2_files == []
                and _log_ok and _log_full
            )
            _a12_detail = (f"contradictory ok={_r_w1.get('ok')} codes={sorted(_w1_codes)}; "
                           f"stale ok={_r_w2.get('ok')} files={len(_w2_files)} codes={sorted(_w2_codes)}; "
                           f"log_saved={_log_full}")
        else:
            _a12_rest = True
            _a12_detail = (f"contradictory ok={_r_w1.get('ok')} codes={sorted(_w1_codes)}; "
                           f"stale/log SKIPPED (no Verilator >= 5.040; got "
                           f"{sorted(_w2_codes)})")

        a12_ok = _a12_a and _a12_rest
        checks.append({
            "id": "A12_WAVEFORM_LOG_ARTIFACT_GUARD",
            "passed": a12_ok,
            "detail": _a12_detail,
        })

        # A13: 契约路径（--spec-path）生成的 wrapper 也必须有波形支持，且不能
        # 用编译期死宏（曾用 `ifdef WAVE_DUMP，但没有任何调用方 define 它 =
        # 静默不生效）。这条**不依赖 Verilator**：只做产物文本断言，所以即使
        # 在裸容器里也能持续监视。
        _cw = root / "contract"
        _cw.mkdir(exist_ok=True)
        (_cw / "dut.sv").write_text(
            "`timescale 1ns/1ps\n"
            "module reg_slave(input logic aclk, input logic aresetn,\n"
            "  input logic [11:0] s_axi_awaddr, input logic [2:0] s_axi_awprot,\n"
            "  input logic s_axi_awvalid, output logic s_axi_awready,\n"
            "  input logic [31:0] s_axi_wdata, input logic [3:0] s_axi_wstrb,\n"
            "  input logic s_axi_wvalid, output logic s_axi_wready,\n"
            "  output logic [1:0] s_axi_bresp, output logic s_axi_bvalid,\n"
            "  input logic s_axi_bready,\n"
            "  input logic [11:0] s_axi_araddr, input logic [2:0] s_axi_arprot,\n"
            "  input logic s_axi_arvalid, output logic s_axi_arready,\n"
            "  output logic [31:0] s_axi_rdata, output logic [1:0] s_axi_rresp,\n"
            "  output logic s_axi_rvalid, input logic s_axi_rready);\n"
            "  logic [31:0] rw_reg;\n"
            "  always_ff @(posedge aclk) begin\n"
            "    if (!aresetn) rw_reg <= '0;\n"
            "    else if (s_axi_awvalid && s_axi_wvalid) rw_reg <= s_axi_wdata;\n"
            "  end\n"
            "  assign s_axi_awready = 1'b1; assign s_axi_wready = 1'b1;\n"
            "  assign s_axi_bresp = 2'b00; assign s_axi_bvalid = s_axi_awvalid && s_axi_wvalid;\n"
            "  assign s_axi_arready = 1'b1; assign s_axi_rdata = rw_reg;\n"
            "  assign s_axi_rresp = 2'b00; assign s_axi_rvalid = s_axi_arvalid;\n"
            "endmodule\n",
            encoding="utf-8",
        )
        (_cw / "contract.yaml").write_text(
            "boundary:\n"
            "  module_name: reg_slave\n"
            "  rtl_files: [dut.sv]\n"
            "  parameters: {}\n"
            "  clock: {port: aclk, freq_mhz: 100.0}\n"
            "  reset: {port: aresetn, active_low: true, sync: true}\n"
            "  out_of_scope_ports: []\n"
            "  axi4_lite:\n"
            "    addr_width: 12\n"
            "    data_width: 32\n"
            "    role: slave\n"
            "    port_map:\n"
            "      awaddr: s_axi_awaddr\n      awprot: s_axi_awprot\n"
            "      awvalid: s_axi_awvalid\n      awready: s_axi_awready\n"
            "      wdata: s_axi_wdata\n      wstrb: s_axi_wstrb\n"
            "      wvalid: s_axi_wvalid\n      wready: s_axi_wready\n"
            "      bresp: s_axi_bresp\n      bvalid: s_axi_bvalid\n"
            "      bready: s_axi_bready\n      araddr: s_axi_araddr\n"
            "      arprot: s_axi_arprot\n      arvalid: s_axi_arvalid\n"
            "      arready: s_axi_arready\n      rdata: s_axi_rdata\n"
            "      rresp: s_axi_rresp\n      rvalid: s_axi_rvalid\n"
            "      rready: s_axi_rready\n"
            "test_plan:\n"
            "  protocol_rules:\n"
            "    check_valid_stability: true\n    check_reset_outputs: true\n"
            "    max_resp_cycles: 16\n    allow_unaligned_addr: false\n"
            "  register_rules:\n"
            "  - {rule_id: REG_001, name: CTRL_REG, offset: 0, reset_value: 0,\n"
            "     access: RW, write_mask: 0xffffffff, read_mask: 0xffffffff}\n"
            "  illegal_addr_rules: []\n"
            "  stress_profile: {random_backpressure: true, iterations: 8, seed: 1}\n",
            encoding="utf-8",
        )
        _r_ct = tool_emit_testbench(str(_cw / "contract.yaml"), str(_cw / "tb"),
                                    rtl_file=str(_cw / "dut.sv"))
        _a13_ok = False
        if _r_ct.get("ok"):
            _wsrc = Path(_r_ct["data"]["artifacts"]["unit_isolation_wrapper"]).read_text(encoding="utf-8")
            _a13_ok = (
                "$dumpfile(" in _wsrc
                and "$dumpvars" in _wsrc
                and '$test$plusargs("WAVE")' in _wsrc
                and "string wave_path;" in _wsrc
                and "`ifdef WAVE_DUMP" not in _wsrc  # 死宏 = 静默失效
            )
        # detail 用变量承载布尔值，避免在 f-string 表达式里写转义引号：
        # py3.10/3.11 不允许 f-string 表达式含反斜杠（缺陷 72 的检查器已锁死）。
        _a13_parts = []
        if _r_ct.get("ok"):
            _has_dump = "$dumpfile(" in _wsrc
            _has_plusarg = '$test$plusargs("WAVE")' in _wsrc
            _has_decl = "string wave_path;" in _wsrc
            _no_dead_ifdef = "`ifdef WAVE_DUMP" not in _wsrc
            _a13_ok = _has_dump and _has_plusarg and _has_decl and _no_dead_ifdef
            _a13_parts = [f"dumpfile={_has_dump}", f"plusarg={_has_plusarg}",
                          f"decl={_has_decl}", f"no_dead_ifdef={_no_dead_ifdef}"]
        checks.append({
            "id": "A13_CONTRACT_PATH_WAVEFORM_GUARD",
            "passed": _a13_ok,
            "detail": "emit ok={ok} ".format(ok=_r_ct.get("ok")) + " ".join(_a13_parts),
        })

        # A14: 所有生成的 shell 脚本必须 bash -n 语法通过 + set -u 下无未声明变量。
        # 背景（缺陷 78）：_emit_run_script 曾把 `local want_tag` 写进 if/else 的
        # else 分支，而脚本用 set -u —— 全新目录首次构建走 then 分支时变量未定义，
        # 之后 printf "$want_tag" 报 unbound variable、rc=1，【首次构建 100% 失败】；
        # 第二次运行 binary 已存在走 else 分支反而正常，把缺陷完全掩盖。
        # 这里对两个 shell 生成器的产物做：(1) bash -n；(2) bash -u -n（set -u 语法层）。
        import subprocess as _sub

        from .eda_campaign import generate_remote_run_script
        from .protocol_tb_emitter import _emit_run_script

        _a14_bad: list = []
        _a14_checked = 0
        for _p, _rtl, _tb in (("apb", "a.sv", "tb.sv"), ("pcie", "a.sv", "tb.sv")):
            _s = _emit_run_script(_p, "dut", Path("/x") / _rtl, Path("/x") / _tb)
            _a14_checked += 1
            for _label, _flags in (("bash -n", ["-n"]),
                                   ("bash -u -n", ["-u", "-n"])):
                _proc = _sub.run(["bash", *_flags, "-c", _s],
                                 capture_output=True, text=True)
                if _proc.returncode != 0:
                    _a14_bad.append(f"{_p}/{_label}: {_proc.stderr.strip()[:80]}")
            # 关键：bash -n / -u -n 只做【语法】检查，抓不到【运行期】unbound
            # variable（缺陷 78 正是这类）。这里真跑一遍脚本、并把所有外部命令
            # 换成 no-op 回显，配合 set -u，让"走了某分支却用了未声明变量"暴露
            # 成 unbound variable / command not found。脚本自身对未定义变量也
            # 会因 set -u 而中止。
            _stubbed = _s
            # 必须让脚本【真正走到 run_one 内部】，否则只执行到 build 失败就
            # set -e 退出，run_one 里的变量引用从未被执行 —— 检查形同虚设
            # （实测：第一版 runtime 检查 BIN echo 0 次，因为 obj/.wave_mode
            # 目录不存在导致 line 58 重定向失败早退）。所以把 verilator、rm、
            # mkdir 全 stub 成 no-op，并预建 obj 目录。
            _stubbed = re.sub(r'(?m)^VERILATOR=', r'VERILATOR=echo ', _stubbed)
            _stubbed = re.sub(r'(?m)^(\s*)"?\$VLT"? --binary.*$', r'\1echo VLT_STUB; mkdir -p "$out"', _stubbed)
            _stubbed = re.sub(r'(?m)^(\s*)rm -rf "\$out"$', r'\1mkdir -p "$out"', _stubbed)
            _stubbed = re.sub(r'(?m)^(\s*)"\$bin" \+', r'\1echo BIN +', _stubbed)
            _stubbed = re.sub(r'(?m)^(\s*)"\$bin"$', r'\1echo BIN', _stubbed)
            _tmpsh = root / f"a14_{_p}.sh"
            _tmpsh.write_text(_stubbed, encoding="utf-8")
            _proc = _sub.run(["bash", str(_tmpsh)], capture_output=True, text=True)
            _blob = _proc.stdout + _proc.stderr
            if "unbound variable" in _blob:
                _a14_bad.append(f"{_p}/runtime: {_blob.strip().splitlines()[-1][:80]}")
        # eda_campaign 的远程脚本（含 set -euo pipefail + scp/ssh）
        for _shm in (True, False):
            _s2 = generate_remote_run_script("host1", "tag1", "run.sh", use_dev_shm=_shm)
            _a14_checked += 1
            _proc = _sub.run(["bash", "-u", "-n", "-c", _s2],
                             capture_output=True, text=True)
            if _proc.returncode != 0:
                _a14_bad.append(f"eda/shm={_shm}: {_proc.stderr.strip()[:80]}")
        a14_ok = _a14_checked > 0 and not _a14_bad
        checks.append({
            "id": "A14_GENERATED_SHELL_SYNTAX",
            "passed": a14_ok,
            "detail": f"checked={_a14_checked} bad={len(_a14_bad)} "
                      f"{_a14_bad[:2]}",
        })

    failed = [c for c in checks if not c["passed"]]
    data = {"total": len(checks), "passed": len(checks) - len(failed), "failed": len(failed), "checks": checks}
    if failed and strict:
        # 自审计失败时仍带 data：调用方需要看到逐项 passed/failed（哪些护栏失效），
        # 而非只有一句汇总。与 lint_tb / run_mutation 同类修复。
        return err_result(
            "audit_harness",
            f"Self-audit failed on {len(failed)} check(s): {[c['id'] for c in failed]}",
            data=data,
            diagnostics=[{"severity": "ERROR", "code": "E040", "path": c["id"], "message": c["detail"]} for c in failed],
        )
    return ok_result("audit_harness", data)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("audit_harness"))
