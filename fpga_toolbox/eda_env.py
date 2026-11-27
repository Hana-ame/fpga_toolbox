"""EDA environment probe (Verilator >= 5.040, slang, stdbuf) ported from agent/verilog/vlt_env.sh."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

MIN_VERILATOR_VERSION: Tuple[int, int] = (5, 40)


def parse_verilator_version(text: str) -> Optional[Tuple[int, int]]:
    m = re.search(r"Verilator\s+(\d+)\.(\d+)", text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def find_verilator() -> Optional[str]:
    candidates = []
    if os.environ.get("VERILATOR"):
        candidates.append(os.environ["VERILATOR"])
    if os.environ.get("VERILATOR_ROOT"):
        candidates.append(str(Path(os.environ["VERILATOR_ROOT"]) / "bin" / "verilator"))
    home = Path.home()
    candidates.append(str(home / ".local" / "verilator" / "bin" / "verilator"))
    # 顺序很重要：`/tmp/verilator/bin/verilator` 只作为**最后兜底**，且必须通过
    # 归属校验。`/tmp` 通常是 1777（任何用户可创建/替换），在多用户 Linux/EDA
    # 服务器上，把 /tmp 里的可执行文件排在 PATH 之前 = 允许他人劫持本工具实际
    # 调用的仿真器（旧实现的顺序正是 /tmp 先于 shutil.which，实测构成劫持面）。
    which_v = shutil.which("verilator")
    if which_v:
        candidates.append(which_v)
    tmp_v = "/tmp/verilator/bin/verilator"
    if _is_owned_by_me(tmp_v):
        candidates.append(tmp_v)
    for c in candidates:
        p = Path(c)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _is_owned_by_me(path_str: str) -> bool:
    """判断 path_str 是否指向一个「由当前用户拥有、且不在他人可替换位置」的可执行文件。

    /tmp 是 1777：攻击者可以预先创建 /tmp/verilator 目录与其中的横幅二进制。
    对 /tmp 下的候选额外要求：
      * 解析符号链接后仍在 /tmp 下（避免指向他人的可执行文件）；
      * 目标是**当前用户**拥有（root 装的系统 verilator 走 PATH 更合适）。
    非 /tmp 路径不做额外限制（保持原有 VERILATOR / VERILATOR_ROOT / ~/.local
    的优先级语义）。仅当系统支持 os.getuid 时检查（Windows 无此 API）。
    """
    try:
        p = Path(path_str)
        if not p.exists():
            return False
        real = p.resolve()
        if "/tmp" not in real.parts[:3] and not str(real).startswith("/tmp/"):
            return True
        getuid = getattr(os, "getuid", None)
        if getuid is None:
            return True
        return real.stat().st_uid == getuid()
    except OSError:
        return False


def probe_eda_env() -> Dict[str, Any]:
    """Detect Verilator (>= 5.040), slang, and stdbuf without failing silently."""
    vbin = find_verilator()
    v_ver: Optional[Tuple[int, int]] = None
    v_ok = False
    v_msg = "verilator not found"
    if vbin:
        try:
            proc = subprocess.run(
                [vbin, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5, check=False)
            v_ver = parse_verilator_version(proc.stdout + proc.stderr)
            if v_ver is None:
                v_msg = f"could not parse verilator version from '{vbin}'"
            elif v_ver < MIN_VERILATOR_VERSION:
                v_msg = (
                    f"Verilator {v_ver[0]}.{v_ver[1]:03d} < {MIN_VERILATOR_VERSION[0]}.{MIN_VERILATOR_VERSION[1]:03d} "
                    "(older versions suffer from cross-file virtual interface silent hangs)"
                )
            else:
                v_ok = True
                v_msg = f"Verilator {v_ver[0]}.{v_ver[1]:03d}"
        except Exception as exc:
            v_msg = f"verilator probe failed: {exc}"

    slang_bin = shutil.which("slang")
    stdbuf_bin = shutil.which("stdbuf")
    return {
        "verilator": {
            "path": vbin,
            "version": f"{v_ver[0]}.{v_ver[1]:03d}" if v_ver else None,
            "meets_min_version": v_ok,
            "message": v_msg,
        },
        "slang": {"path": slang_bin, "available": bool(slang_bin)},
        "stdbuf": {"path": stdbuf_bin, "available": bool(stdbuf_bin)},
    }
