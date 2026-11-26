"""Tool 7: Normalize cocotb / SVA / Verilator suite results into a strict VerificationReport schema.

Accepted inputs:
  * cocotb JUnit XML (``results.xml``)
  * JSON envelope carrying a ``checks`` or ``tests`` list, or a bare JSON list
  * SVA log containing ``[RULE:P_xxx]`` lines
  * Verilator multi-suite regression log (with per-suite identity verification)

Unrecognized input is reported as an error instead of silently yielding an
empty PASS/NO_RESULTS report.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result
from .sim_runner import verify_suite_identities

_SVA_ERR_RE = re.compile(r"\[RULE:(P_\d+)\]\s*(.+)")
# SVA checker 的心跳探针：证明 bound 后的断言引擎确实在跑。
# 生成器在通过时没有 [RULE:...] 输出，只有这个周期性命中；
# 心跳存在 = checker 在场且无违例，从而与「checker 根本没跑」区分开。
_SVA_HBEAT_RE = re.compile(r"\[SVA:HBEAT\]")
# 套件结束汇总行：生成的 TB 必定成对打印 PASS:/FAIL:（$finish 前）。
# 缺这一行说明仿真是**中途崩溃**（CHECK 之后没走到 $finish），
# 不能仅凭已有的若干 CHECK PASS 就判通过。
_SUITE_SUMMARY_RE = re.compile(r"^\s*(PASS|FAIL)\s*:\s*(.+)$", re.MULTILINE)
_STATUS_MAP = {
    "PASS": "PASS", "PASSED": "PASS", "OK": "PASS",
    "FAIL": "FAIL", "FAILED": "FAIL", "ERROR": "FAIL",
    "SKIP": "SKIP", "SKIPPED": "SKIP",
}


def _norm_status(raw: Any) -> str:
    return _STATUS_MAP.get(str(raw).strip().upper(), "FAIL")


def _norm_completed(raw: Any) -> Optional[bool]:
    """把 JSON/XML 里的 completed 标志归一成 True/False/None。

    旧实现用 `cocotb_completed is False` 判定 —— 只认 Python 的 False。而结果
    文件是**序列化后再读回**的，实测三种形态都能绕过这道完整性门禁：
      * `"completed": "false"`（字符串，某些序列化器会把 bool 写成 str）
      * `"completed": 0`（整数 0 / 空字符串也常被当作 false）
      * 走 `"tests"` 分支时 `completed` 被**整条丢弃**（函数直接 return，没读它）
    归一后：显式假值 -> False；显式真值 -> True；缺失/无法判定 -> None。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return bool(raw)
    txt = str(raw).strip().lower()
    if txt in ("false", "0", "no", "incomplete", "aborted", "partial"):
        return False
    if txt in ("true", "1", "yes", "complete", "completed", "ok"):
        return True
    return None


# JUnit XML 允许的根元素。任何别的东西都不是本工具能解释的结果文件 ——
# 旧实现不校验根标签，`<foo><bar/></foo>` 直接得到 0 条检查 + NO_RESULTS + ok=True
# （「成功但没结果」），而真相是"这不是一份 JUnit 报告"。
_JUNIT_ROOTS = ("testsuites", "testsuite")


def _from_xml(path: Path) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    root = ET.parse(path).getroot()
    if root.tag not in _JUNIT_ROOTS:
        raise ValueError(
            f"not a JUnit XML report: root element is <{root.tag}>, expected one of "
            f"{list(_JUNIT_ROOTS)}. Refusing to report NO_RESULTS for an unrelated XML."
        )
    checks: List[Dict[str, Any]] = []

    # suite / testsuites 级别的失败汇总：JUnit 允许把 setup/teardown 失败或
    # 汇总计数挂在 suite 上而不展开成 testcase。旧实现只遍历 testcase，于是：
    #   * <testsuite><errors><error message="setup failed"/></errors></testsuite>
    #   * <testsuite failures="3">（只有属性没有内联节点）
    # 都会被判 PASS。这里显式读取，任何非零计数/存在 error 节点即记 FAIL。
    # 注意：error/failure 节点可能挂在 <testsuites> 下（suite 之外），
    # 也可能在 suite 内部。两种都要看，否则 `<testsuites><errors><error/></errors>
    # </testsuites>` 仍会被判 PASS（实测）。
    _root_level_err = root.find("errors") is not None or root.find("failure") is not None
    if _root_level_err:
        checks.append({
            "rule_id": "TESTSUITES_ERROR_NODE",
            "category": "junit_suite_integrity",
            "status": "FAIL",
            "detail": "JUnit report has a top-level <errors>/<failure> section: the run "
                      "did not complete cleanly.",
        })
    for suite in root.iter("testsuite"):
        _fails = 0
        for _k in ("failures", "errors"):
            try:
                _fails += int(str(suite.get(_k) or "0").strip() or 0)
            except (TypeError, ValueError):
                _fails += 0
        # `.//error` 只找 suite 子树；root 级的已在上面单独处理。
        _has_err_node = suite.find(".//error") is not None
        if _fails or _has_err_node:
            checks.append({
                "rule_id": f"SUITE_{suite.get('name') or 'unnamed'}",
                "category": "junit_suite_integrity",
                "status": "FAIL",
                "detail": (f"JUnit <testsuite> reports failures={suite.get('failures')}, "
                           f"errors={suite.get('errors')}, "
                           f"error-node={_has_err_node}: the suite did not complete "
                           "cleanly (not evidence of passing)."),
            })
    if root.tag == "testsuites":
        try:
            _rf = int(str(root.get("failures") or "0").strip() or 0)
            _re = int(str(root.get("errors") or "0").strip() or 0)
        except (TypeError, ValueError):
            _rf = _re = 0
        if _rf or _re:
            checks.append({
                "rule_id": "TESTSUITES_TOTAL",
                "category": "junit_suite_integrity",
                "status": "FAIL",
                "detail": f"<testsuites> aggregate reports failures={_rf}, errors={_re}.",
            })

    for tc in root.iter("testcase"):
        status, detail = "PASS", ""
        for child in tc:
            tag = child.tag.lower()
            if tag in ("failure", "error"):
                status = "FAIL"
                detail = (child.get("message") or child.text or "").strip()
                break
            if tag == "skipped":
                status = "SKIP"
                detail = (child.get("message") or "").strip()
        # 既无 name 也无 classname 的 <testcase/> 不携带任何身份信息。旧实现把它
        # 记成 rule_id="UNKNOWN"、status=PASS —— 于是一份**完全没有断言**的
        # XML（只有一个空 testcase 元素）会让 overall_status 变成 PASS，
        # 比"零 testcase"（正确判 NO_RESULTS）还宽松。这是典型的假通过：
        # 没有名字就没有证据，不能算通过。
        name = tc.get("name")
        # `.strip()`：空白 name（`name="   "`）同样是零身份信息，
        # 旧判据 `if not name` 对纯空白是 truthy，会被当成有名字的通过项。
        if not (name or "").strip():
            checks.append({
                "rule_id": "UNNAMED_TESTCASE",
                "category": tc.get("classname") or "cocotb",
                "status": "FAIL",
                "detail": "JUnit <testcase> has no 'name' attribute; it cannot be "
                          "attributed to a specific check, so it is not evidence of "
                          "passing (treated as failure, not silent pass).",
            })
            continue
        checks.append({
            "rule_id": str(name),
            "category": tc.get("classname") or "cocotb",
            "status": status,
            "detail": detail.replace("\n", " ")[:400],
        })
    return checks, None


def _from_json(raw: Any) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[bool]]:
    """Returns (checks, seed, completed).

    ``completed`` 为 None 表示产出方未声明（旧格式/外部生成的 JSON）；
    True/False 表示 cocotb 测试平台是否跑完了全部阶段。
    """
    if isinstance(raw, dict) and isinstance(raw.get("checks"), list):
        raw_checks = raw["checks"]
        # 非 dict 元素会让 c.get(...) 抛 AttributeError，冒泡成
        # "'str' object has no attribute 'get'" —— 用户完全看不出是结果文件
        # 的 checks 数组里混进了字符串/None。模块 docstring 承诺「无法识别的
        # 输入报错而不是静默 NO_RESULTS」，所以这里是可读错误。
        bad = [i for i, c in enumerate(raw_checks) if not isinstance(c, dict)]
        if bad:
            raise ValueError(
                f"results 'checks' must be a list of objects; "
                f"element(s) at index {bad[:5]} are not objects "
                f"(got types {[type(raw_checks[i]).__name__ for i in bad[:5]]})."
            )
        # 同样与 XML 侧口径一致：没有 rule_id 的检查项不能归属，不算通过证据。
        checks = []
        for _c in raw_checks:
            _cid = _c.get("rule_id")
            if not (str(_cid).strip() if _cid is not None else ""):
                checks.append({
                    "rule_id": "UNNAMED_TESTCASE",
                    "category": str(_c.get("category", "functional")),
                    "status": "FAIL",
                    "detail": "results entry has no 'rule_id'; it cannot be attributed "
                              "to a specific check, so it is not evidence of passing "
                              "(treated as failure, not silent pass).",
                })
                continue
            checks.append({
                "rule_id": str(_cid),
                "category": str(_c.get("category", "functional")),
                "status": _norm_status(_c.get("status", "FAIL")),
                "detail": str(_c.get("detail", "")),
            })
        return checks, raw.get("seed"), _norm_completed(raw.get("completed"))

    tests = None
    seed = None
    if isinstance(raw, dict) and isinstance(raw.get("tests"), list):
        tests, seed = raw["tests"], raw.get("seed")
    elif isinstance(raw, list):
        tests = raw
    if tests is not None:
        # 旧实现对非 dict 元素用 `if isinstance(t, dict)` **静默过滤**：
        # {"tests": ["oops", null, 42]} 会变成 0 条检查 -> summary.total=0 ->
        # overall_status=NO_RESULTS 且 ok=True。调用方看到"成功"、零检查，
        # 而真相是**结果文件根本无法解析**。这与模块 docstring 的承诺
        # （"Unrecognized input is reported as an error instead of silently
        # yielding an empty PASS/NO_RESULTS report"）直接矛盾。
        bad_tests = [i for i, t in enumerate(tests) if not isinstance(t, dict)]
        if bad_tests:
            raise ValueError(
                f"results 'tests' must be a list of objects; "
                f"element(s) at index {bad_tests[:5]} are not objects "
                f"(got types {[type(tests[i]).__name__ for i in bad_tests[:5]]})."
            )
        # 与 XML 侧口径一致：没有标识的测试项不是「通过证据」。旧实现用
        # `t.get("name", t.get("rule_id", "UNKNOWN"))` 兜底成 "UNKNOWN"，于是
        # {"tests": [{"status":"passed"}, {"status":"passed"}]} 得到
        # total=2/passed=2/overall_status=PASS —— 两条无从归属的通过项被当成
        # 真实证据。XML 分支（缺陷 119）已改为 FAIL，这里补齐同样语义。
        checks = []
        for _t in tests:
            _nm = _t.get("name", _t.get("rule_id"))
            if not (str(_nm).strip() if _nm is not None else ""):
                checks.append({
                    "rule_id": "UNNAMED_TESTCASE",
                    "category": str(_t.get("category", "cocotb")),
                    "status": "FAIL",
                    "detail": "cocotb results entry has no 'name'/'rule_id'; it cannot "
                              "be attributed to a specific check, so it is not evidence "
                              "of passing (treated as failure, not silent pass).",
                })
                continue
            checks.append({
                "rule_id": str(_nm),
                "category": str(_t.get("category", "cocotb")),
                "status": _norm_status(_t.get("status", _t.get("result", "FAIL"))),
                "detail": str(_t.get("detail", "")),
            })
        # tests 分支同样要透传 completed：旧实现直接 `return checks, seed, None`
        # 把它丢了，于是 {"tests": [...], "completed": false} 完全绕过
        # COCOTB_INCOMPLETE 完整性门禁（实测 overall_status=PASS）。
        _completed_tests = raw.get("completed") if isinstance(raw, dict) else None
        return checks, seed, _norm_completed(_completed_tests)

    raise ValueError(
        "Unrecognized results format: expected cocotb JUnit XML, "
        "a JSON object with 'checks'/'tests', or a JSON list of tests."
    )



_ENGINE_VER_RE = re.compile(r"\bVerilator\s+(\d+\.\d+(?:\.\d+)?)")


def _detect_engine(log_txt: str) -> str:
    """从日志里读出真实仿真器版本；读不到返回空串（绝不编版本号）。"""
    m = _ENGINE_VER_RE.search(log_txt)
    if m:
        return f"Verilator {m.group(1)}"
    if "Verilator" in log_txt:
        return "Verilator"
    return ""


def _parse_verilator_checks(log_txt: str) -> List[Dict[str, Any]]:
    """Parse structured per-transaction [CHECK] lines emitted by Verilator testbenches."""
    out: List[Dict[str, Any]] = []
    for line in log_txt.splitlines():
        if "[CHECK]" not in line:
            continue
        payload = line.split("[CHECK]", 1)[1].strip()
        fields: Dict[str, str] = {}
        for part in payload.split("|"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k.strip()] = v.strip()
        if not fields.get("id"):
            # `[CHECK]` 行没有 id 时旧实现直接 `continue` **静默丢弃**。于是
            # `[CHECK] status=FAIL exp=1 got=2` 这条明确的失败证据凭空消失，
            # 报告里既看不到失败、也没有任何提示。与 XML/JSON 分支的无名项
            # 口径统一：不可归属的检查项算失败，不能算「没出现过」。
            out.append({
                "rule_id": "UNNAMED_TESTCASE",
                "protocol": fields.get("proto", ""),
                "category": fields.get("cat", "verilator_check"),
                "engine": _detect_engine(log_txt),
                "tx": fields.get("tx", ""),
                "expected": fields.get("exp", ""),
                "observed": fields.get("got", ""),
                "status": "FAIL",
                "detail": "Verilator [CHECK] line has no 'id' field; it cannot be "
                          "attributed to a specific check (treated as failure, not "
                          "silently dropped). Raw: " + payload[:200],
            })
            continue
        out.append({
            "rule_id": fields["id"],
            "protocol": fields.get("proto", ""),
            "category": fields.get("cat", "verilator_check"),
            # 不能写死工具版本：日志可能来自任何 Verilator 版本（本仓库 CI 就跨
            # 5.020/5.054，用户环境更是各异）。旧实现把所有 [CHECK] 一律标成
            # "Verilator 5.054"，报告里的版本信息是编的。
            # 从日志本身抓 `Verilator 5.0xx` 字样，抓不到就留空（不猜）。
            "engine": _detect_engine(log_txt),
            "tx": fields.get("tx", ""),
            "expected": fields.get("exp", ""),
            "observed": fields.get("got", ""),
            "status": _norm_status(fields.get("status", "FAIL")),
            "detail": f"{fields.get('proto', '')} | {fields.get('tx', '')} | exp={fields.get('exp', '')} | got={fields.get('got', '')}",
        })
    return out


def tool_normalize_sim_result(
    module_name: str,
    cocotb_results_json: Optional[str] = None,
    sva_log_path: Optional[str] = None,
    verilator_log_path: Optional[str] = None,
    expected_suites: Optional[List[str]] = None,
    coverage_summary: Optional[Dict[str, Any]] = None,
    mutation_summary: Optional[Dict[str, Any]] = None,
    out_report_json: Optional[str] = None,
) -> Dict[str, Any]:
    if not cocotb_results_json and not sva_log_path and not verilator_log_path:
        return err_result("normalize_sim_result", "Provide cocotb_results_json, sva_log_path, and/or verilator_log_path.")

    checks: List[Dict[str, Any]] = []
    seed: Optional[int] = None
    sources: List[str] = []

    if cocotb_results_json:
        p = Path(cocotb_results_json)
        if not p.is_file():
            return err_result("normalize_sim_result", f"Results file not found: {cocotb_results_json}")
        cocotb_completed: Optional[bool] = None
        try:
            if p.suffix.lower() == ".xml":
                parsed, seed = _from_xml(p)
            else:
                parsed, seed, cocotb_completed = _from_json(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, ET.ParseError, json.JSONDecodeError) as exc:
            return err_result("normalize_sim_result", f"Failed to parse '{p}': {exc}")
        checks.extend(parsed)
        # 完整性闸门：cocotb 平台在 Phase 5 结束时写 completed=True。若 JSON 里
        # 明确 completed=False，说明测试没跑完（异常/超时/await 卡死）——此时
        # 残留的「前几条全 PASS」绝不能当通过。旧实现没有这个信号，残缺结果
        # 与完整结果都判 PASS。
        if _norm_completed(cocotb_completed) is False:
            checks.append({
                "rule_id": "COCOTB_INCOMPLETE",
                "category": "cocotb_run_integrity",
                "status": "FAIL",
                "detail": "cocotb report has completed=false: test aborted before all phases finished "
                          f"({len(parsed)} check(s) recorded). Treat as failure, not partial pass.",
            })
        sources.append(str(p))

    if sva_log_path:
        p = Path(sva_log_path)
        if not p.is_file():
            return err_result("normalize_sim_result", f"SVA log not found: {sva_log_path}")
        sva_violations = 0
        sva_heartbeats = 0
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = _SVA_ERR_RE.search(line)
            if m:
                checks.append({"rule_id": m.group(1), "category": "sva_protocol", "status": "FAIL", "detail": m.group(2).strip()})
                sva_violations += 1
                continue
            if _SVA_HBEAT_RE.search(line):
                sva_heartbeats += 1
        # 有心跳、无违例 => 断言引擎在场且全部通过。这是一条**正向**结果，
        # 不能与「零输出 = NO_RESULTS」混为一谈（后者可能是 bind 失败/没跑）。
        if sva_heartbeats and sva_violations == 0:
            checks.append({
                "rule_id": "SVA_P000",
                "category": "sva_protocol",
                "status": "PASS",
                "detail": f"assertion checker active ({sva_heartbeats} heartbeat(s)), no [RULE:*] violations",
            })
        sources.append(str(p))

    if verilator_log_path:
        p = Path(verilator_log_path)
        if not p.is_file():
            return err_result("normalize_sim_result", f"Verilator log not found: {verilator_log_path}")
        log_txt = p.read_text(encoding="utf-8", errors="ignore")
        # Verilator 的**编译/elaboration 错误**在日志里是 `%Error: ...` 行。
        # 旧实现完全不看它：一份「%Error: syntax error + PASS: ...」的混合日志
        # 被归一成 overall_status=NO_RESULTS、all_verified=true、零诊断 ——
        # 一个编译都没过的仿真被报告成「无结果」（既不失败也不提示）。
        # 这里把 %Error 行转成 FAIL 检查，让错误在报告里可见。
        _verr_lines = [
            ln.strip() for ln in log_txt.splitlines()
            if ln.lstrip().startswith("%Error")
        ]
        if _verr_lines:
            checks.append({
                "rule_id": "VERILATOR_ERROR",
                "category": "verilator_log",
                "status": "FAIL",
                "detail": (f"Verilator reported {len(_verr_lines)} %Error line(s); first: "
                           + _verr_lines[0][:200]),
            })
        ident = verify_suite_identities(log_txt, expected_suites or [])
        parsed_vchecks = _parse_verilator_checks(log_txt)
        if parsed_vchecks:
            checks.extend(parsed_vchecks)
            for sname, st in ident["observed"].items():
                if st != "PASS":
                    checks.append({
                        "rule_id": f"SUITE_{sname}",
                        "category": "verilator_suite",
                        "status": "FAIL",
                        "detail": f"suite={sname} status={st}",
                    })
        else:
            for sname, st in ident["observed"].items():
                checks.append({
                    "rule_id": f"SUITE_{sname}",
                    "category": "verilator_suite",
                    "status": "PASS" if st == "PASS" else "FAIL",
                    "detail": f"suite={sname} status={st}",
                })
        for miss in ident["missing"]:
            checks.append({
                "rule_id": f"SUITE_{miss}",
                "category": "verilator_suite",
                "status": "FAIL",
                "detail": f"Expected suite '{miss}' missing from log (silent skip guard).",
            })
        # 完整性闸门：每个**启动过**的套件（`== sim: <name> ==` 头）都必须有
        # 对应的 `PASS:` / `FAIL:` 结束汇总行。生成的 TB 一定成对打印——
        # 缺这一行说明仿真在 CHECK 之后、$finish 之前崩溃。旧实现只看已有的
        # CHECK 行，于是「跑到一半崩了」与「完整跑完」可能都判 PASS。
        # 汇总行文本不保证以套件名开头：真实 sim.sh 打印
        # `PASS: apb all 8 checks passed`（首词是套件名），但测试夹具/其它 TB
        # 可能打印 `PASS: all 20 checks passed`（首词是 "all"）。因此不能按
        # 「汇总行首词 == 套件名」逐一套，那会误伤合法日志。
        # 采用计数法：**启动了几个套件，就必须至少有几条汇总行**——
        # 生成/运行一个套件必然在 $finish 前打印恰好一条。数量不足即说明
        # 有套件在中途崩溃。
        # 逐套件核对「有没有自己的结束汇总行」，而不是比对**总数**。
        # 旧实现用 len(汇总行) < len(启动套件) 判断，会被重复汇总行绕过：
        # 实测 A 打印两条 `PASS:`、B 崩溃一条都没有 → 总数 2 >= 2，B 被放行，
        # overall_status 仍是 PASS。verify_suite_identities 现已按套件边界
        # 归集 summaries，这里直接用它。
        _started = list(ident["observed"].keys())
        _summaries = ident.get("summaries") or {}
        _no_summary = [n for n in _started if n not in _summaries]
        if _no_summary:
            checks.append({
                "rule_id": "VERILATOR_NO_SUMMARY",
                "category": "verilator_run_integrity",
                "status": "FAIL",
                "detail": f"suite(s) {_no_summary} started but never printed their own "
                          "'PASS:'/'FAIL:' summary line before the log ended "
                          f"(started={_started}, summaries={_summaries}). They aborted "
                          "before $finish. Treat as failure, not partial pass.",
            })
        sources.append(str(p))

    passed = sum(1 for c in checks if c["status"] == "PASS")
    failed = sum(1 for c in checks if c["status"] == "FAIL")
    skipped = sum(1 for c in checks if c["status"] == "SKIP")
    overall = "PASS" if (passed > 0 and failed == 0) else ("FAIL" if failed > 0 else "NO_RESULTS")
    report: Dict[str, Any] = {
        "schema_version": "1.1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "module": module_name,
        "seed": seed,
        "overall_status": overall,
        "summary": {
            "total": len(checks),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": round(passed / len(checks), 4) if checks else 0.0,
        },
        "sources": sources,
        "failed_rules": [c for c in checks if c["status"] == "FAIL"],
        "checks": checks,
    }
    # 套件身份核对结构化输出：调用方（run_sim 套件核对 / MCP 客户端）需要读到
    # all_verified/missing/failed/observed，而非只能从 SUITE_* 规则反推。
    if verilator_log_path:
        report["verilator_suite_identity"] = ident
    if coverage_summary is not None:
        report["coverage_summary"] = coverage_summary
    if mutation_summary is not None:
        report["mutation_summary"] = mutation_summary

    if out_report_json:
        out = Path(out_report_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return ok_result("normalize_sim_result", report)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("normalize_sim_result"))
