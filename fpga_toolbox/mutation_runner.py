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
    for idx, m in enumerate(items):
        if not isinstance(m, dict):
            continue
        out.append({
            "id": str(m.get("id", f"M{idx + 1:02d}")),
            "file": str(m["file"]),
            "old": str(m["old"]),
            "new": str(m["new"]),
            "desc": str(m.get("desc", "")),
        })
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
        if m["old"] not in txt and m["new"] in txt:
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
        finally:
            fp.write_bytes(original_bytes)

    total = len(mutations)
    score = round((killed / total) * 100.0, 2) if total else 0.0
    data = {
        "workdir": str(wd.resolve()),
        "total": total,
        "killed": killed,
        "survived": total - killed,
        "mutation_score": score,
        "min_score": min_score,
        "results": results,
    }
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
