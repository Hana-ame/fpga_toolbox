"""运行单个原子工具：`python tools.py <tool> [--参数 值 ...]`

放在仓库根目录，所以 `sys.path[0]` 天然就是仓库根，不需要任何路径或包名处理。
参数由各工具的 inputSchema 自动生成，复杂对象支持 `@file.json`。
"""

from __future__ import annotations

import sys

from fpga_toolbox.cli import run_single_tool
from fpga_toolbox.registry import TOOL_REGISTRY


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print("usage: python tools.py <tool> [--param value ...]")
        print("tools: " + ", ".join(sorted(TOOL_REGISTRY)))
        return 0 if args else 2
    if args[0] not in TOOL_REGISTRY:
        print(f"unknown tool: {args[0]}", file=sys.stderr)
        print("available: " + ", ".join(sorted(TOOL_REGISTRY)), file=sys.stderr)
        return 2
    return run_single_tool(args[0], args[1:])


if __name__ == "__main__":
    sys.exit(main())
