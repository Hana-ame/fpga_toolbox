"""MCP stdio server（`serve-mcp`）的端到端协议测试。

覆盖点全部来自真实协议约束，而不是「函数能跑通」：

1. **握手** —— `initialize` 回显客户端版本（在支持列表内）、声明 tools 能力；
   不支持的版本回落到最新版而不是报错（旧客户端仍可连）。
2. **tools/list / tools/call** —— 13 个工具的 schema 原样透出；调用结果把完整
   `ToolResult` 信封放进 text content（agent 需要看 diagnostics/data）。
3. **业务失败 ≠ 协议失败** —— 工具返回 `ok:false` 必须是正常响应 + `isError`，
   不能是 JSON-RPC error。否则客户端会当成传输层故障并重试。
4. **stdout 洁净性** —— 工具执行期间的 print 不得污染协议流。stdout 上只允许
   newline-delimited JSON；任何多余字节都会让客户端解析失败。
5. **健壮性** —— 坏 JSON 回 -32700、未知方法回 -32601、未知工具/非法参数回
   -32602，且**一条坏消息不能杀死 server**（后续请求仍要正常响应）。
6. **通知与 falsy id** —— 无 id 的通知不产生响应；`id: 0` / `id: ""` 是合法
   请求（旧实现用 `msg.get("id")` 判断会把它们当通知吞掉）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fpga_toolbox.mcp_server import (  # noqa: E402
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    McpServer,
    serve,
)


def _run_stdio(lines) -> list:
    """把若干行喂给 serve()，返回解析后的响应列表。"""
    import io

    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    out = []
    for line in stdout.getvalue().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def test_mcp_initialize_and_capabilities() -> None:
    print("coverage: MCP initialize handshake and capability advertisement")
    srv = McpServer()
    r = srv.handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05",
                   "clientInfo": {"name": "probe", "version": "1.0"}},
    })
    assert r["jsonrpc"] == "2.0" and r["id"] == 1, r
    res = r["result"]
    # 支持的版本必须原样回显，否则客户端会按自己假设的版本发消息
    assert res["protocolVersion"] == "2024-11-05", res
    assert res["serverInfo"]["name"] == "fpga-toolbox", res
    assert "tools" in res["capabilities"], res
    # 未在支持列表内的版本：回落到最新版而不是报错（旧/未来客户端仍可连）
    r2 = McpServer().handle({
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    })
    assert r2["result"]["protocolVersion"] == "2024-11-05", r2


def test_mcp_tools_list_and_call() -> None:
    print("coverage: MCP tools/list + tools/call round trip")
    from fpga_toolbox.registry import TOOL_REGISTRY

    out = _run_stdio([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "audit_harness", "arguments": {}}}),
    ])
    assert len(out) == 2, out
    tools = out[0]["result"]["tools"]
    # schema 必须与注册表一致（否则客户端按错 schema 构造参数）
    assert len(tools) == len(TOOL_REGISTRY), (len(tools), len(TOOL_REGISTRY))
    names = {t["name"] for t in tools}
    assert names == set(TOOL_REGISTRY), names ^ set(TOOL_REGISTRY)
    for t in tools:
        assert set(t) >= {"name", "description", "inputSchema"}, t
        assert t["inputSchema"]["type"] == "object", t["name"]

    call = out[1]["result"]
    assert call["isError"] is False, call
    inner = json.loads(call["content"][0]["text"])
    # 完整信封：agent 必须能看到 diagnostics / data，而不是一句 "ok"
    assert inner["ok"] is True and inner["tool"] == "audit_harness", inner
    assert inner["data"]["passed"] == inner["data"]["total"], inner["data"]


def test_mcp_business_failure_is_not_protocol_error() -> None:
    print("coverage: tool failure maps to isError, not JSON-RPC error")
    out = _run_stdio([json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "extract_interfaces",
                   "arguments": {"rtl_file": "/definitely/missing.sv"}},
    })])
    assert len(out) == 1, out
    resp = out[0]
    # 关键：业务失败不能走 JSON-RPC error 通道
    assert "error" not in resp, resp
    assert resp["result"]["isError"] is True, resp
    inner = json.loads(resp["result"]["content"][0]["text"])
    assert inner["ok"] is False and "not found" in (inner.get("error") or ""), inner


def test_mcp_stdout_stays_protocol_clean() -> None:
    """工具执行期间的任何 print 都不得写进 stdout。

    这是 MCP 最常见的实现事故：工具内部打印日志 → 客户端解析 JSON 失败 →
    表现为「工具超时」而不是报错，极难定位。server 必须把 stdout 临时重定向。
    """
    print("coverage: MCP stdout cleanliness during tool execution")
    out = _run_stdio([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "audit_harness", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
    ])
    # serve() 内部已对每行做 json.loads；能走到这里说明 stdout 全为合法 JSON。
    assert len(out) == 2, out
    assert out[1]["result"] == {}, out[1]


def test_mcp_error_codes_and_resilience() -> None:
    print("coverage: MCP error codes (-32700/-32601/-32602) and post-error liveness")
    out = _run_stdio([
        "{not json",                                                   # 坏 JSON
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "bogus/x"}),  # 未知方法
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "no_such_tool"}}),              # 未知工具
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "audit_harness", "arguments": 5}}),  # 参数非法
        json.dumps({"jsonrpc": "2.0", "id": 5, "method": "ping"}),     # 存活检查
    ])
    codes = [r.get("error", {}).get("code") for r in out]
    assert codes[0] == PARSE_ERROR, codes
    assert codes[1] == METHOD_NOT_FOUND, codes
    assert codes[2] == INVALID_PARAMS, codes
    assert codes[3] == INVALID_PARAMS, codes
    # 连续 4 条坏消息之后 server 仍须正常工作
    assert out[4]["result"] == {}, out[4]
    # 未知工具的错误应列出可用工具，方便 agent 自纠
    assert "available" in out[2]["error"].get("data", {}), out[2]["error"]


def test_mcp_notifications_and_falsy_ids() -> None:
    print("coverage: MCP notifications produce no response; id=0/'' are requests")
    srv = McpServer()
    # 通知（无 id）不产生响应
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert srv.initialized is True
    # id=0 与 id="" 是**合法请求**，不能被当成通知吞掉
    for falsy in (0, "", False):
        r = srv.handle({"jsonrpc": "2.0", "id": falsy, "method": "ping"})
        assert r is not None, falsy
        assert r["id"] == falsy, (falsy, r)
    # shutdown 通知式退出
    assert srv.handle({"jsonrpc": "2.0", "method": "exit"}) is None
    assert srv.should_exit is True
    # 非法 jsonrpc 版本要拒绝，且用 -32600 Invalid Request 而不是 -32602
    # （参数非法是 -32602；整个信封不合法属于 invalid request）
    bad = srv.handle({"jsonrpc": "1.0", "id": 7, "method": "ping"})
    assert bad["error"]["code"] == INVALID_REQUEST, bad


def test_mcp_cli_subcommand_end_to_end() -> None:
    """真正启动 `python3 -m fpga_toolbox serve-mcp` 子进程跑一遍协议。"""
    print("coverage: serve-mcp CLI subprocess end-to-end")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "method": "exit"},
    ]
    payload = "\n".join(json.dumps(m) for m in msgs) + "\n"
    cp = subprocess.run(
        [sys.executable, "-m", "fpga_toolbox", "serve-mcp"],
        cwd=str(ROOT), input=payload, capture_output=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        # 显式 UTF-8：Windows 上 text=True 用 locale 编码（cp1252）解码，
        # 工具返回体里的中文诊断会抛 UnicodeDecodeError（CI Windows job 实测）。
        encoding="utf-8", errors="replace",
    )
    assert cp.returncode == 0, (cp.returncode, cp.stderr[-500:])
    lines = [ln for ln in cp.stdout.splitlines() if ln.strip()]
    # 通知（notifications/initialized、exit）不产生响应 → 3 条
    assert len(lines) == 3, (len(lines), cp.stdout[:400])
    parsed = [json.loads(ln) for ln in lines]
    assert [p["id"] for p in parsed] == [1, 2, 3], parsed
    assert len(parsed[1]["result"]["tools"]) == 13, parsed[1]


def test_mcp_main_entry_points() -> None:
    """`main()` / `--help` 是用户入口，必须被覆盖。

    `--help` 走的是 stderr + rc=0（不能污染 stdout 协议流），而正常 `main()`
    在 stdin 为空时应立即返回 0 而不是阻塞 —— 这两条都是 CI 里
    `python3 -m fpga_toolbox serve-mcp --help` 与「空输入管道」的实际行为。
    """
    print("coverage: MCP main() and --help entry points")
    import io
    from contextlib import redirect_stderr

    from fpga_toolbox.mcp_server import main as mcp_main

    # --help：写 stderr、rc=0、stdout 必须干净（不能有协议消息）
    err = io.StringIO()
    with redirect_stderr(err):
        rc = mcp_main(["--help"])
    assert rc == 0, rc
    assert "serve-mcp" in err.getvalue(), err.getvalue()[:300]

    # 正常 main()：空 stdin 应立即返回（不阻塞、不产生输出）
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO("")
    out = io.StringIO()
    sys.stdout = out
    try:
        rc2 = mcp_main([])
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
    assert rc2 == 0, rc2
    assert out.getvalue() == "", out.getvalue()[:200]


def test_mcp_batch_requests() -> None:
    print("coverage: MCP batch (JSON-RPC 2.0 array) requests")
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},  # 通知，不应有响应
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    out = _run_stdio([json.dumps(batch)])
    assert len(out) == 1, out  # 批量响应本身是一行（数组）
    assert isinstance(out[0], list), out
    assert [r["id"] for r in out[0]] == [1, 2], out[0]


def main() -> int:
    test_mcp_initialize_and_capabilities()
    test_mcp_tools_list_and_call()
    test_mcp_business_failure_is_not_protocol_error()
    test_mcp_stdout_stays_protocol_clean()
    test_mcp_error_codes_and_resilience()
    test_mcp_notifications_and_falsy_ids()
    test_mcp_cli_subcommand_end_to_end()
    test_mcp_main_entry_points()
    test_mcp_batch_requests()
    print("\nALL MCP SERVER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
