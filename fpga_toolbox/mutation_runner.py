"""Tool 11: Parameterized RTL mutation runner with try/finally restore and residual-mutation guard.

Ported from agent/verilog/axi/mutate.py:
  * Refuses to run if a previous un-restored mutation is detected (residual guard, E030)
  * Always restores original RTL in try/finally even on crashes or timeouts
  * Quantifies mutation score = killed / total
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result


def load_mutations(spec_path: Path) -> List[Dict[str, str]]:
    # 变异 spec 是用户手写的（YAML/JSON），注释里可能带 GBK/Shift-JIS/latin-1
    # 字节（实测 latin-1 注释 0xE9 让 utf-8 解码抛 UnicodeDecodeError）。旧实现
    # 让它冒泡，外层包成 "Invalid mutation spec: 'utf-8' codec can't decode ..."
    # —— 用户会以为是 spec **格式**错，真正的错因是**编码**，方向完全误导。
    # errors="replace" 让注释乱码不影响 id/file/old/new 结构解析。
    text = spec_path.read_text(encoding="utf-8", errors="replace")
    raw = json.loads(text) if spec_path.suffix.lower() == ".json" else yaml.safe_load(text)
    items = raw.get("mutations", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("Mutation spec must be a list or mapping with 'mutations' list.")
    out: List[Dict[str, str]] = []
    _bad_elems: List[str] = []
    for idx, m in enumerate(items):
        if not isinstance(m, dict):
            # 旧实现 `continue` 静默丢弃非对象元素：spec 里混进一个字符串
            # （`mutations: ["oops", {...}]`）时，那条变异凭空消失，total 只剩
            # 后面的有效项，工具报 ok=true。用户会以为所有变异都跑了。
            _bad_elems.append(f"[{idx}]={type(m).__name__}")
            continue
        out.append({
            "id": str(m.get("id", f"M{idx + 1:02d}")),
            "file": str(m["file"]),
            "old": str(m["old"]),
            "new": str(m["new"]),
            "desc": str(m.get("desc", "")),
        })
    if _bad_elems:
        raise ValueError(
            "Mutation spec entries must be objects with file/old/new; got non-object "
            f"element(s) at {_bad_elems[:8]}. Every entry must be a mapping so no "
            "mutation is silently skipped."
        )
    return out


def check_residual_mutations(workdir: Path, mutations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Detect whether any target file already has `new` without `old` (unrestored mutation)."""
    residuals: List[Dict[str, Any]] = []
    for m in mutations:
        # 与变异主循环同一套沙箱约束：越界目标不参与残留检查（既不该读外部文件，
        # 也避免外部文件恰好匹配 old/new 而误报残留）。越界会在主循环里被拒。
        fp = _resolve_mutation_target(workdir, m["file"])
        if fp is None or not fp.is_file():
            continue
        txt = fp.read_text(encoding="utf-8", errors="replace")
        _old, _new = m["old"], m["new"]

        # ---- 删除型变异（new == ""）：不能用 `new in txt` 判定 ----
        # `"" in txt` 恒为真，于是任何"old 已不存在"的文件都会被判成残留
        # （实测：已变异的 `assign y =;` 对 spec old=' a & b' new='' 必然报
        # E030）。删除型变异的残留信号只能是「old 已消失」本身 —— 但这也正是
        # 合法变异后的状态，无法与「未恢复」区分。因此删除型变异**不参与**
        # 残留检查，改为在主循环里保证 try/finally 还原（by design）。
        if _new == "":
            continue

        # ---- 重复模式：不能只比较"old 是否完全不存在" ----
        # 变异主循环用 replace(..., 1) 只替换**第一处**。若 old 在同一文件出现
        # 多次、第一处已变异而后面仍是 old，则 `old in txt` 为真 → 旧判据认为
        # 没有残留，**漏报**真实未恢复的变异（实测）。
        # 正确信号：new 的出现次数 > old 的出现次数变化。这里用「new 存在且
        # old 的剩余次数 < 原始应有次数」判定，但原始次数无从得知 —— 退一步用
        # 更保守的可靠信号：**两者并存**（old 还在 且 new 已出现）说明文件中
        # 至少有一处已被改写、同时至少还有一处未改写，必是混合态。
        _has_old = _old in txt
        _has_new = _new in txt
        # 三种都需要覆盖：
        #   a) old 完全消失、new 出现       -> 完整未恢复（旧判据已能抓到）
        #   b) old 与 new **并存**          -> 混合态（部分改写）。只要两者都在，
        #      就说明"至少改写了一次、且至少还剩一处未改写"。这正是
        #      `replace(...,1)` + 多处匹配才会产生的状态，必然是残留。
        #   c) new 出现次数 > old 次数      -> 兜底（new 在文本里已多处出现）
        if _has_new and (_has_old or txt.count(_new) > txt.count(_old)):
            residuals.append({
                "severity": "ERROR",
                "code": "E030",
                "path": m["file"],
                "message": f"Residual unrestored mutation detected for {m['id']} ({m['desc']}).",
            })
    return residuals


def _resolve_mutation_target(wd: Path, file_str: str) -> Optional[Path]:
    """把 spec 里的 ``file`` 解析成沙箱内的绝对路径；越界返回 None。

    背景（真实踩坑）：mutation spec 由用户 / AI 手写，``file`` 可能写成绝对路径
    或 ``../xxx`` 相对路径，也可能是沙箱内指向外部的符号链接。旧实现直接
    ``wd / m["file"]`` 就 ``read_bytes`` / ``write_bytes``，于是变异测试会去
    **改写工作目录之外的文件**——finally 的还原只在「正常走到」时才生效，任何
    一次 crash / kill -9 / 断电落在 write 与 restore 之间，外部文件就被永久
    破坏。这与 copy_sandbox 的「沙箱不得越界」是同一类防护：变异目标必须落在
    workdir 之内。
    """
    rel = Path(file_str)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    target = (wd / rel).resolve()
    try:
        target.relative_to(wd.resolve())
    except ValueError:
        # 沙箱内符号链接指向外部 —— 同样拒绝
        return None
    return target


def tool_run_mutation(
    workdir: str,
    mutation_spec: str,
    test_cmd: List[str],
    min_score: float = 80.0,
    timeout_sec: int = 120,
    only_id: Optional[str] = None,
) -> Dict[str, Any]:
    # 同 run_sim：MCP / CLI 可能把数字序列化成字符串，边界处显式转换，
    # 避免 subprocess.run(timeout=str) 与 score < min_score 抛 TypeError。
    try:
        min_score = float(min_score)
    except (TypeError, ValueError):
        return err_result("run_mutation", f"min_score must be a number, got {min_score!r}")
    try:
        timeout_sec = int(timeout_sec)
    except (TypeError, ValueError):
        return err_result("run_mutation", f"timeout_sec must be an integer, got {timeout_sec!r}")

    wd = Path(workdir)
    sp = Path(mutation_spec)
    if not wd.is_dir():
        return err_result("run_mutation", f"Working directory not found: {workdir}")
    if not sp.is_file():
        return err_result("run_mutation", f"Mutation spec not found: {mutation_spec}")
    if not test_cmd:
        return err_result("run_mutation", "test_cmd must be a non-empty command list.")

    try:
        mutations = load_mutations(sp)
    except Exception as exc:
        return err_result("run_mutation", f"Invalid mutation spec: {exc}")

    if only_id:
        mutations = [m for m in mutations if m["id"] == only_id]
        if not mutations:
            return err_result("run_mutation", f"Mutation id '{only_id}' not found.")

    residuals = check_residual_mutations(wd, mutations)
    if residuals:
        return err_result(
            "run_mutation",
            f"Refusing to run: {len(residuals)} residual unrestored mutation(s) detected (exit-4 guard).",
            diagnostics=residuals,
        )

    # ---- baseline 预检（变异前先跑一次未变异的测试命令）----
    # 背景（真实踩坑）：旧实现只看「变异后 test_cmd 是否非零」就判 KILLED。若
    # test_cmd 本身是坏的（写了错的名字、缺二进制、或测试台**本来就失败**），
    # 那么每个变异都以非零退出 → 全部判 KILLED → 报 **100% 变异得分**。
    # 这是最严重的静默假通过：分数满格、结论「测试台能杀掉所有故障」，而实际
    # 一个故障都没验。变异得分的定义前提是「未变异版本必须通过」，所以必须在
    # 动任何源码之前先把这个前提验掉，不成立就 fail-fast（一行源码都不改）。
    baseline_detail = ""
    try:
        baseline = subprocess.run(
            test_cmd, cwd=str(wd), capture_output=True, text=True,
            timeout=timeout_sec, check=False,
        )
    except FileNotFoundError as exc:
        # 旧实现的 subprocess.run 只 catch TimeoutExpired，命令不存在会让
        # FileNotFoundError 直接冒泡成裸堆栈（CLI 下就是 traceback），
        # 用户看到 Python 异常而不是「test_cmd 的第一个词不是可执行文件」。
        return err_result(
            "run_mutation",
            f"test_cmd is not executable: {exc}. The first element must be a "
            "command on PATH (or an absolute path). No RTL was modified.",
            data={"baseline": {"ran": False, "error": str(exc)}},
        )
    except subprocess.TimeoutExpired:
        return err_result(
            "run_mutation",
            f"Baseline run of test_cmd exceeded the {timeout_sec}s timeout. "
            "Raise timeout_sec, or the test command is hanging. No RTL was modified.",
            data={"baseline": {"ran": True, "timed_out": True}},
        )
    baseline_detail = ((baseline.stdout or "") + "\n" + (baseline.stderr or ""))[-2000:]
    if baseline.returncode != 0:
        return err_result(
            "run_mutation",
            f"Baseline (unmutated) test run already fails with exit code "
            f"{baseline.returncode}. A passing baseline is required: with a "
            "broken or already-failing test_cmd every mutation exits non-zero and "
            "would be counted as KILLED, reporting a false 100% mutation score. "
            "No RTL was modified. Fix test_cmd first.",
            data={
                "baseline": {
                    "ran": True, "returncode": baseline.returncode,
                    "stderr_tail": (baseline.stderr or "")[-1000:],
                    "stdout_tail": (baseline.stdout or "")[-1000:],
                }
            },
            diagnostics=[{
                "severity": "ERROR", "code": "E031", "path": "test_cmd",
                "message": f"Baseline test run failed (rc={baseline.returncode}); "
                           "mutation score would be meaningless.",
            }],
        )

    results: List[Dict[str, Any]] = []
    killed = 0
    wd_resolved = wd.resolve()
    for m in mutations:
        # 越界防护：拒绝绝对路径 / ".." / 指向外部的符号链接（见 _resolve_mutation_target）
        fp = _resolve_mutation_target(wd, m["file"])
        if fp is None:
            results.append({
                "id": m["id"], "status": "ERROR",
                "detail": (f"Refusing to mutate '{m['file']}': mutation target must be a "
                           f"relative path inside the working directory ({wd_resolved})."),
            })
            continue
        if not fp.is_file():
            results.append({"id": m["id"], "status": "ERROR", "detail": f"Missing file {m['file']}"})
            continue
        # 变异目标按**字节**读写，而不是 text。
        # 原因（实测）：RTL 可能是 GBK/Shift-JIS/latin-1 编码（注释里 0xE9 等字节）。
        # 若用 read_text(utf-8)：非 UTF-8 文件直接抛 UnicodeDecodeError 冒泡。
        # 若退而用 errors="replace"：非法字节变 U+FFFD，而下面 finally 的
        # write_text(original, utf-8) 会把**含 U+FFFD 的文本写回**——实测 latin-1
        # RTL 的 2 个 0xe9 全部丢失、字节不再一致，RTL 被 try/finally 强还原
        # **永久破坏**（这正是变异测试最不能接受的：测完把被测代码改坏了）。
        # 按字节读写则 old/new 编成 UTF-8 字节去匹配，非 ASCII 字节原样保留。
        original_bytes = fp.read_bytes()
        old_b = m["old"].encode("utf-8")
        new_b = m["new"].encode("utf-8")
        # 空 old 不是"匹配到所有位置"，而是 spec 本身写错了：b"" in bytes 恒真，
        # replace(b"", new, 1) 把 new 插到文件**开头**（实测），变异于是"成功"，
        # 看起来像一次合法变异。这种变异没有任何语义，却会进入分母影响得分。
        # 明确拒绝，而不是静默按"插入到开头"执行。
        if not old_b:
            results.append({
                "id": m["id"], "status": "ERROR",
                "detail": "Mutation 'old' is empty: an empty search pattern matches "
                          "anywhere and would insert 'new' at offset 0, which is not "
                          "a meaningful mutation. Give the exact source text to replace.",
            })
            continue
        if old_b not in original_bytes:
            results.append({"id": m["id"], "status": "SKIP_UNMATCHED", "detail": "Pattern 'old' not found."})
            continue
        mutated_bytes = original_bytes.replace(old_b, new_b, 1)
        try:
            fp.write_bytes(mutated_bytes)
            proc = subprocess.run(test_cmd, cwd=str(wd), capture_output=True, text=True, timeout=timeout_sec, check=False)
            if proc.returncode != 0:
                killed += 1
                results.append({"id": m["id"], "status": "KILLED", "returncode": proc.returncode, "desc": m["desc"]})
            else:
                results.append({"id": m["id"], "status": "SURVIVED", "returncode": 0, "desc": m["desc"]})
        except subprocess.TimeoutExpired:
            killed += 1
            results.append({"id": m["id"], "status": "KILLED_TIMEOUT", "desc": m["desc"]})
        except OSError as exc:
            # 命令在 baseline 时还在、到某个变异时消失（被删/权限变了）——
            # 这不是 KILLED（没测到任何东西），标 ERROR 并排除出分母。
            results.append({"id": m["id"], "status": "ERROR",
                            "detail": f"test_cmd could not be executed: {exc}", "desc": m["desc"]})
        finally:
            fp.write_bytes(original_bytes)

    total = len(mutations)
    # ERROR / SKIP_UNMATCHED 不是「存活」——它们根本没被测到。旧实现用
    # survived = total - killed 把它们算成 survived 并留在分母里：
    # 一个文件写错就凭空多一个「存活变异」，让本来 100% 的分数掉到 50%，
    # 而报告里 survived 的语义（「测试台没杀掉的故障」）被污染。
    errors = sum(1 for r in results if r["status"] == "ERROR")
    skipped = sum(1 for r in results if r["status"] == "SKIP_UNMATCHED")
    evaluated = total - errors - skipped
    survived = evaluated - killed
    score = round((killed / evaluated) * 100.0, 2) if evaluated else 0.0
    data = {
        "workdir": str(wd.resolve()),
        "total": total,
        "evaluated": evaluated,
        "killed": killed,
        "survived": survived,
        "errors": errors,
        "skipped": skipped,
        "mutation_score": score,
        "min_score": min_score,
        "baseline": {"ran": True, "returncode": baseline.returncode,
                     "output_tail": baseline_detail},
        "results": results,
    }
    # 一个变异都没评估到（全 ERROR / 全 SKIP）时不能说「0% 得分」了事：
    # 分母是 0，任何 min_score 比较都无意义，必须显式失败。
    if evaluated == 0:
        return err_result(
            "run_mutation",
            f"No mutation could be evaluated ({errors} error(s), {skipped} unmatched "
            f"out of {total}). The mutation score is undefined; check that spec "
            "'file'/'old' patterns point at existing in-sandbox files.",
            data=data,
        )
    if score < min_score:
        # 分数低于门槛时仍带 data：调用方需要看到逐个变异的 killed/survived
        # 结果（哪些变异存活 = 测试盲区），否则只能看到一句「50% < 100%」。
        return err_result(
            "run_mutation",
            f"Mutation score {score}% is below threshold {min_score}%.",
            data=data,
            diagnostics=[{"severity": "ERROR", "code": "E031", "path": "mutation_score", "message": f"{killed}/{total} killed ({score}% < {min_score}%)."}],
        )
    return ok_result("run_mutation", data)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("run_mutation"))
