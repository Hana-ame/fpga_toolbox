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


def _from_xml(path: Path) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    root = ET.parse(path).getroot()
    checks: List[Dict[str, Any]] = []
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
        if not name:
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
        checks = [{
            "rule_id": str(c.get("rule_id", "UNKNOWN")),
            "category": str(c.get("category", "functional")),
            "status": _norm_status(c.get("status", "FAIL")),
            "detail": str(c.get("detail", "")),
        } for c in raw_checks]
        return checks, raw.get("seed"), raw.get("completed")

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
        checks = [{
            "rule_id": str(t.get("name", t.get("rule_id", "UNKNOWN"))),
            "category": str(t.get("category", "cocotb")),
            "status": _norm_status(t.get("status", t.get("result", "FAIL"))),
            "detail": str(t.get("detail", "")),
        } for t in tests]
        return checks, seed, None

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
        if cocotb_completed is False:
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
