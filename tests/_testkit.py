"""共享测试辅助。

`_available_backends` 原先定义在 test_full_coverage.py 末尾，拆分后 7 个领域
文件都要用，抽到这里。文件名不以 test_ 开头 —— 门禁 Layer 4 通过
`glob("tests/test_*.py")` 收集套件，辅助模块不应被当作套件执行。
"""

from __future__ import annotations


def _available_backends() -> list:
    """Return the parser backends usable in this environment.

    Tests that compare builtin vs slang must skip slang when it is not installed:
    `parse_sv_file(..., backend="slang")` raises a RuntimeError in a zero-EDA
    environment (that is the documented contract), so iterating
    ("builtin", "slang") unconditionally made the whole suite hard-fail on any
    machine without slang - which is exactly the environment README promises to
    support for the control-plane tools.
    """
    import shutil as _shutil
    backends = ["builtin"]
    if _shutil.which("slang"):
        backends.append("slang")
    return backends
