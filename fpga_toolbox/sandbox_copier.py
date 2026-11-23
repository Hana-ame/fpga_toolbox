"""Tool 3: Copy resolved RTL/header files into an isolated sandbox with SHA-256 checksums.

Safety contract:
  * The sandbox may never be the project root, nor an ancestor of it.
  * Cleaning is only performed on a directory carrying the sandbox marker,
    unless the caller explicitly passes ``force=True``.
  * Relative directory layout is preserved, so ``rtl/a/common.sv`` and
    ``rtl/b/common.sv`` stay distinct and ``include`` paths keep working.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result

MARKER_NAME = ".fpga_sandbox"

_INC_LOCAL_RE = re.compile(r'`include\s+"([^"]+)"')


def _missing_included_files(dst_root: Path, copied: List[Dict[str, Any]], root: Path) -> List[str]:
    """检测沙箱内文件的 `include "xxx"` 指向的本地文件是否未进沙箱。

    背景（真实踩坑）：copy_sandbox 按调用方给的 relative_files 复制，但调用方
    （AI agent）常只列顶层文件而漏掉被 `include 的子模块 —— 沙箱静默缺依赖，
    里面的 RTL 无法编译。这里主动扫描沙箱内文件的 `include，指向的本地文件
    存在但不在沙箱时给出告警，把「静默产生不可编译的沙箱」变成「主动提醒」。
    纯信息性（不改 ok 状态）：调用方可能有意只复制部分文件。
    """
    missing = []
    staged = {c["staged_rel"].replace("\\", "/") for c in copied}
    for c in copied:
        dst = Path(c["staged_path"])
        try:
            txt = dst.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _INC_LOCAL_RE.finditer(txt):
            inc = m.group(1)
            # 只关心带 .v/.sv/.vh/.svh 后缀的本地 include（跳过 <> 形式）
            if not inc.endswith((".v", ".sv", ".vh", ".svh")):
                continue
            # 已在沙箱内（按相对布局或同目录）
            if inc in staged:
                continue
            # 项目里是否存在这个被 include 的文件（按 staged 文件的相对布局找）
            proj_candidate = (root / c["source_rel"]).parent / inc
            if proj_candidate.exists() and inc not in staged:
                missing.append(
                    f"{c['staged_rel']} includes {inc} — exists in project but not staged "
                    "(sandbox RTL may not compile)")
    return missing



def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _clear_dir(target: Path) -> None:
    for entry in target.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def tool_copy_sandbox(
    project_root: str,
    relative_files: List[str],
    sandbox_dir: str,
    clean: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    root = Path(project_root).resolve()
    dst_root = Path(sandbox_dir).resolve()

    if not root.is_dir():
        return err_result("copy_sandbox", f"Project root not found: {root}")
    if not relative_files:
        return err_result("copy_sandbox", "relative_files is empty; nothing to stage.")

    if dst_root == root:
        return err_result("copy_sandbox", f"Refusing to use the project root as sandbox: {dst_root}")
    if _is_within(root, dst_root):
        return err_result(
            "copy_sandbox",
            f"Refusing to stage into '{dst_root}': it is an ancestor of the project root '{root}'.",
        )
    if dst_root.parent == dst_root:
        return err_result("copy_sandbox", f"Refusing to use a filesystem root as sandbox: {dst_root}")
    if dst_root == Path.home().resolve():
        return err_result("copy_sandbox", f"Refusing to use the home directory as sandbox: {dst_root}")

    if dst_root.exists():
        if not dst_root.is_dir():
            return err_result("copy_sandbox", f"Sandbox path exists but is not a directory: {dst_root}")
        if clean and any(dst_root.iterdir()):
            marked = (dst_root / MARKER_NAME).is_file()
            if not marked and not force:
                return err_result(
                    "copy_sandbox",
                    f"Refusing to clean '{dst_root}': it is not empty and carries no {MARKER_NAME} marker. "
                    "Pass force=True if you really intend to wipe it.",
                )
            _clear_dir(dst_root)

    dst_root.mkdir(parents=True, exist_ok=True)
    (dst_root / MARKER_NAME).write_text(
        "Created by fpga_toolbox.copy_sandbox. Safe to clean.\n", encoding="utf-8"
    )

    copied: List[Dict[str, Any]] = []
    seen = set()
    for rel in relative_files:
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            return err_result("copy_sandbox", f"Rejected non-portable relative path: {rel}")
        src = root / rel_path
        if not src.is_file():
            return err_result("copy_sandbox", f"Source file missing: {src}")

        # 符号链接逃逸防护：上面只校验了相对路径字符串不含 ".."，但 ``src`` 可能
        # 是项目内的符号链接（文件或目录），``is_file()`` / ``shutil.copy2`` 都会
        # 跟随它读到项目根之外的内容，把任意文件「搬」进沙箱并算进 sha256。
        # 这里解析真实路径，确认它仍落在 root 之内；项目内部的相对符号链接
        # （如 x/alias.sv -> ../y/real.sv）解析后仍在 root 内，照常放行。
        try:
            real_src = src.resolve(strict=True)
        except OSError:
            return err_result("copy_sandbox", f"Source file missing: {src}")
        if not _is_within(real_src, root):
            return err_result(
                "copy_sandbox",
                f"Refusing to stage '{rel}': it resolves outside the project root "
                f"('{real_src}' is not within '{root}').",
            )

        key = str(rel_path).replace("\\", "/")
        if key in seen:
            continue
        seen.add(key)

        dst = dst_root / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append({
            "source_rel": key,
            "staged_rel": str(dst.relative_to(dst_root)).replace("\\", "/"),
            "staged_path": str(dst.resolve()),
            "filename": dst.name,
            "sha256_prefix": hashlib.sha256(dst.read_bytes()).hexdigest()[:16],
        })

    return ok_result("copy_sandbox", {
        "sandbox_dir": str(dst_root.resolve()),
        "source_project_untouched": True,
        "layout_preserved": True,
        "file_count": len(copied),
        "files": copied,
        # include 完整性检查：沙箱内文件若 `include "xxx"` 指向存在但未复制
        # 的本地文件，沙箱将无法编译。主动报告，避免静默缺依赖。
        "include_warnings": _missing_included_files(dst_root, copied, root),
    })


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("copy_sandbox"))
