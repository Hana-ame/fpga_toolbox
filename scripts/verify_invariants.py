#!/usr/bin/env python3
"""Six-Layer Invariant & Intent-Drift Enforcement Gate for fpga_toolbox.

Structurally blocks logical regressions and spec-to-code intent drift before commit, merge, or release:
  Layer 1 — Registry <-> Python Signature <-> Schema Bijection
  Layer 2 — Zero Silent False-Pass Adversarial Matrix (audit_harness + 5 live bug-guard probes)
  Layer 3 — Static Hygiene (ruff + compileall)
  Layer 4 — 100% Function Coverage Floor across fpga_toolbox/ (in --full mode)
  Layer 5 — Intent-Drift & Documentation Sync (registry tools in USER_MANUAL, build_docs_html --check, links)
  Layer 6 — Hardware RTL & TB Integrity (slang parse on all 18 DUTs + lint_tb 0 errors/warnings)

Usage:
  python3 scripts/verify_invariants.py --fast   # Pre-commit gate (~5s)
  python3 scripts/verify_invariants.py --full   # Full CI / release gate (includes 100% coverage trace)
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import inspect
import io
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import trace
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fpga_toolbox.core.sv_parser import parse_sv_file  # noqa: E402
from fpga_toolbox.eda_campaign import clean_netlist_for_pt, parse_dc_pt_qor, parse_uvm_log  # noqa: E402
from fpga_toolbox.iface_extractor import tool_extract_interfaces  # noqa: E402
from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench  # noqa: E402
from fpga_toolbox.registry import TOOL_REGISTRY  # noqa: E402
from fpga_toolbox.self_auditor import tool_audit_harness  # noqa: E402
from fpga_toolbox.tb_linter import tool_lint_tb  # noqa: E402


def _check_layer1_bijection() -> Tuple[bool, str]:
    """Every registered tool's schema properties MUST match its Python function signature 1-to-1."""
    errors: List[str] = []
    for name, entry in TOOL_REGISTRY.items():
        fn = entry["fn"]
        schema = entry["inputSchema"]
        props = set(schema.get("properties", {}).keys())
        req = set(schema.get("required", []))
        sig = inspect.signature(fn)
        fn_params = set(sig.parameters.keys())
        if props != fn_params:
            errors.append(f"{name}: schema props {sorted(props)} != fn params {sorted(fn_params)}")
        if not req.issubset(props):
            errors.append(f"{name}: required {sorted(req - props)} missing from properties")
    if errors:
        return False, "; ".join(errors)
    return True, f"{len(TOOL_REGISTRY)}/{len(TOOL_REGISTRY)} tools satisfy exact schema<->signature bijection"


def _check_layer2_adversarial_invariants() -> Tuple[bool, str]:
    """Run the 19-check fault-injection audit_harness + 5 mathematical anti-false-pass probes."""
    ah = tool_audit_harness(str(ROOT))
    if not ah["ok"]:
        return False, f"audit_harness failed: {ah.get('error')}"

    # Probe A: multi-group QoR with 1 violating group MUST NOT be timing_clean
    qor = parse_dc_pt_qor(
        "Timing Path Group 'a'\nCritical Path Slack: 0.00\nTotal Negative Slack: 0.00\nNo. of Violating Paths: 0\n"
        "Timing Path Group 'b'\nCritical Path Slack: -0.10\nTotal Negative Slack: -1.0\nNo. of Violating Paths: 2\n"
    )
    if qor["data"]["timing_clean"] is not False or qor["data"]["wns"] != -0.10:
        return False, "Probe A failed: multi-group QoR false-pass detected"

    # Probe B: truncated UVM log MUST fail with E050
    uvm = parse_uvm_log("Chronologic VCS...\n", expected_frames=1000)
    if uvm["ok"] or not any(d["code"] == "E050" for d in uvm["diagnostics"]):
        return False, "Probe B failed: truncated UVM log not rejected with E050"

    # Probe C: Verilog escaped identifier blackbox MUST be stripped
    cleaned, stats = clean_netlist_for_pt("module m(output q);\n  RDBUS \\u/0 (.o_d(q));\nendmodule\n")
    if stats["removed_instances"] != 1 or "assign q = 1'b0;" not in cleaned:
        return False, "Probe C failed: escaped Verilog blackbox not cleaned"

    # Probe D: non-ANSI dependent localparam MUST preserve exact width
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "na.sv"
        lp.write_text("module na(clk, d); parameter W=8; localparam H=W-1; input clk; input [H:0] d; endmodule\n", encoding="utf-8")
        mods = parse_sv_file(lp, backend="builtin")
        if {p.name: p.width for p in mods[0].ports}.get("d") != 8:
            return False, "Probe D failed: non-ANSI dependent localparam width collapsed"

        # Probe E: active-high reset MUST assert 1 then release 0 in emitted TB
        apb = Path(td) / "hi.sv"
        apb.write_text(
            "module hi(input logic pclk, input logic reset, input logic psel, input logic penable, "
            "input logic pwrite, input logic [15:0] paddr, input logic [31:0] pwdata, input logic [3:0] pstrb, "
            "output logic pready, output logic [31:0] prdata, output logic pslverr); endmodule\n",
            encoding="utf-8",
        )
        em = emit_protocol_testbench(str(apb), str(Path(td) / "out"), protocol="apb")
        tb_txt = Path(em["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        if ", reset = 1;" not in tb_txt or "    reset = 0;" not in tb_txt:
            return False, "Probe E failed: active-high reset polarity inverted"

    return True, f"audit_harness {ah['data']['passed']}/{ah['data']['total']} + 5/5 anti-false-pass probes passed"


def _check_layer3_static_hygiene() -> Tuple[bool, str]:
    # ruff 可选：未安装时降级为"compileall-only"，而不是抛 FileNotFoundError
    # 带完整堆栈崩溃（实测：裸环境无 ruff 时整个门禁崩溃，用户只看到
    # FileNotFoundError: 'ruff'，不知道该装 ruff 还是可跳过）。
    # compileall 仍独立验证全部语法，所以语法卫生不因 ruff 缺失而失去保障。
    try:
        r_ruff = subprocess.run(
            ["ruff", "check", "fpga_toolbox", "tests", "scripts"],
            cwd=ROOT, capture_output=True, text=True,
        )
    except FileNotFoundError:
        r_comp = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", "fpga_toolbox", "tests", "scripts"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if r_comp.returncode != 0:
            return False, f"compileall failed: {r_comp.stderr}"
        return True, (
            "ruff not installed — syntax verified via compileall only "
            "(install ruff for full lint: pip install ruff)"
        )
    if r_ruff.returncode != 0:
        return False, f"ruff failed:\n{r_ruff.stdout}\n{r_ruff.stderr}"
    r_comp = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "fpga_toolbox", "tests", "scripts"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r_comp.returncode != 0:
        return False, f"compileall failed: {r_comp.stderr}"
    return True, "ruff 0 errors + compileall clean"



def _iter_functions_with_tool_requirements(tree: ast.AST):
    """遍历**全部**函数（含嵌套/类方法），产出 (node, 所需工具元组)。

    实现要点：嵌套函数（如 slang 解析器里的 `_walk`）自身没有装饰器，但只有
    外层被调用时才可能执行，因此继承最近一层带标记祖先的工具需求。

    必须先建立 parent 映射再用 `ast.walk` 全量遍历 —— 早先手写递归只下探
    FunctionDef/AsyncFunctionDef，会漏掉类方法等节点（实测分母从 172 掉到
    143，把 29 个函数静默排除出覆盖率统计，等于放宽了门禁）。
    """
    parent: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def _tools_of(fn: ast.AST) -> tuple:
        for dec in getattr(fn, "decorator_list", []):
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                    and dec.func.id in ("_requires_tool", "requires_tool")):
                return tuple(
                    a.value for a in dec.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str))
        return ()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_requires_tool"):
            continue
        # 先看**自身**装饰器（`@_requires_tool("slang")` 直接标在被标记函数上），
        # 再看最近一个带工具需求的祖先函数（嵌套的 `_walk` 继承外层的需求）。
        needs: tuple = _tools_of(node)
        cur = parent.get(node)
        while not needs and cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inherited = _tools_of(cur)
                if inherited:
                    needs = inherited
                    break
            cur = parent.get(cur)
        yield node, needs


def _check_layer4_function_coverage() -> Tuple[bool, str]:
    pkg_root = (ROOT / "fpga_toolbox").resolve()
    py_files = sorted(pkg_root.rglob("*.py"))
    tracer = trace.Trace(count=True, trace=False)

    def _run_suites() -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for tf in sorted((ROOT / "tests").glob("test_*.py")):
                try:
                    runpy.run_path(str(tf), run_name="__main__")
                except SystemExit as e:
                    if e.code not in (0, None):
                        raise RuntimeError(f"{tf.name} exited with {e.code}") from e

    tracer.runfunc(_run_suites)
    hits_by_file = {}
    for (fname, lineno), cnt in tracer.results().counts.items():
        if cnt > 0:
            try:
                hits_by_file.setdefault(Path(fname).resolve(), set()).add(lineno)
            except OSError:
                continue

    total, covered, uncovered, skipped = 0, 0, [], []
    for pf in py_files:
        tree = ast.parse(pf.read_text(encoding="utf-8"))
        hits = hits_by_file.get(pf, set())
        # 读取「需要外部工具才能覆盖」的标记：函数体第一行是
        # `@_requires_tool("slang")` 装饰器。工具不可用时这些函数从分母剔除，
        # 否则零 EDA 环境（README 声明支持的口径）永久无法通过本层。
        for node, needs in _iter_functions_with_tool_requirements(tree):
            if needs and not all(shutil.which(t) for t in needs):
                skipped.append(f"{pf.name}:{node.name}(needs {','.join(needs)})")
                continue
            total += 1
            if any(ln in hits for ln in range(node.lineno + 1, (node.end_lineno or node.lineno) + 1)):
                covered += 1
            else:
                uncovered.append(f"{pf.name}:{node.name}")
    if uncovered:
        return False, f"Coverage {covered}/{total}; uncovered: {', '.join(uncovered[:8])}"
    note = ""
    if skipped:
        note = (f"; {len(skipped)} tool-gated function(s) excluded because their EDA "
                f"binary is absent: {', '.join(skipped[:4])}")
    return True, (f"100.0% function coverage ({covered}/{total} functions across "
                  f"{len(py_files)} modules){note}")


def _check_layer5_intent_and_docs() -> Tuple[bool, str]:
    # 1. Every registered tool MUST be documented in docs/USER_MANUAL.md
    manual_p = ROOT / "docs" / "USER_MANUAL.md"
    if not manual_p.is_file():
        return False, "docs/USER_MANUAL.md missing"
    manual_txt = manual_p.read_text(encoding="utf-8")
    missing_docs = [t for t in TOOL_REGISTRY if t not in manual_txt]
    if missing_docs:
        return False, f"Tools missing from docs/USER_MANUAL.md: {missing_docs}"

    # 2. Docs HTML derivation sync
    r_doc = subprocess.run([sys.executable, "docs/build_docs_html.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    if r_doc.returncode != 0:
        return False, f"build_docs_html.py --check failed: {r_doc.stdout} {r_doc.stderr}"

    # 2b. changelog 索引一致性 + 顶层 CHANGELOG.md 摘要同步。
    #     历史上出现过两个 `10-*.md` 撞号、`01`–`03` 长期是反引号而非链接、
    #     新增条目忘记登记 INDEX 等漂移。这些靠人工 review 不可靠（评审 184
    #     也是人工翻出来的），改为门禁自动校验。
    r_cl = subprocess.run(
        [sys.executable, "scripts/sync_changelog_index.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r_cl.returncode != 0:
        return False, (
            "changelog 索引/摘要校验失败（运行 python3 scripts/sync_changelog_index.py --write 修复）：\n"
            + (r_cl.stdout or "") + (r_cl.stderr or "")
        )

    # 3. No broken relative links in README.md and docs/USER_MANUAL.md
    for mf in (ROOT / "README.md", manual_p):
        t = mf.read_text(encoding="utf-8")
        for m in re.finditer(r"\[[^\]]+\]\(([^)#]+)\)", t):
            tgt = m.group(1).strip()
            if tgt.startswith(("http://", "https://", "mailto:")):
                continue
            if not (mf.parent / tgt).resolve().exists():
                return False, f"Broken link in {mf.name}: {tgt}"
    # 4. 诊断码文档覆盖（D13）：新增一个诊断码却忘了写进 docs/FEATURES.md 时，
    #    本地 --fast 过去查不出来，只有 CI 的 docs_check.py 才报错 —— 一次真实
    #    踩坑：新增 W052/W053 后本地全绿、CI 四个 test job 全红。把这条前移到
    #    本地门禁，代价很低（纯文本扫描），收益是"文档漏登记"当场可见。
    # 与 CI 的 examples/axi_apb_ref/axi/docs_check.py（D13）保持同一口径：
    # 「声明的码」取包内的**字符串字面量** `"E0xx"`/`"W0xx"`，文档侧扫
    # README + 全部 docs/*.md。不要用宽松的 \b 匹配（那会把注释里提到的码
    # 也算成"声明"，且会误把 E003–E009 这类只在 ranges 里出现的码判成缺失）。
    doc_files = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))
    doc_src = "\n".join(f.read_text(encoding="utf-8", errors="ignore")
                        for f in doc_files if f.is_file())
    declared = set()
    for p_ in sorted((ROOT / "fpga_toolbox").rglob("*.py")):
        declared.update(re.findall(r'"([EW]\d{3})"',
                                   p_.read_text(encoding="utf-8", errors="ignore")))
    doc_codes = set(re.findall(r"([EW]\d{3})", doc_src))
    # 范围概括（如 "E000–E015"）视为覆盖了区间内的码 —— README/USER_MANUAL 用
    # 这种写法列举整段诊断码，逐码核对会把它们全部误判为缺失。
    for m in re.finditer(r"(E\d{3})\s*[-–—]\s*(E\d{3})", doc_src):
        lo, hi = m.group(1), m.group(2)
        if lo[0] == hi[0] == "E":
            for n in range(int(lo[1:]), int(hi[1:]) + 1):
                doc_codes.add(f"E{n:03d}")
    undocumented = sorted(c for c in declared if c not in doc_codes)
    if undocumented:
        return False, (f"{len(undocumented)} diagnostic code(s) used in the package "
                       f"but not documented in docs/FEATURES.md or README.md: "
                       f"{undocumented}")

    return True, (f"all {len(TOOL_REGISTRY)} tools documented + HTML docs in sync + "
                  f"0 broken links + {len(declared)} diagnostic codes documented")


def _check_layer6_rtl_and_tb_integrity() -> Tuple[bool, str]:
    ref = ROOT / "examples" / "multi_bus_ref"
    duts = sorted(p for p in ref.rglob("*.sv") if not p.name.startswith("tb_"))
    for sv in duts:
        r = tool_extract_interfaces(str(sv))
        if not r["ok"]:
            return False, f"extract_interfaces failed on {sv.name}: {r.get('error')}"
    subdirs = [ref, ref / "complex_datapath", ref / "soc_fabric", ref / "full_conversion_suite", ref / "omni_hub"]
    for sub in subdirs:
        if not sub.is_dir():
            continue
        lr = tool_lint_tb(str(sub))
        if not lr["ok"] or lr["data"]["warning_count"] > 0:
            return False, f"lint_tb failed on {sub.name}: e={lr['data']['error_count']} w={lr['data']['warning_count']}"
    return True, f"{len(duts)}/{len(duts)} DUTs parsed cleanly + 5/5 TB dirs lint_tb 0 errors / 0 warnings"


def main() -> int:
    ap = argparse.ArgumentParser(description="fpga_toolbox six-layer invariant & intent-drift gate")
    ap.add_argument("--fast", action="store_true", help="Skip full coverage trace (pre-commit mode)")
    ap.add_argument("--full", action="store_true", help="Run all 6 layers including 100% function coverage trace")
    args = ap.parse_args()

    layers: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = [
        ("Layer 1: Schema<->Signature Bijection", _check_layer1_bijection),
        ("Layer 2: Adversarial Anti-False-Pass Matrix", _check_layer2_adversarial_invariants),
        ("Layer 3: Static Hygiene (ruff + compileall)", _check_layer3_static_hygiene),
        ("Layer 5: Intent-Drift & Docs Sync", _check_layer5_intent_and_docs),
        ("Layer 6: RTL AST & TB Lint Integrity", _check_layer6_rtl_and_tb_integrity),
    ]
    if args.full or not args.fast:
        layers.insert(3, ("Layer 4: 100% Function Coverage Floor", _check_layer4_function_coverage))

    failed = 0
    for label, fn in layers:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label}: {detail}")
        if not ok:
            failed += 1
    if failed:
        print(f"\nINVARIANT GATE FAILED ({failed} layer(s) violated)")
        return 1
    print(f"\nALL {len(layers)} INVARIANT LAYERS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
