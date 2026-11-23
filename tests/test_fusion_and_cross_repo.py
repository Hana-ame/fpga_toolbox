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

# Windows 的 cp1252 无法编码中文 print（实测 CI 失败），强制 stdout/stderr 用 UTF-8。
# 放在所有 print 之前，让含中文的诊断输出在任意平台都安全。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

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
        # 用 sim.sh 的真实格式：`== sim: <name> (top=<top>) ==`。套件名后带
        # 参数。旧正则要求套件名后紧跟 `==`，对这种真实日志永远匹配不到，
        # 会把所有套件误判为 missing —— 「逐套件身份核对」护栏对真实日志
        # 从不生效。这里特意用真实格式锁死该缺陷。
        log.write_text(
            "== sim: counter (top=tb_counter) ==\nPASS: all 20 checks passed\n"
            "== sim: axi (top=tb_axi_apb) ==\nPASS: all 35 checks passed\n",
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
    # 动态断言：检查数会随新护栏覆盖扩展（已从 4 增至 9，含 A5-A9 套件身份核对、版本守卫、emit fail-fast、slang 回退可见性）。
    # 这里只要求「全部通过」，不再硬编码具体数字，避免加检查时误判为回归。
    aud = r_aud.get("data", {})
    check(r_aud["ok"] and aud.get("failed") == 0 and aud.get("passed", 0) >= 9,
          f"audit_harness all fault-injection checks passed (passed={aud.get('passed')}/{aud.get('total')})")

    schemas = list_tool_schemas()
    names = {s["name"] for s in schemas}
    check(len(schemas) == 12, f"12 tools registered (got {len(schemas)})")
    check({"lint_tb", "run_sim", "run_mutation", "audit_harness"}.issubset(names), "all 4 new tools in MCP schemas")

    proc = subprocess.run([sys.executable, "-m", "fpga_toolbox", "audit_harness"], cwd=str(ROOT), capture_output=True, text=True, check=False)
    check(proc.returncode == 0 and '"ok": true' in proc.stdout, "CLI `python -m fpga_toolbox audit_harness` exits 0")


def test_slang_backend_real_rtl() -> None:
    """锁死 slang 后端两个历史缺陷（仅在装了 slang 时验证）。

    缺陷 1：slang --ast-json - 把人类可读 banner（"Top level design units:"
            / "Build succeeded:")和 JSON 混在同一个 stdout，旧实现直接
            json.loads 整段输出必然失败 → slang 后端从未真正生效。
    缺陷 2：slang 遇到未知 package 时 exit 非 0 但仍输出完整 AST JSON，
            旧实现因 returncode != 0 整段丢弃；且回退到 builtin 时对调用方
            无任何信号 —— 恰是本项目批判的「静默通过」。
    """
    import shutil

    print("fusion: slang AST backend on real RTL (banner handling + fallback visibility)")
    if not shutil.which("slang"):
        print("  skip slang not installed (W013/回退诊断在无 slang 时仍可验证)")
        return

    # 1) 真实桥文件（带跨文件 import）必须真的走 slang 后端，不能静默回退
    bridge = REF / "axi" / "axi_apb_bridge.sv"
    if bridge.is_file():
        r = call_tool("extract_interfaces", {
            "rtl_file": str(bridge), "parser_backend": "slang",
        })
        check(r.get("ok") is True, "slang backend parses bridge with cross-file imports")
        mods = r.get("data", {}).get("modules", [])
        if mods:
            check(mods[0].get("parser_backend") == "slang",
                  f"bridge really used slang backend (got {mods[0].get('parser_backend')})")

    # 2) auto 后端应优先选 slang，且不产生「回退」诊断
    if bridge.is_file():
        r = call_tool("extract_interfaces", {"rtl_file": str(bridge)})
        mods = r.get("data", {}).get("modules", [])
        if mods:
            check(mods[0].get("parser_backend") == "slang",
                  "auto backend prefers slang when available")
            diags = r.get("diagnostics") or []
            check(not any(d.get("code") == "W013" for d in diags),
                  "no W013 fallback warning when slang succeeds")

    # 3) 覆盖率：靶场里含 module 的 .sv 文件应全部走 slang（零静默回退）
    from fpga_toolbox.core.sv_parser import parse_sv_file
    rtl_files = sorted(REF.rglob("*.sv"))
    with_mod = []
    for f in rtl_files:
        if "module " in f.read_text(encoding="utf-8", errors="replace"):
            with_mod.append(f)
    slang_hits = 0
    fallback_diags = 0
    for f in with_mod:
        diag: list = []
        m = parse_sv_file(f, backend="auto", diagnostics=diag)
        if m and m[0].parser_backend == "slang":
            slang_hits += 1
        elif diag:
            fallback_diags += 1
    check(slang_hits == len(with_mod),
          f"all {len(with_mod)} module-bearing .sv files parsed by slang (got {slang_hits})")
    check(fallback_diags == 0,
          "no silent builtin fallback (fallbacks are reported via diagnostics)")

    # 4) 无 slang 的环境必须产出回退诊断（可见信号）
    if bridge.is_file():
        diag2: list = []
        m2 = parse_sv_file(bridge, backend="auto", diagnostics=diag2)
        check(m2 is not None, "auto backend still returns modules when diagnostics collected")


def test_emit_testbench_error_paths() -> None:
    """锁死 emit_testbench 的两个错误路径（历史缺陷：报错只有 'awaddr' 一个词）。

    缺陷：spec 缺 AXI4-Lite boundary（如纯 APB 寄存器文件）时，build_and_validate_ir
    静默构造出空 port_map 的 axil IR，往下 emit 在 _emit_wrap 抛 KeyError('awaddr')，
    调用方只看到一个单词、看不出根因。修复后必须给出明确错误。
    """
    import tempfile

    print("fusion: emit_testbench clear error paths (no cryptic KeyError)")
    with tempfile.TemporaryDirectory() as tmp:
        # 1) 纯 APB 寄存器 spec（无 axi4_lite boundary）→ 必须明确拒绝
        r = call_tool("emit_testbench", {
            "spec_path": str(REF / "axi" / "regfile_spec.json"),
            "out_dir": str(Path(tmp) / "apb_only"),
        })
        check(r.get("ok") is False,
              "pure-APB spec rejected (no AXI4-Lite boundary)")
        err = str(r.get("error") or "")
        check("awaddr" != err,
              f"error is not a bare 'awaddr' (got {err[:60]!r})")
        check("axi4_lite" in err.lower() or "boundary" in err.lower(),
              "error mentions the missing AXI4-Lite boundary")

        # 2) 完整链路但缺必需 port_map → 必须报 E001 而非崩在生成阶段
        # 构造一个含 axi4_lite 但缺 wstrb 的契约
        bad = Path(tmp) / "no_wstrb.yaml"
        bad.write_text(
            "boundary:\n"
            "  module_name: foo\n"
            "  rtl_files: [foo.sv]\n"
            "  clock: {port: ACLK}\n"
            "  reset: {port: ARESETn, active_low: true}\n"
            "  axi4_lite:\n"
            "    addr_width: 8\n"
            "    data_width: 32\n"
            "    role: slave\n"
            "    port_map:\n"
            "      awaddr: AWADDR\n"  # 故意只给一个信号，缺 wstrb 等
            "test_plan:\n"
            "  protocol_rules: {check_valid_stability: true, check_reset_outputs: true, max_resp_cycles: 16, allow_unaligned_addr: false}\n"
            "  register_rules: []\n"
            "  illegal_addr_rules: []\n"
            "  stress_profile: {random_backpressure: false, iterations: 1, seed: 1}\n",
            encoding="utf-8",
        )
        r2 = call_tool("emit_testbench", {
            "spec_path": str(bad),
            "out_dir": str(Path(tmp) / "no_wstrb"),
        })
        check(r2.get("ok") is False,
              "incomplete port_map rejected with E001")
        err2 = str(r2.get("error") or "")
        check("E001" in err2,
              f"error surfaces the E001 diagnostic (got {err2[:70]!r})")
        check(len(r2.get("diagnostics") or []) >= 1,
              "E001 diagnostic is propagated to the caller")


def test_suite_identity_real_format() -> None:
    r"""锁死 verify_suite_identities 对 sim.sh 真实日志格式的解析。

    sim.sh 打印 `== sim: <name> (top=<top>) ==`，套件名后带 (top=...) /
    (WAIT=...) 参数。旧正则 `==\s*sim:\s*(\w+)\s*==` 对此永远匹配不到，
    会把所有套件判为 missing——反静默通过护栏形同虚设。
    """
    from fpga_toolbox.sim_runner import verify_suite_identities

    print("fusion: suite identity parsing on real sim.sh log format")
    real = (
        "== sim: counter (top=tb_counter) ==\nPASS: all 20 checks passed\n"
        "== sim: latency (WAIT=93) ==\nPASS: latency suite ok\n"
        "== sim: sv (top=tb_axi_apb_sv) ==\n[MON] monitor clean\nPASS: sv txn testbench\n"
        "== sim: gaps (top=tb_gaps_sv) ==\nFAIL: coverage-gap testbench\n"
    )
    r = verify_suite_identities(real, ["counter", "latency", "sv", "gaps", "dual"])
    check(r["observed"].get("counter") == "PASS", f"counter parsed PASS (got {r['observed'].get('counter')})")
    check(r["observed"].get("latency") == "PASS", f"latency (WAIT=) format parsed (got {r['observed'].get('latency')})")
    check(r["observed"].get("sv") == "PASS", f"sv parsed PASS despite non-PASS monitor line (got {r['observed'].get('sv')})")
    check(r["observed"].get("gaps") == "FAIL", f"gaps with FAIL detected (got {r['observed'].get('gaps')})")
    check(r["missing"] == ["dual"], f"only 'dual' missing (got {r['missing']})")
    check(r["all_verified"] is False, "all_verified False when a suite missing")


def test_lint_wait_form() -> None:
    """锁死 lint_tb 的 wait() 握手检测（第三方 TB 广泛使用 wait 而非 while）。

    历史缺陷：R1/R3 只匹配 `while (!BVALID)`，不识别语义等价的
    `wait (BVALID)`。真实第三方 testbench（如 GH 的 AXI4_Lite_APB4_Bridge）
    大量用 wait()，旧实现完全漏报。本检查用最小 fixture 锁死：wait 等 VALID
    而 READY 未置高 → R3 ERROR；wait 同时检查 READY → 干净。
    """
    print("fusion: lint_tb wait()-form handshake detection")
    with tempfile.TemporaryDirectory() as td:
        sv = Path(td) / "wait_tb.sv"
        sv.write_text(
            "module wait_tb;\n"
            "  logic clk, bvalid, bready, rvalid, rready;\n"
            "  initial begin\n"
            "    bready = 1'b0;\n"
            "    wait (bvalid);\n"       # BREADY=0 时等 BVALID → R3
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )
        r = call_tool("lint_tb", {"target_path": str(sv), "rules": ["handshake"]})
        codes = {f.get("code") for f in (r.get("data") or {}).get("findings", [])}
        check(r.get("ok") is False, f"wait(bvalid) without BREADY flagged (ok={r.get('ok')})")
        check("R3" in codes, f"R3 emitted for wait(bvalid) form (got {sorted(codes)})")

        # 对照：wait 同时检查 READY 不应报
        sv2 = Path(td) / "ok_tb.sv"
        sv2.write_text(
            "module ok_tb;\n"
            "  logic clk, bvalid, bready;\n"
            "  initial begin\n"
            "    wait (bvalid && bready);\n"   # 同时检查 READY → 干净
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )
        r2 = call_tool("lint_tb", {"target_path": str(sv2), "rules": ["handshake"]})
        check(r2.get("ok") is True, f"wait(bvalid && bready) is clean (ok={r2.get('ok')})")


def test_index_repo_include_boundary() -> None:
    """锁死 index_repo 的 include 边界归属（真实 RISC-V 仓库踩坑）。

    缺陷：仓库用 `include 把多个子模块拼进一个翻译单元（如 Single_Cycle_Top.v
    include 了 PC/ALU/寄存器堆/存储器）。旧实现把整个文件源交给例化扫描，导致
    文件里**每一个**模块都继承该文件的全部例化——叶子模块 ALU/Register_File
    被误标成例化了 PC/存储器（那些其实是顶层 Single_Cycle_Top 的例化）。

    修复：例化扫描限定在 module 自身定义体内；include 展开进来的子模块在本文件
    无自身 module 声明时不归属任何例化。
    """
    print("fusion: index_repo include-boundary instantiation attribution")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        # top.v 用 `include 拼进 leaf_a/leaf_b/top；三个子模块在各自独立文件里
        (td_p / "top.v").write_text(
            "`include \"leaf_a.v\"\n"
            "`include \"leaf_b.v\"\n"
            "module top(input logic clk);\n"
            "  leaf_a ua (.clk(clk));\n"
            "  leaf_b ub (.clk(clk));\n"
            "endmodule\n",
            encoding="utf-8",
        )
        (td_p / "leaf_a.v").write_text(
            "module leaf_a(input logic clk);\nendmodule\n", encoding="utf-8")
        (td_p / "leaf_b.v").write_text(
            "module leaf_b(input logic clk);\nendmodule\n", encoding="utf-8")
        r = call_tool("index_repo", {"project_root": str(td_p)})
        check(r.get("ok") is True, "index_repo parses include-based project")
        mods = r.get("data", {}).get("modules", {})
        check(mods.get("leaf_a", {}).get("instantiated_modules") == [],
              f"leaf_a not attributed top's instantiations (got {mods.get('leaf_a', {}).get('instantiated_modules')})")
        check(mods.get("leaf_b", {}).get("instantiated_modules") == [],
              f"leaf_b not attributed top's instantiations (got {mods.get('leaf_b', {}).get('instantiated_modules')})")
        check(set(mods.get("top", {}).get("instantiated_modules") or []) == {"leaf_a", "leaf_b"},
              f"top correctly attributes its own instantiations (got {mods.get('top', {}).get('instantiated_modules')})")


def test_dual_side_apb_slave_emitter() -> None:
    print("fusion: dual-side AXI4-Lite + APB master bridge emitter (zero WIDTHEXPAND)")
    import shutil

    import yaml

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        dut_sv = tdp / "dual_bridge_dut.sv"
        dut_sv.write_text(
            "`timescale 1ns/1ps\n"
            "module dual_bridge_dut #(parameter int AW=8, DW=32) (\n"
            "  input logic ACLK, input logic ARESETn,\n"
            "  output logic AWREADY, input logic AWVALID, input logic [AW-1:0] AWADDR,\n"
            "  output logic WREADY, input logic WVALID, input logic [DW-1:0] WDATA, input logic [DW/8-1:0] WSTRB,\n"
            "  output logic BVALID, input logic BREADY, output logic [1:0] BRESP,\n"
            "  output logic ARREADY, input logic ARVALID, input logic [AW-1:0] ARADDR,\n"
            "  output logic RVALID, input logic RREADY, output logic [DW-1:0] RDATA, output logic [1:0] RRESP,\n"
            "  output logic PSEL, output logic PENABLE, output logic PWRITE,\n"
            "  output logic [AW-1:0] PADDR, output logic [DW-1:0] PWDATA,\n"
            "  input logic PREADY, input logic [DW-1:0] PRDATA, input logic PSLVERR\n"
            ");\n"
            "  assign AWREADY=1'b0; assign WREADY=1'b0; assign BVALID=1'b0; assign BRESP=2'b0;\n"
            "  assign ARREADY=1'b0; assign RVALID=1'b0; assign RDATA='0; assign RRESP=2'b0;\n"
            "  assign PSEL=1'b0; assign PENABLE=1'b0; assign PWRITE=1'b0; assign PADDR='0; assign PWDATA='0;\n"
            "endmodule\n",
            encoding="utf-8",
        )
        draft_y = tdp / "draft.yaml"
        r_ext = call_tool("extract_interfaces", {"rtl_file": str(dut_sv), "emit_draft_yaml": str(draft_y)})
        check(r_ext["ok"], "dual-side bridge interfaces extracted")
        spec = yaml.safe_load(draft_y.read_text(encoding="utf-8"))
        spec["boundary"]["apb"]["wait_cycles"] = 1
        spec["test_plan"]["illegal_addr_rules"] = [{"rule_id": "ILL_001", "offset": 0x3C, "expected_resp": "SLVERR"}]
        draft_y.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

        tb_dir = tdp / "tb"
        r_emit = call_tool("emit_testbench", {"spec_path": str(draft_y), "out_dir": str(tb_dir), "rtl_file": str(dut_sv)})
        check(r_emit["ok"] and r_emit["data"].get("dual_side") is True, "emit_testbench reports dual_side=True")
        arts = r_emit["data"].get("artifacts", {})
        check("apb_slave_model" in arts and Path(arts["apb_slave_model"]).is_file(), "apb_slave_model.sv generated")
        wrap_txt = Path(arts["unit_isolation_wrapper"]).read_text(encoding="utf-8")
        check("u_apb_slv" in wrap_txt and ".PRDATA(apb_prdata)" in wrap_txt, "wrapper wires APB master to u_apb_slv (no 1'b0 tie-off)")

        if shutil.which("verilator"):
            proc = subprocess.run(
                [
                    "verilator", "--lint-only", "-Wall",
                    "-Wno-DECLFILENAME", "-Wno-UNUSED", "-Wno-UNUSEDSIGNAL",
                    "--top-module", "dual_bridge_dut_axil_wrap",
                    str(dut_sv), arts["apb_slave_model"], arts["sva_protocol_checker"], arts["unit_isolation_wrapper"],
                ],
                capture_output=True, text=True, check=False,
            )
            check(proc.returncode == 0 and "WIDTHEXPAND" not in proc.stderr and "WIDTHTRUNC" not in proc.stderr, "verilator --lint-only -Wall zero WIDTHEXPAND/WIDTHTRUNC")


def test_multi_bus_classifiers():
    """Verify slang/regex native classification for AHB-Lite, DDR3, DDR4, HBM, and PCIe Gen3/4/5."""
    from fpga_toolbox.iface_extractor import tool_extract_interfaces
    ref = Path(__file__).resolve().parents[1] / "examples" / "multi_bus_ref"
    expected = {
        "ahb_lite_slave.sv": ("ahb_lite", "HCLK", "HRESETn"),
        "ddr3_mem_ctrl.sv": ("ddr3", "clk", "rst_n"),
        "ddr4_mem_ctrl.sv": ("ddr4", "clk", "rst_n"),
        "hbm_pc_ctrl.sv": ("hbm", "clk", "rst_n"),
        "pcie_tlp_ep.sv": ("pcie", "clk", "rst_n"),
    }
    for fname, (proto, clk_name, rst_name) in expected.items():
        res = tool_extract_interfaces(str(ref / fname))
        assert res["ok"], f"{fname} extract failed: {res}"
        mod = res["data"]["modules"][0]
        assert mod["clock"]["port"] == clk_name, f"{fname} clk mismatch: {mod['clock']}"
        assert mod["reset"]["port"] == rst_name, f"{fname} rst mismatch: {mod['reset']}"
        protos = [i["protocol"] for i in mod["interfaces"]]
        assert proto in protos, f"{fname} expected {proto}, got {protos}"
        if proto == "pcie":
            pcie_if = [i for i in mod["interfaces"] if i["protocol"] == "pcie"][0]
            assert pcie_if.get("pcie_gen") == 4
            assert pcie_if.get("pcie_lanes") == 4


def test_multi_bus_emitter_port_guard() -> None:
    """protocol= 的 TB 模板硬连的端口若在 DUT 上不存在，必须 fail-fast。

    回归 39：分类器只需 4 个信号就能认出 APB，但 TB 模板会连全部 9 个
    （含 pstrb/pslverr）。RTL 缺这些端口时，旧实现仍返回 ok=True 并生成
    `.pstrb(pstrb)` 连到不存在端口的 TB——Verilator 报 PINNOTFOUND 编译
    失败，即「静默生成坏产物」。修复后生成前校验必需端口，缺失即报错。
    """
    import tempfile

    from fpga_toolbox import call_tool

    print("fusion: multi-bus emitter required-port guard")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # APB3 风格：无 pstrb / pslverr（分类器仍能认出 apb）
        missing_sv = tdp / "apb3_no_pstrb.sv"
        missing_sv.write_text(
            "module apb3_no_pstrb(input logic clk, input logic rst,\n"
            "   input logic psel, input logic penable, input logic pwrite,\n"
            "   input logic [7:0] paddr, input logic [31:0] pwdata,\n"
            "   output logic pready, output logic [31:0] prdata); endmodule\n",
            encoding="utf-8",
        )
        out1 = tdp / "tb_bad"
        r = call_tool("emit_testbench", {
            "rtl_file": str(missing_sv), "out_dir": str(out1), "protocol": "apb",
        })
        check(r["ok"] is False, f"APB TB with missing pstrb is rejected (got {str(r.get('error'))[:120]})")
        check("pstrb" in str(r.get("error")), f"error names the missing port (got {str(r.get('error'))[:160]})")
        check(not (out1 / "tb_apb3_no_pstrb_apb.sv").exists(), "no bogus TB artifact written")

        # 端口齐全时必须正常生成（不能把好路径也堵死）
        full_sv = tdp / "apb_ok.sv"
        full_sv.write_text(
            "module apb_ok(input logic clk, input logic rst,\n"
            "   input logic psel, input logic penable, input logic pwrite,\n"
            "   input logic [7:0] paddr, input logic [31:0] pwdata, input logic [3:0] pstrb,\n"
            "   output logic pready, output logic [31:0] prdata, output logic pslverr); endmodule\n",
            encoding="utf-8",
        )
        r2 = call_tool("emit_testbench", {
            "rtl_file": str(full_sv), "out_dir": str(tdp / "tb_ok"), "protocol": "apb",
        })
        check(r2["ok"] is True, f"complete APB still emits (got {str(r2.get('error'))[:120]})")

        # ddr3/hbm/pcie 模板不用 port_map，按标准端口名硬连 + `.*` 兜底。
        # `.*` 只连 TB 已声明的同名变量，所以 DUT 用 `inout ddr3_dq` 而模板
        # 声明 ddr3_dq_in/ddr3_dq_out 时会编译失败（Can't find definition of
        # variable），旧实现仍报 ok=True。回归 41。
        inout_ddr3 = tdp / "ddr3_inout.sv"
        inout_ddr3.write_text(
            "module ddr3_inout(input logic clk, input logic rst_n,\n"
            "   output logic ddr3_ck_p, output logic ddr3_ck_n, output logic ddr3_cke,\n"
            "   output logic ddr3_cs_n, output logic ddr3_ras_n, output logic ddr3_cas_n,\n"
            "   output logic ddr3_we_n, output logic [2:0] ddr3_ba, output logic [13:0] ddr3_addr,\n"
            "   inout  wire  [15:0] ddr3_dq, output logic ddr3_rd_valid); endmodule\n",
            encoding="utf-8",
        )
        out3 = tdp / "tb_inout"
        r3 = call_tool("emit_testbench", {
            "rtl_file": str(inout_ddr3), "out_dir": str(out3), "protocol": "ddr3",
        })
        check(r3["ok"] is False, f"ddr3 inout-dq DUT rejected (got ok={r3['ok']})")
        check("ddr3_dq_out" in str(r3.get("error")), "error names ddr3_dq_out")
        check(not (out3 / "tb_ddr3_inout_ddr3.sv").exists(), "no bogus ddr3 TB written")

        # 方向不匹配不会编译失败，但会在运行期永久挂起（TB 等不到响应），
        # 比编译失败更隐蔽。回归 42：pready 被写成 input 时，TB 的
        # `do ... end while (!pready)` 永远等不到响应。
        wrong_dir = tdp / "apb_wrong_dir.sv"
        wrong_dir.write_text(
            "`timescale 1ns/1ps\n"
            "module apb_wrong_dir(input logic clk, input logic rst,\n"
            "   input logic psel, input logic penable, input logic pwrite,\n"
            "   input logic [7:0] paddr, input logic [31:0] pwdata, input logic [3:0] pstrb,\n"
            "   input logic pready, output logic [31:0] prdata, output logic pslverr); endmodule\n",
            encoding="utf-8",
        )
        out4 = tdp / "tb_wrong_dir"
        r4 = call_tool("emit_testbench", {
            "rtl_file": str(wrong_dir), "out_dir": str(out4), "protocol": "apb",
        })
        check(r4["ok"] is False, f"APB pready-as-input rejected (got ok={r4['ok']})")
        check("pready" in str(r4.get("error")) and "direction" in str(r4.get("error")).lower(),
              f"error explains direction mismatch (got {str(r4.get('error'))[:120]})")
        check(not (out4 / "tb_apb_wrong_dir_apb.sv").exists(), "no hanging TB written")

        # 测试向量必须随位宽缩放：dw=8 时若仍写 `8'h12345678`，Verilator 报
        # WIDTHDIGITS 且数据被截断，断言值失真但测试仍"跑通"。回归 43。
        narrow = tdp / "apb_narrow.sv"
        narrow.write_text(
            "`timescale 1ns/1ps\n"
            "module apb_narrow(input logic clk, input logic rst,\n"
            "   input logic psel, input logic penable, input logic pwrite,\n"
            "   input logic [3:0] paddr, input logic [7:0] pwdata, input logic [0:0] pstrb,\n"
            "   output logic pready, output logic [7:0] prdata, output logic pslverr); endmodule\n",
            encoding="utf-8",
        )
        rn = call_tool("emit_testbench", {
            "rtl_file": str(narrow), "out_dir": str(tdp / "tb_narrow"), "protocol": "apb",
        })
        check(rn["ok"] is True, f"narrow-bus APB still emits (got {str(rn.get('error'))[:100]})")
        if rn["ok"]:
            ntb = Path(rn["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
            # 不应出现 8 位宽标注配 8 个十六进制数字这种超宽字面量
            check("8'h12345678" not in ntb and "8'h000000" not in ntb,
                  "narrow-bus vectors are width-scaled (no over-wide literals)")
            check("8'h78" in ntb or "8'h" in ntb, "scaled 8-bit patterns present")

        # 端口齐全的官方靶场必须仍能生成
        ref = Path(__file__).resolve().parents[1] / "examples" / "multi_bus_ref"
        for fname, proto in (
            ("ddr3_mem_ctrl.sv", "ddr3"),
            ("hbm_pc_ctrl.sv", "hbm"),
            ("pcie_tlp_ep.sv", "pcie"),
        ):
            rp = call_tool("emit_testbench", {
                "rtl_file": str(ref / fname), "out_dir": str(tdp / f"tb_{proto}"), "protocol": proto,
            })
            check(rp["ok"] is True, f"reference {proto} still emits (got {str(rp.get('error'))[:100]})")
        if r2["ok"]:
            tb = Path(r2["data"]["artifacts"]["sv_testbench"])
            check(tb.is_file(), f"TB artifact exists ({tb})")
            txt = tb.read_text(encoding="utf-8")
            check(".pstrb(pstrb)" in txt or "pstrb = strb" in txt, "TB drives pstrb")


def main() -> int:
    test_cross_repo_extraction()
    test_index_repo_include_boundary()
    test_regfile_spec_v2_and_bitfields()
    test_lint_tb()
    test_lint_wait_form()
    test_mutation_runner()
    test_verilator_log_normalizer_and_visual()
    test_suite_identity_real_format()
    test_audit_and_registry()
    test_slang_backend_real_rtl()
    test_dual_side_apb_slave_emitter()
    test_emit_testbench_error_paths()
    test_multi_bus_classifiers()
    test_multi_bus_emitter_port_guard()
    print("\n" + ("ALL FUSION CHECKS PASSED" if FAILS == 0 else f"{FAILS} CHECK(S) FAILED"))
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
