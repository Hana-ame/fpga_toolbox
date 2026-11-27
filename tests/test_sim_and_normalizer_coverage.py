"""仿真运行与结果归一（suite 身份、伪 PASS 拦截、日志去重）

仿真运行与结果归一（suite 身份、伪 PASS 拦截、日志去重）

从 tests/test_full_coverage.py 按领域拆分而来（原文件 ~5500 行、86 个用例，
定位失败需要通读全文）。拆分只搬移代码，不改断言。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fpga_toolbox.registry import call_tool  # noqa: E402
from fpga_toolbox.sim_runner import _fst_available  # noqa: E402


def test_sim_runner_fst_probe() -> None:
    print("coverage: sim_runner._fst_available probe")
    # Missing binary must cleanly return (False, reason)
    ok_bad, msg_bad = _fst_available("nonexistent_verilator_binary_xyz")
    assert ok_bad is False and msg_bad

    # Real verilator binary probe (either True if lz4.h installed, or False with diagnostic)
    ok_real, msg_real = _fst_available("verilator")
    assert isinstance(ok_real, bool) and isinstance(msg_real, str)

def test_suite_identity_requires_per_suite_summary() -> None:
    """Defect 117: 套件「打印过 PASS 之后崩溃」仍被判 PASS（两处静默通过）。

    正则/计数法都只看「日志里出现过 PASS」：

    1. `sim_runner.verify_suite_identities` 对任意含 `\bPASS\b` 的行判该套件
       PASS。套件打印 `[CHECK] id=C1 | status=PASS` 后在 `$finish` 之前崩溃
       （断言挂 / 段错误 / 超时被杀），仍被判 PASS。
    2. `result_normalizer` 的完成度只看**汇总行总数** >= 启动套件数。A 打印两条
       `PASS:`、B 崩溃一条都没有时，总数仍够 —— 实测 overall_status=PASS。

    正确口径：每个**启动过**的套件都必须有自己的 `PASS:` 结束汇总行；只有
    STARTED 没有汇总行 = 中途崩溃 = FAIL。
    """
    print("coverage: suite identity needs per-suite PASS summary (crash is not PASS)")
    from fpga_toolbox.result_normalizer import tool_normalize_sim_result
    from fpga_toolbox.sim_runner import verify_suite_identities

    # 1) 只在 CHECK 行里出现过 PASS、随后崩溃 => 不能算 PASS
    crash_log = ("== sim: apb (top=tb) ==\n"
                 "[CHECK] id=C1 | status=PASS\n%Error: Assertion failed\n")
    r = verify_suite_identities(crash_log, ["apb"])
    assert r["observed"].get("apb") == "FAIL", r
    assert r["all_verified"] is False, r

    # 正常跑完（有自身汇总行）=> PASS
    ok_log = ("== sim: apb (top=tb) ==\n"
              "[CHECK] id=C1 | status=PASS\nPASS: apb all 8 checks passed\n")
    r2 = verify_suite_identities(ok_log, ["apb"])
    assert r2["observed"].get("apb") == "PASS", r2
    assert r2["all_verified"] is True, r2

    # 2) 计数法绕过：A 两条 PASS、B 零条 => B 必须 FAIL
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "v.log"
        log.write_text(
            "== sim: A (top=t) ==\nPASS: a\nPASS: a duplicated\n"
            "[CHECK] id=A1 | status=PASS\n"
            "== sim: B (top=t) ==\n[CHECK] id=B1 | status=PASS\n%Error crash\n",
            encoding="utf-8")
        nr = tool_normalize_sim_result("m", verilator_log_path=str(log),
                                       expected_suites=["A", "B"])
        d = nr["data"]
        assert d["overall_status"] == "FAIL", d["overall_status"]
        assert any(c["rule_id"] == "VERILATOR_NO_SUMMARY" and c["status"] == "FAIL"
                   for c in d["checks"]), [c["rule_id"] for c in d["checks"]]

        # 两个套件都各自有汇总行 => 仍然是 PASS（不能因为加严而误伤）
        good = Path(td) / "good.log"
        good.write_text(
            "== sim: A (top=t) ==\n[CHECK] id=A1 | status=PASS\nPASS: A ok\n"
            "== sim: B (top=t) ==\n[CHECK] id=B1 | status=PASS\nPASS: B ok\n",
            encoding="utf-8")
        ng = tool_normalize_sim_result("m", verilator_log_path=str(good),
                                       expected_suites=["A", "B"])
        assert ng["data"]["overall_status"] == "PASS", ng["data"]["checks"]
        assert not any(c["rule_id"] == "VERILATOR_NO_SUMMARY"
                       for c in ng["data"]["checks"])

def test_normalizer_rejects_malformed_and_unnamed_results() -> None:
    """Defect 119: normalize_sim_result 对畸形结果「静默 NO_RESULTS」或裸 AttributeError。

    模块 docstring 承诺 "Unrecognized input is reported as an error instead of
    silently yielding an empty PASS/NO_RESULTS report"，但旧实现有三处违背：

    1. `checks: [str/None]` → `c.get(...)` 抛 `AttributeError: 'str' object has
       no attribute 'get'`，用户看不出是 checks 数组混进了非对象。
    2. `tests: [str/None/42]` → 被 `if isinstance(t, dict)` **静默过滤**，
       变成 0 条检查 → summary.total=0 → overall_status=NO_RESULTS 且
       **ok=True**。调用方看到「成功 + 零检查」，真相是文件根本无法解析。
    3. JUnit XML 里既无 name 也无 classname 的 `<testcase/>`（零证据）被记成
       rule_id="UNKNOWN"、status=PASS → 整份报告 overall_status=PASS，
       比「零 testcase」（正确判 NO_RESULTS）还宽松。
    """
    print("coverage: normalize_sim_result rejects malformed items and unnamed testcases")
    from fpga_toolbox.result_normalizer import tool_normalize_sim_result

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # 1) checks 里混入非对象 => 可读错误，不是 AttributeError 裸文案
        for bad in (["oops"], [None], [{"rule_id": "R1"}, "x"]):
            f = base / f"c{abs(hash(str(bad)))}.json"
            f.write_text(json.dumps({"checks": bad}), encoding="utf-8")
            r = tool_normalize_sim_result("m", cocotb_results_json=str(f))
            assert r["ok"] is False, (bad, r.get("data"))
            err = str(r["error"])
            assert "must be a list of objects" in err, (bad, err[:200])
            assert "attribute 'get'" not in err, (bad, err[:200])

        # 2) tests 里混入非对象 => 报错，不能静默变成 NO_RESULTS + ok=True
        f = base / "t.json"
        f.write_text(json.dumps({"tests": ["oops", None, 42]}), encoding="utf-8")
        r = tool_normalize_sim_result("m", cocotb_results_json=str(f))
        assert not r["ok"], f"all-invalid tests silently passed: {r.get('data')}"
        assert "must be a list of objects" in str(r["error"]), str(r["error"])[:200]

        # 3) 无名 testcase => FAIL（零证据不算通过），而不是 PASS
        xml = base / "u.xml"
        xml.write_text('<?xml version="1.0"?><testsuites><testsuite>'
                       '<testcase/></testsuite></testsuites>', encoding="utf-8")
        r = tool_normalize_sim_result("m", cocotb_results_json=str(xml))
        assert r["data"]["overall_status"] == "FAIL", r["data"]["checks"]
        assert any(c["rule_id"] == "UNNAMED_TESTCASE" and c["status"] == "FAIL"
                   for c in r["data"]["checks"]), r["data"]["checks"]

        # 正常 JUnit（有名字、无 failure）仍 PASS —— 不能过度收紧
        ok_xml = base / "ok.xml"
        ok_xml.write_text('<?xml version="1.0"?><testsuites><testsuite>'
                          '<testcase name="T_OK"/></testsuite></testsuites>',
                          encoding="utf-8")
        r = tool_normalize_sim_result("m", cocotb_results_json=str(ok_xml))
        assert r["data"]["overall_status"] == "PASS", r["data"]["checks"]

        # 4) engine 版本不再写死：从日志抓真实版本，抓不到留空
        log = base / "v.log"
        log.write_text(
            "== sim: apb (top=t) ==\n"
            "[CHECK] id=A1 | proto=APB | exp=0x1 | got=0x1 | status=PASS\n"
            "- Verilator 5.020 2024-01-01 rev (Debian 5.020-1)\n"
            "PASS: apb ok\n", encoding="utf-8")
        r = tool_normalize_sim_result("m", verilator_log_path=str(log),
                                      expected_suites=["apb"])
        engines = {c.get("engine") for c in r["data"]["checks"] if c.get("engine")}
        assert engines == {"Verilator 5.020"}, engines

def test_normalizer_surfaces_verilator_errors_and_empty_verification() -> None:
    """Defect 142/143: 日志里的 %Error 与「零套件核对」都不能算正常。

    142: result_normalizer 完全不看 `%Error:` 行。一份「%Error: syntax error」
    混着「PASS: ...」的日志被归一成 overall_status=NO_RESULTS、零诊断 ——
    一个编译都没过的仿真既不失败也不提示。修法：把 %Error 行转成
    rule_id=VERILATOR_ERROR 的 FAIL 检查，使 overall_status=FAIL。

    143: `all_verified` 的语义是「预期套件没缺失/没失败」，expected_suites 为空
    时必然为 true。但它与 observed={} 并排出现会被误读成「核对通过」。
    新增 observed_any / verified_any 明确区分「没核对」与「核对通过」。
    （all_verified 语义保持不变：自审计 A6 依赖它对「无预期」不假报警。）
    """
    print("coverage: normalizer surfaces %Error and empty verification")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        log = root / "sim.log"
        log.write_text(
            "%Error: some_sv.sv:10:5: syntax error\n"
            "PASS: apb all 8 checks passed\n", encoding="utf-8")

        r = call_tool("normalize_sim_result", {
            "verilator_log_path": str(log), "module_name": "apb"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        # 142: %Error 必须让 overall_status 变 FAIL，且作为一条 FAIL 检查出现
        assert data["overall_status"] == "FAIL", data["overall_status"]
        assert data["summary"]["failed"] >= 1, data["summary"]
        rules = {c["rule_id"]: c["status"] for c in data["checks"]}
        assert rules.get("VERILATOR_ERROR") == "FAIL", rules

        # 143: 日志里没有 `== sim: ... ==` 头 → 没观察到任何套件
        ident = data["verilator_suite_identity"]
        assert ident["all_verified"] is True, ident          # 语义不变
        assert ident["observed_any"] is False, ident         # 但确实没核对
        assert ident["verified_any"] is False, ident

        # 对照组：有真实 suite 头 + PASS 汇总行 → observed_any / verified_any 为真
        ok_log = root / "ok.log"
        ok_log.write_text(
            "== sim: apb (top=tb_apb) ==\n"
            "PASS: apb all 8 checks passed\n", encoding="utf-8")
        ro = call_tool("normalize_sim_result", {
            "verilator_log_path": str(ok_log), "module_name": "apb",
            "expected_suites": ["apb"]})
        assert ro["ok"], str(ro.get("error"))[:200]
        io = ro["data"]["verilator_suite_identity"]
        assert io["observed_any"] is True, io
        assert io["verified_any"] is True, io
        assert ro["data"]["overall_status"] == "PASS", ro["data"]["overall_status"]

        # 纯干净日志（无 %Error）不得出现 VERILATOR_ERROR 假报
        rules_ok = {c["rule_id"] for c in ro["data"]["checks"]}
        assert "VERILATOR_ERROR" not in rules_ok, rules_ok

def test_silent_pass_gates_uvm_fatal_zero_frames_completed_xml() -> None:
    """Defect 128: 四处静默假通过（UVM_FATAL / 零帧 / completed 绕过 / XML 结构）。

    1. `parse_uvm_log` 只读 `UVM_ERROR`，完全忽略 `UVM_FATAL`：
       一份打印 `UVM_FATAL : 3` 的日志被判 `passed=True`。
    2. 未传 `expected_frames` 时 `target_frames = frames_obs`，于是
       `delivered=0` + `frames=0` 得到 `0 == 0` → 一个**什么都没跑**的仿真通过。
    3. `result_normalizer` 的 `COCOTB_INCOMPLETE` 门禁用 `is False` 判定：
       `"completed": "false"` 与 `"completed": 0` 都能绕过；且走 `"tests"`
       分支时 `completed` 被整条丢弃，`{"tests": [...], "completed": false}`
       直接 PASS。
    4. `_from_xml` 不校验根标签、不读 suite 级 failures/errors、且无名检查用
       `if not name`（纯空白 name 是 truthy）：非 JUnit XML 得到
       `ok=True + NO_RESULTS`，suite 级 `<error>` / `failures="3"` 判 PASS，
       `name="   "` 判 PASS。
    """
    print("coverage: UVM_FATAL / zero-frame / completed bypass / JUnit XML structure")
    from fpga_toolbox.eda_campaign import parse_uvm_log

    base = ("[C3900] NP_LAT: frames=64 min=10 avg=12 max=20\n"
            "delivered=64\nfid_bad=0\nall_retry=0\nsend_nums=64\n"
            "win_span=5\nUVM_ERROR :    0\n")
    assert parse_uvm_log(base)["ok"] is True, "clean UVM log must pass"
    # 1) UVM_FATAL 必须判失败
    assert parse_uvm_log(base + "UVM_FATAL :    3\n")["ok"] is False, "UVM_FATAL ignored"
    assert parse_uvm_log(base.replace("UVM_ERROR :    0", "UVM_ERROR :    5"))["ok"] is False
    # 2) 零帧不是通过
    zero = ("[C3900] NP_LAT: frames=0 avg=0\ndelivered=0\nfid_bad=0\n"
            "all_retry=0\nsend_nums=0\nUVM_ERROR :    0\n")
    assert parse_uvm_log(zero)["ok"] is False, "zero-frame run treated as pass"

    # 3) completed 的三种绕过形态
    from fpga_toolbox.result_normalizer import tool_normalize_sim_result

    payloads = {
        "bool False": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": False},
        "str false": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": "false"},
        "int 0": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": 0},
        "tests + False": {"tests": [{"name": "T", "status": "PASS"}], "completed": False},
        "tests + str false": {"tests": [{"name": "T", "status": "PASS"}], "completed": "false"},
    }
    with tempfile.TemporaryDirectory() as td:
        for label, payload in payloads.items():
            f = Path(td) / f"{abs(hash(label))}.json"
            f.write_text(json.dumps(payload), encoding="utf-8")
            d = tool_normalize_sim_result("m", cocotb_results_json=str(f))["data"]
            assert d["overall_status"] == "FAIL", (label, d["overall_status"])
            assert any(c["rule_id"] == "COCOTB_INCOMPLETE" for c in d["checks"]), label
        # 显式 True / 缺省都不能被误伤
        for label, payload in {
            "bool True": {"checks": [{"rule_id": "A", "status": "PASS"}], "completed": True},
            "absent": {"checks": [{"rule_id": "A", "status": "PASS"}]},
        }.items():
            f = Path(td) / f"{abs(hash(label))}.json"
            f.write_text(json.dumps(payload), encoding="utf-8")
            d = tool_normalize_sim_result("m", cocotb_results_json=str(f))["data"]
            assert d["overall_status"] == "PASS", (label, d["checks"])

        # 4) XML 结构校验
        xml_cases = {
            "non-JUnit root": ('<?xml version="1.0"?><foo><bar/></foo>', False, None),
            "suite error node": (
                '<?xml version="1.0"?><testsuites><testsuite><testcase name="T"/>'
                '</testsuite><errors><error message="setup failed"/></errors></testsuites>',
                True, "FAIL"),
            "suite failures attr": (
                '<?xml version="1.0"?><testsuites><testsuite failures="3">'
                '<testcase name="T"/></testsuite></testsuites>', True, "FAIL"),
            "whitespace name": (
                '<?xml version="1.0"?><testsuites><testsuite>'
                '<testcase name="   "/></testsuite></testsuites>', True, "FAIL"),
            "clean JUnit": (
                '<?xml version="1.0"?><testsuites><testsuite>'
                '<testcase name="T_OK"/></testsuite></testsuites>', True, "PASS"),
            "real failure": (
                '<?xml version="1.0"?><testsuites><testsuite><testcase name="T">'
                '<failure message="boom"/></testcase></testsuite></testsuites>',
                True, "FAIL"),
        }
        for label, (body, want_ok, want_overall) in xml_cases.items():
            f = Path(td) / f"{abs(hash(label))}.xml"
            f.write_text(body, encoding="utf-8")
            r = tool_normalize_sim_result("m", cocotb_results_json=str(f))
            assert r["ok"] is want_ok, (label, r.get("error"))
            if want_overall is not None:
                assert r["data"]["overall_status"] == want_overall, (
                    label, r["data"]["overall_status"])

def test_residual_and_suite_rerun_gates() -> None:
    """Defect 129: 残留检测的删除型误报/重复模式漏报 + suite 重跑沿用旧状态。

    1. `check_residual_mutations` 用 `m["new"] in txt` 判残留。删除型变异
       （`new == ""`）时 `"" in txt` **恒真**，于是任何「old 已消失」的文件都
       被判残留 → 合法的删除型变异永远无法运行（实测已变异的
       `assign y =;` 对 old=' a & b' new='' 必报 E030）。
    2. 变异主循环用 `replace(..., 1)` 只替换**第一处**。若 old 在文件中出现多
       次、第一处已变异而后面仍是 old，则 `old in txt` 为真 → 旧判据认为没有
       残留，**漏报**真实未恢复的混合态（实测）。
    3. `verify_suite_identities` 用 `results.setdefault(current, "STARTED")`
       —— 同名 suite **重跑**时不重置状态：第一次 PASS、第二次被截断（无任何
       输出）时沿用第一次的 PASS 与 summaries，判 `all_verified=True`。
    """
    print("coverage: residual gates (deletion/duplicate) + suite rerun state reset")
    from fpga_toolbox.mutation_runner import check_residual_mutations
    from fpga_toolbox.sim_runner import verify_suite_identities

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 1) 删除型变异（new=""）不参与残留检查 —— 否则必然误报
        (root / "del.sv").write_text("assign y =;\n", encoding="utf-8")
        r = check_residual_mutations(root, [
            {"id": "D1", "file": "del.sv", "old": " a & b", "new": "", "desc": "delete"}])
        assert r == [], f"deletion-type mutation false-positive residual: {r}"

        # 干净文件 + 删除型变异也不该报
        (root / "clean.sv").write_text("assign y = a & b;\n", encoding="utf-8")
        r2 = check_residual_mutations(root, [
            {"id": "D2", "file": "clean.sv", "old": " a & b", "new": "", "desc": "d"}])
        assert r2 == [], r2

        # 2a) 完全未恢复（old 消失、new 在）必须报
        (root / "full.sv").write_text("assign y = (a | b);\n", encoding="utf-8")
        r3 = check_residual_mutations(root, [
            {"id": "M1", "file": "full.sv", "old": "a & b", "new": "a | b", "desc": "x"}])
        assert len(r3) == 1 and r3[0]["code"] == "E030", r3

        # 2b) 混合态（第一处已改、第二处仍是 old）必须报 —— 旧判据漏报
        (root / "mixed.sv").write_text(
            "assign a = x | y;\nassign b = x & y;\n", encoding="utf-8")
        r4 = check_residual_mutations(root, [
            {"id": "M2", "file": "mixed.sv", "old": "x & y", "new": "x | y", "desc": "d"}])
        assert len(r4) == 1 and r4[0]["code"] == "E030", r4

        # 2c) 完全干净（只有 old、没有 new）不能误报
        (root / "ok.sv").write_text("assign a = x & y;\n", encoding="utf-8")
        r5 = check_residual_mutations(root, [
            {"id": "M3", "file": "ok.sv", "old": "x & y", "new": "x | y", "desc": "d"}])
        assert r5 == [], r5

    # 3) suite 重跑：截断的重跑不能沿用第一次 PASS
    trunc = ("== sim: apb (top=t) ==\nPASS: apb ok\n"
             "== sim: apb (top=t) ==\n")
    rr = verify_suite_identities(trunc, ["apb"])
    assert rr["all_verified"] is False, rr
    # 截断的重跑不能算通过。缺陷 191 修复后「启动过但没有自己的汇总行」在任何
    # 状态下都归为 FAIL（旧实现只转 PASS，STARTED 会被漏成"没失败"）。
    assert rr["observed"]["apb"] == "FAIL", rr["observed"]

    # 重跑也完整通过 -> 仍 PASS
    both = ("== sim: apb (top=t) ==\nPASS: apb ok\n"
            "== sim: apb (top=t) ==\nPASS: apb ok again\n")
    rb = verify_suite_identities(both, ["apb"])
    assert rb["all_verified"] is True, rb

    # 重跑崩溃 -> FAIL
    crash = ("== sim: apb (top=t) ==\nPASS: apb ok\n"
             "== sim: apb (top=t) ==\n[CHECK] id=A | status=PASS\n%Error: crash\n")
    rc = verify_suite_identities(crash, ["apb"])
    assert rc["all_verified"] is False and rc["observed"]["apb"] == "FAIL", rc

def test_json_results_unnamed_entries_are_not_pass_evidence() -> None:
    """Defect 153: cocotb JSON 里无名称的结果项不能算「通过证据」。

    XML 分支（缺陷 119）已经改成：`<testcase />` 没有 name 时判
    UNNAMED_TESTCASE FAIL。JSON 分支当时漏了 —— 用
    `t.get("name", t.get("rule_id", "UNKNOWN"))` 兜底成字符串 "UNKNOWN"：

        {"tests": [{"name":"t1","status":"passed"}, {"status":"passed"}]}
        → total=2, passed=2, overall_status=PASS

    一条无从归属的「通过」被当成真实证据。`checks` 分支同样问题。
    """
    print("coverage: unnamed JSON result entries are not pass evidence")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def _run(name: str, obj) -> dict:
            fp = root / name
            fp.write_text(json.dumps(obj), encoding="utf-8")
            return call_tool("normalize_sim_result", {
                "cocotb_results_json": str(fp), "module_name": "m"})

        # tests 分支：无名项 → FAIL
        r = _run("tests.json", {"tests": [
            {"name": "t1", "status": "passed"}, {"status": "passed"}]})
        assert r["ok"], str(r.get("error"))[:200]
        d = r["data"]
        assert d["overall_status"] == "FAIL", d["overall_status"]
        rules = {c["rule_id"]: c["status"] for c in d["checks"]}
        assert rules.get("UNNAMED_TESTCASE") == "FAIL", rules
        assert d["summary"]["failed"] >= 1, d["summary"]

        # 空白 name 同样算无名
        r2 = _run("blank.json", {"tests": [
            {"name": "t1", "status": "passed"}, {"name": "   ", "status": "passed"}]})
        d2 = r2["data"]
        assert d2["overall_status"] == "FAIL", d2["overall_status"]

        # checks 分支：同样语义
        r3 = _run("checks.json", {"checks": [
            {"rule_id": "c1", "status": "PASS"}, {"status": "PASS"}]})
        d3 = r3["data"]
        assert d3["overall_status"] == "FAIL", d3["overall_status"]
        rules3 = {c["rule_id"]: c["status"] for c in d3["checks"]}
        assert rules3.get("UNNAMED_TESTCASE") == "FAIL", rules3

        # 全部有名且通过 → 仍然 PASS（不能假报警）
        r4 = _run("ok.json", {"tests": [
            {"name": "t1", "status": "passed"}, {"name": "t2", "status": "passed"}]})
        d4 = r4["data"]
        assert d4["overall_status"] == "PASS", d4
        assert d4["summary"]["passed"] == 2, d4["summary"]

        # rule_id 作为标识是合法的（tests 分支）
        r5 = _run("ruleid.json", {"tests": [
            {"rule_id": "r1", "status": "passed"}]})
        d5 = r5["data"]
        assert d5["overall_status"] == "PASS", d5

def test_check_lines_and_suite_summary_precedence() -> None:
    """Defect 154/155: `[CHECK]` 明细行不能静默消失，也不能篡改套件结论。

    154: `_parse_verilator_checks` 对没有 `id` 的 `[CHECK]` 行直接 `continue`
         静默丢弃。`[CHECK] status=FAIL exp=1 got=2` 这条明确的失败证据凭空
         消失（与 XML/JSON 无名项口径不一致）。
    155: `verify_suite_identities` 把 `\bFAIL\b` 关键词检查放在**汇总行之前**，
         于是逐事务明细 `[CHECK] ... | status=FAIL | ...` 会让整个套件判 FAIL，
         即使套件自己的汇总行是 `PASS:` —— 实测得到
         observed={'apb':'FAIL'} 而 summaries={'apb':'PASS'} 的自相矛盾。
         汇总行才是套件级结论的权威；无汇总行时 CHECK FAIL 仍是失败证据。
    """
    print("coverage: CHECK lines and suite summary precedence")
    from fpga_toolbox.result_normalizer import _parse_verilator_checks
    from fpga_toolbox.sim_runner import verify_suite_identities

    # 154: 无 id 的 CHECK 行必须保留为 UNNAMED_TESTCASE FAIL
    checks = _parse_verilator_checks("[CHECK] status=FAIL exp=1 got=2\n")
    assert len(checks) == 1, checks
    assert checks[0]["rule_id"] == "UNNAMED_TESTCASE", checks[0]
    assert checks[0]["status"] == "FAIL", checks[0]
    # 有 id 的正常行不受影响
    ok_checks = _parse_verilator_checks("[CHECK] id=C1 | status=PASS\n")
    assert [c["rule_id"] for c in ok_checks] == ["C1"], ok_checks

    # 155: 明细 FAIL + 汇总 PASS → 套件判 PASS（汇总权威）
    log = ("== sim: apb (top=tb_apb) ==\n"
           "[CHECK] id=C1 | status=FAIL | exp=1 | got=2\n"
           "PASS: apb all 8 checks passed\n")
    r = verify_suite_identities(log, ["apb"])
    assert r["observed"]["apb"] == "PASS", r
    assert r["all_verified"] is True, r

    # 明细 FAIL + 汇总 FAIL → FAIL
    log2 = ("== sim: apb ==\n"
            "[CHECK] id=C1 | status=FAIL\n"
            "FAIL: apb 1 errors\n")
    r2 = verify_suite_identities(log2, ["apb"])
    assert r2["observed"]["apb"] == "FAIL", r2

    # 明细 FAIL 且**无**汇总行 → 仍是失败证据（不能因重排而放行）
    log3 = "== sim: apb ==\n[CHECK] id=C1 | status=FAIL\n"
    r3 = verify_suite_identities(log3, ["apb"])
    assert r3["observed"]["apb"] == "FAIL", r3

    # 崩溃痕迹 + 无汇总行 → FAIL
    log4 = "== sim: apb ==\n[CHECK] id=C1 | status=PASS\nSegmentation fault\n"
    r4 = verify_suite_identities(log4, ["apb"])
    assert r4["observed"]["apb"] == "FAIL", r4

    # 干净日志 → PASS
    r5 = verify_suite_identities("== sim: apb ==\nPASS: apb all\n", ["apb"])
    assert r5["all_verified"] is True, r5

def test_empty_report_cannot_claim_pass() -> None:
    """缺陷 188: 0 条检查 + overall_status=PASS 被判一致（零证据通过）。

    W053 原本只在 `n > 0` 时检查「PASS 但无一条通过」，于是

        {"checks": [], "overall_status": "PASS", "summary": {全部 0}}

    得到 `consistency_ok=True`、零诊断 —— 一份没有任何证据行的报告宣称通过。
    而同性质的「全部 SKIP 却 PASS」反倒会被 W053 抓到（`n > 0` 成立），
    两种情形处理不一致。

    修复：`n == 0` 且 overall=PASS 时同样报 W053。
    """
    print("coverage: empty report cannot claim pass")

    def _render(rep):
        with tempfile.TemporaryDirectory() as td:
            return call_tool("render_visual_report", {
                "report": rep,
                "dep_graph": {"nodes": [], "edges": []},
                "extracted_interfaces": [],
                "out_html_path": str(Path(td) / "r.html")})

    # 0 条检查 + PASS → 必须不一致
    r = _render({"module": "m",
                 "summary": {"total": 0, "passed": 0, "failed": 0},
                 "checks": [], "overall_status": "PASS"})
    assert r["ok"], str(r.get("error"))[:200]
    assert r["data"]["consistency_ok"] is False, r["data"]
    codes = [d.get("code") for d in (r.get("diagnostics") or [])]
    assert "W053" in codes, codes

    # 0 条检查 + FAIL → 不矛盾（什么都不做就是失败，合理）
    r2 = _render({"module": "m",
                  "summary": {"total": 0, "passed": 0, "failed": 0},
                  "checks": [], "overall_status": "FAIL"})
    assert r2["data"]["consistency_ok"] is True, r2["data"]
    assert "W053" not in [d.get("code") for d in (r2.get("diagnostics") or [])]

    # 正常 PASS 不受影响（不假报）
    r3 = _render({"module": "m",
                  "summary": {"total": 1, "passed": 1, "failed": 0},
                  "checks": [{"rule_id": "r", "status": "PASS"}],
                  "overall_status": "PASS"})
    assert r3["data"]["consistency_ok"] is True, r3["data"]
    assert "W053" not in [d.get("code") for d in (r3.get("diagnostics") or [])]

def test_summary_payloads_are_type_checked() -> None:
    """缺陷 189: coverage_summary / mutation_summary 的非法字段原样透传。

    `normalize_sim_result` 把这两个 summary 直接塞进报告：

        {"coverage_summary": {"line": {"x": 1}},
         "mutation_summary": {"mutation_score": "abc"}}

    返回 ok=true、零诊断，报告里于是出现「覆盖率 = 一个 dict」
    「mutation_score = 'abc'」，带着非法类型一路流到 render_visual_report。
    渲染器恰好容错不崩，但报告数据本身已失真。

    修复：对上游最关键的数值字段做类型检查，非法值丢弃该字段并逐条报
    W014（"值被忽略"的既有诊断码），其余合法字段保留。
    """
    print("coverage: summary payloads are type checked")

    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "a.log"
        log.write_text(
            "== sim: apb (top=tb_apb) ==\n"
            "PASS: apb all 1 checks passed\n", encoding="utf-8")
        base = {"verilator_log_path": str(log), "module_name": "m"}

        r = call_tool("normalize_sim_result", {
            **base,
            "coverage_summary": {"line": {"x": 1}, "branch": 50.0},
            "mutation_summary": {"mutation_score": "abc", "killed": 3},
        })
        assert r["ok"], str(r.get("error"))[:200]
        cov = r["data"]["coverage_summary"]
        mut = r["data"]["mutation_summary"]
        # 非法字段被丢弃
        assert "line" not in cov, cov
        assert "mutation_score" not in mut, mut
        # 合法字段保留
        assert cov["branch"] == 50.0, cov
        assert mut["killed"] == 3, mut
        # 逐字段给出可见诊断
        paths = {(d["code"], d["path"]) for d in (r.get("diagnostics") or [])}
        assert ("W014", "coverage_summary.line") in paths, paths
        assert ("W014", "mutation_summary.mutation_score") in paths, paths

        # 全部合法时不得产生假诊断
        r2 = call_tool("normalize_sim_result", {
            **base,
            "coverage_summary": {"line": 87.5, "branch": 60},
            "mutation_summary": {"mutation_score": 91.0, "killed": 10},
        })
        assert r2["ok"], str(r2.get("error"))[:200]
        assert r2["data"]["coverage_summary"] == {"line": 87.5, "branch": 60}
        assert r2["data"]["mutation_summary"] == {"mutation_score": 91.0, "killed": 10}
        assert not [d for d in (r2.get("diagnostics") or []) if d["code"] == "W014"], \
            r2.get("diagnostics")

        # 非 dict 的 summary（类型校验已在 call_tool 层拦住，这里确保函数层也安全）
        r3 = call_tool("normalize_sim_result", {**base, "coverage_summary": {}})
        assert r3["ok"], str(r3.get("error"))[:200]
        assert r3["data"]["coverage_summary"] == {}, r3["data"]

def test_suite_verification_works_without_expected_list() -> None:
    """缺陷 190/191: 不传 expected_suites 时完全不核对；STARTED 漏成"没失败"。

    190: `run_sim` 里
             identity = verify_suite_identities(...) if expected_suites else None
         于是不传 expected_suites 时 `suite_identity` 恒为 null —— 日志里明明有
         两个套件的完整汇总行，却既没核对身份、也没检查完成度。

    191: `verify_suite_identities` 的「启动过但没有汇总行 → FAIL」只转换
         `status == "PASS"`，从未打印汇总行的套件状态是 `STARTED`，被漏掉：

             apb 打印 PASS，axi4 只打印 header 就崩溃
             → observed={'apb':'PASS','axi4':'STARTED'}, failed=[],
               all_verified=true

         一个真实崩溃被报告成核对通过。且 `failed` 只从 expected_suites 里取，
         无预期时恒为 []。
    """
    print("coverage: suite verification works without expected list")
    from fpga_toolbox.sim_runner import verify_suite_identities

    # 191a: STARTED 必须转成 FAIL
    log_crash = "== sim: apb ==\nPASS: apb ok\n== sim: axi4 ==\n[CHECK] id=C1\n"
    r = verify_suite_identities(log_crash, [])
    assert r["observed"].get("axi4") == "FAIL", r["observed"]
    assert "axi4" in r["failed"], r
    assert r["all_verified"] is False, r
    assert r["observed_any"] is True and r["verified_any"] is False, r

    # 191b: 有预期时同样正确
    r2 = verify_suite_identities(log_crash, ["apb", "axi4"])
    assert set(r2["failed"]) == {"axi4"}, r2
    assert r2["all_verified"] is False, r2

    # 无预期 + 全干净 -> all_verified 保持 True（不假报警，自审计 A6 依赖）
    r3 = verify_suite_identities("== sim: apb ==\nPASS: apb ok\n", [])
    assert r3["all_verified"] is True, r3
    assert r3["failed"] == [], r3
    assert r3["observed_any"] is True, r3

    # 完全没有套件 -> all_verified True 但 observed_any False（区分「没核对」）
    r4 = verify_suite_identities("", [])
    assert r4["all_verified"] is True, r4
    assert r4["observed_any"] is False, r4
    assert r4["verified_any"] is False, r4

    # 190: run_sim 不传 expected_suites 时也必须产出非 null 的核对结果
    with tempfile.TemporaryDirectory() as td:
        sh = Path(td) / "s.sh"
        sh.write_text(
            "#!/bin/bash\n"
            'echo "== sim: apb =="\n'
            'echo "PASS: apb ok"\n'
            'echo "== sim: axi4 =="\n'
            'echo "PASS: axi4 ok"\n', encoding="utf-8")
        sh.chmod(0o755)
        rr = call_tool("run_sim", {"sim_script": str(sh)})
        # 有 Verilator 时才可能真跑；没有则 run_sim 会因环境错误退出
        if rr["ok"]:
            ident = (rr["data"] or {}).get("suite_identity")
            assert ident is not None, rr["data"]
            assert ident["observed_any"] is True, ident
            assert set(ident["observed"]) == {"apb", "axi4"}, ident
            assert ident["all_verified"] is True, ident

def test_log_files_are_deduplicated() -> None:
    """缺陷 193: 重叠的搜索目录导致 log_files 出现重复条目。

    `run_sim` 的搜索目录是 `[脚本所在目录] + [log_dir]`。当两者相同（或一个是
    另一个的父目录）时，旧 `_collect_logs` 逐个 `rglob` 直接 append，同一个
    文件被记录多次：

        run_sim(sim_script=<dir>/s.sh, log_dir=<同一目录>)
        → log_files 里两条完全相同的 path

    修复：按 realpath 去重（符号链接/相对路径写法不同但指向同一文件时也合并）。
    """
    print("coverage: log files are deduplicated")

    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sh = root / "s.sh"
        sh.write_text(
            "#!/bin/bash\n"
            'echo "== sim: apb =="\n'
            'echo "PASS: apb ok"\n', encoding="utf-8")
        sh.chmod(0o755)

        r = call_tool("run_sim", {
            "sim_script": str(sh),
            "log_dir": str(root),          # 与脚本目录相同 → 必然重叠
            "suite": "apb_dedup",
        })
        if not r["ok"]:
            # 无 Verilator 等环境下 run_sim 可能整体失败，跳过产物断言
            return
        logs = (r["data"] or {}).get("log_files") or []
        paths = [x["path"] for x in logs]
        # 关键：不能有重复
        assert len(paths) == len(set(os.path.realpath(p) for p in paths)), paths
        # 且确实记录了本次的日志
        assert any("apb_dedup" in os.path.basename(p) for p in paths), paths

def test_lint_tb_zero_matches_is_not_silent_clean() -> None:
    """Defect 141: 目录模式扫到 0 个文件时必须区分「没检查」与「检查通过」。

    `_collect_targets` 在目录模式下只收「tb_ 前缀 / vip 目录 / 含 driver」的
    SV 文件（刻意的启发式，避免把 DUT 当 TB 检查）。于是 `my_testbench.sv`、
    `bad_tb.sv` 这类常见命名被静默跳过：一个只含 `bad_tb.sv` 的目录返回
    `scanned_sv_files=0, clean=true, findings=[]`，调用方以为 TB 已通过检查。
    """
    print("coverage: lint_tb distinguishes no-match from clean")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "bad_tb.sv").write_text(
            "module bad_tb;\n"
            "  initial begin $display(\"PASS: x\"); end\n"
            "endmodule\n", encoding="utf-8")

        # 目录模式：文件名不匹配启发式 → 必须显式说明「没扫到文件」
        r = call_tool("lint_tb", {"target_path": str(root)})
        d = r["data"]
        assert d["scanned_sv_files"] == 0, d
        assert d["scanned_anything"] is False, d
        codes = [(x["code"], x["severity"]) for x in (r.get("diagnostics") or [])]
        assert ("W055", "WARNING") in codes, codes
        # strict 模式下这是硬失败
        rs = call_tool("lint_tb", {"target_path": str(root), "strict": True})
        assert rs["ok"] is False, rs.get("error")

        # 直接传文件路径时必须照常检查（不受命名启发式限制）
        rf = call_tool("lint_tb", {"target_path": str(root / "bad_tb.sv")})
        df = rf["data"]
        assert df["scanned_sv_files"] == 1, df
        assert df["scanned_anything"] is True, df

        # 符合启发式的文件：照常扫描，不报 W055
        (root / "tb_good.sv").write_text(
            "module tb_good;\n"
            "  initial begin $display(\"PASS: x\"); $finish; end\n"
            "endmodule\n", encoding="utf-8")
        r2 = call_tool("lint_tb", {"target_path": str(root)})
        assert r2["data"]["scanned_sv_files"] >= 1, r2["data"]
        codes2 = [x["code"] for x in (r2.get("diagnostics") or [])]
        assert "W055" not in codes2, codes2


def main() -> int:
    test_sim_runner_fst_probe()
    test_suite_identity_requires_per_suite_summary()
    test_normalizer_rejects_malformed_and_unnamed_results()
    test_normalizer_surfaces_verilator_errors_and_empty_verification()
    test_silent_pass_gates_uvm_fatal_zero_frames_completed_xml()
    test_residual_and_suite_rerun_gates()
    test_json_results_unnamed_entries_are_not_pass_evidence()
    test_check_lines_and_suite_summary_precedence()
    test_empty_report_cannot_claim_pass()
    test_summary_payloads_are_type_checked()
    test_suite_verification_works_without_expected_list()
    test_log_files_are_deduplicated()
    test_lint_tb_zero_matches_is_not_silent_clean()
    print("ALL test_sim_and_normalizer_coverage.py TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
