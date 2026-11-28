"""零外部依赖的 MCP (Model Context Protocol) stdio server。

MCP 是 Agent 客户端（Claude Desktop / IDE 插件 / 自研 agent）连接工具后端的
标准协议。它跑在 **stdio** 上，用 newline-delimited JSON-RPC 2.0 通信：每行
一条 JSON 消息，请求与响应靠 `id` 配对。

本模块只依赖标准库 + 已有的 `registry.list_tool_schemas()` / `call_tool()`，
不引入 `mcp` PyPI 包 —— 这样「零 EDA 强依赖，仅 PyYAML」的承诺对新入口同样
成立，用户不需要为了接 Agent 再装一套传输层。

实现的方法（MCP 2024-11-05 核心子集）：
  * ``initialize``            握手，返回 protocolVersion / serverInfo / capabilities
  * ``notifications/initialized``  客户端就绪通知（无响应）
  * ``tools/list``            列出 13 个原子工具的 name/description/inputSchema
  * ``tools/call``            调用工具，把 ``ToolResult`` 信封放进 content
  * ``ping``                  健康检查
  * ``shutdown`` / ``exit``   优雅退出

设计要点（都是踩坑产物）：
  1. **stdout 只能有协议消息**。工具执行期间任何 print（含库内的调试输出）都会
     污染协议流，客户端解析失败后表现为「工具超时」而非报错。因此工具调用期间
     把 sys.stdout 临时重定向到 stderr 缓冲区。
  2. **一条坏消息不能杀死整个 server**。JSON 解析失败要回 JSON-RPC 的
     ``-32700 Parse error``；未知方法回 ``-32601``；参数非法回 ``-32602``。
     直接抛异常退出会让客户端看到「连接被关闭」，丢失真正的错误原因。
  3. **工具失败 ≠ 协议失败**。工具返回 ``ok: false`` 是**业务**结果，应作为
     正常 ``tools/call`` 响应返回（``isError: true``），而不是 JSON-RPC error。
     否则客户端会把它当成传输层故障并重试，噪声翻倍。
  4. ``id`` 可以缺省（通知）或为 0/""（合法 falsy）。用 ``"id" in msg``
     而不是 ``msg.get("id")`` 判断，否则会把 id=0 的请求当通知吞掉。
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any, Dict, List, Optional, TextIO

from . import __version__ as _pkg_version
from .mcp_resources import (
    get_prompt,
    list_prompts,
    list_resources,
    read_resource,
)
from .registry import TOOL_REGISTRY, call_tool, list_tool_schemas

#: 支持的 MCP 协议版本（客户端请求的版本在本列表内就回显它，否则回最新版）
#: 支持的 MCP 协议版本，**新的在前**。`initialize` 时客户端请求的版本在这里面
#: 就原样回显，否则回最新版（旧客户端仍可连，不会因版本号不识别而被拒）。
#: 2025-06-18 引入了 structuredContent / resources 订阅等能力；2025-03-26 是
#: 其前身；2024-11-05 与 2024-10-07 为兼容旧客户端保留。
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07")
LATEST_PROTOCOL = PROTOCOL_VERSIONS[0]

SERVER_NAME = "fpga-toolbox"
#: serverInfo 里的版本**从包版本派生**，不再单独维护 —— 否则发版时容易
#: 只改 pyproject/__init__ 而漏掉这里，客户端看到的是过期版本号。
SERVER_VERSION = _pkg_version

# JSON-RPC 2.0 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _result(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


class McpServer:
    """JSON-RPC 2.0 请求处理器（与传输方式解耦，便于单测直接喂字典）。"""

    def __init__(self) -> None:
        self.initialized = False
        self.should_exit = False

    # -- 单个请求 -------------------------------------------------------
    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理一条消息，返回响应 dict；通知（无 id）返回 None。"""
        if not isinstance(msg, dict):
            return _error(None, INVALID_REQUEST, "request must be a JSON object")
        if msg.get("jsonrpc") != "2.0":
            return _error(msg.get("id"), INVALID_REQUEST,
                          "jsonrpc must be exactly '2.0'")
        has_id = "id" in msg
        method = msg.get("method")
        if not isinstance(method, str):
            # 没有 method：可能是客户端发来的响应（本 server 不发起请求）——
            # 静默忽略，而不是回错误（回错误会与客户端形成乒乓）
            if not has_id:
                return None
            return _error(msg.get("id"), INVALID_REQUEST,
                          "missing 'method' (expected a request or notification)")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            if not has_id:
                return None
            return _error(msg.get("id"), INVALID_PARAMS, "'params' must be an object")

        # 通知：不需要响应
        if not has_id:
            if method == "notifications/initialized":
                self.initialized = True
            elif method == "exit":
                self.should_exit = True
            return None

        try:
            result = self._dispatch(method, params)
        except _RpcError as exc:
            return _error(msg.get("id"), exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001 - 兜底，绝不让一条请求杀死 server
            return _error(msg.get("id"), INTERNAL_ERROR,
                          f"internal error: {type(exc).__name__}: {exc}")
        if result is _NO_RESPONSE:
            return None
        return _result(msg.get("id"), result)

    def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        if method == "initialize":
            req_ver = params.get("protocolVersion")
            ver = req_ver if req_ver in PROTOCOL_VERSIONS else LATEST_PROTOCOL
            self.initialized = True
            return {
                "protocolVersion": ver,
                # 能力声明必须与实际实现一致：声明了 resources/prompts
                # 客户端才会去探测；反过来，实现了却不声明，客户端永不调用。
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": list_tool_schemas()}
        if method == "tools/call":
            return self._call_tool(params)
        if method == "shutdown":
            self.should_exit = True
            return {}
        if method == "resources/list":
            return {"resources": list_resources()}
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise _RpcError(INVALID_PARAMS,
                                "'resources/read' requires a string 'uri'")
            try:
                content = read_resource(uri)
            except KeyError:
                raise _RpcError(
                    INVALID_PARAMS, f"Unknown resource uri: {uri}",
                    {"available": [r["uri"] for r in list_resources()]}) from None
            return {"contents": [content]}
        if method == "prompts/list":
            return {"prompts": list_prompts()}
        if method == "prompts/get":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise _RpcError(INVALID_PARAMS,
                                "'prompts/get' requires a string 'name'")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                raise _RpcError(INVALID_PARAMS, "'arguments' must be an object")
            try:
                return get_prompt(name, args)
            except KeyError:
                raise _RpcError(
                    INVALID_PARAMS, f"Unknown prompt: {name}",
                    {"available": [p["name"] for p in list_prompts()]}) from None
        raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    def _call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _RpcError(INVALID_PARAMS, "'tools/call' requires a string 'name'")
        if name not in TOOL_REGISTRY:
            raise _RpcError(
                INVALID_PARAMS, f"Unknown tool: {name}",
                {"available": sorted(TOOL_REGISTRY)})
        raw_args = params.get("arguments")
        if raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, dict):
            raise _RpcError(INVALID_PARAMS, "'arguments' must be a JSON object")

        # 关键：工具执行期间 stdout 必须干净。把 sys.stdout 临时换到 stderr，
        # 任何 print 都落到 stderr（客户端看不到，但本地日志保留）。
        real_stdout = sys.stdout
        trap = io.StringIO()
        result: Dict[str, Any]
        try:
            sys.stdout = sys.stderr
            result = call_tool(name, raw_args)
        finally:
            sys.stdout = real_stdout
            leaked = trap.getvalue()

        ok = bool(result.get("ok"))
        # 把完整 ToolResult 信封序列化进 text content：agent 需要看到
        # diagnostics / data，而不是一句 "success"。
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        out = {
            "content": [{"type": "text", "text": payload}],
            "isError": not ok,
            # structuredContent（MCP 2025-06-18 起）：强类型客户端可直接读
            # result.structuredContent 而不用再解析 content[0].text 里的 JSON。
            # 两个出口内容必须一致 —— 只给一个会让部分客户端拿到空结构。
            "structuredContent": result,
        }
        if leaked:
            out["_stdout_leak"] = leaked[:2000]
        return out


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


_NO_RESPONSE = object()


def _force_utf8(stream: TextIO) -> TextIO:
    """把 stdio 流强制重配成 UTF-8。

    MCP 规定 JSON-RPC 消息是 UTF-8。但 Windows 上 `sys.stdin`/`sys.stdout`
    默认用 **locale 编码**（常见 cp1252），任何非 ASCII 字节都会抛

        UnicodeDecodeError: 'charmap' codec can't decode byte 0x90 ...

    实测 CI Windows job：`tools/call` 返回体里有中文诊断信息，读取时直接崩，
    而 Linux/macOS 上默认就是 UTF-8，本地永远复现不出来。
    `reconfigure` 只对 TextIOWrapper 有效；调用方传入的 StringIO（单测）没有
    该方法，用 hasattr 兜底。
    """
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # 流已关闭/不支持时保持原样
            pass
    return stream


def serve(stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """主循环：逐行读 JSON-RPC 消息，逐行写响应（newline-delimited）。"""
    stdin = _force_utf8(stdin) if stdin is not None else _force_utf8(sys.stdin)
    stdout = _force_utf8(stdout) if stdout is not None else _force_utf8(sys.stdout)
    server = McpServer()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = _error(None, PARSE_ERROR, f"Parse error: {exc}")
        else:
            if isinstance(msg, list):
                # JSON-RPC 批量请求：逐条处理，过滤掉通知（无响应）
                batch = [server.handle(m) for m in msg]
                resp = [r for r in batch if r is not None]
                if not resp:
                    resp = None
            else:
                resp = server.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
        if server.should_exit:
            break
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        print(
            "用法: python3 -m fpga_toolbox serve-mcp\n\n"
            "在 stdio 上提供 MCP (JSON-RPC 2.0) 服务，供 Agent 客户端接入。\n"
            "示例客户端配置（Claude Desktop claude_desktop_config.json）:\n"
            '  {"mcpServers": {"fpga-toolbox": '
            '{"command": "python3", "args": ["-m", "fpga_toolbox", "serve-mcp"]}}}',
            file=sys.stderr,
        )
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
