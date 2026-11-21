"""CLI entry point for subprocess / shell Harness invocations."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .registry import TOOL_REGISTRY, call_tool, list_tool_schemas


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="fpga_toolbox CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("schemas")
    pc = sub.add_parser("call")
    pc.add_argument("tool_name")
    pc.add_argument("--args", default="{}")
    args = p.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(sorted(TOOL_REGISTRY), indent=2))
        return 0
    if args.cmd == "schemas":
        print(json.dumps(list_tool_schemas(), indent=2, ensure_ascii=False))
        return 0
    res = call_tool(args.tool_name, json.loads(args.args))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
