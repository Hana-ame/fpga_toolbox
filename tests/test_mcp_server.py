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
    # 未知版本回落**最新**协议（不是钉死的某个值 —— 升级版本号时这条断言
    # 不该跟着改，否则每次升版都要改测试，容易改成"测试跟着实现走"）
    from fpga_toolbox.mcp_server import LATEST_PROTOCOL
    r2 = McpServer().handle({
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    })
    assert r2["result"]["protocolVersion"] == LATEST_PROTOCOL, r2


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
    # 不要写死工具数量 —— 新增工具时这条断言不该跟着改（否则测试变成
    # "跟着实现走"，失去回归价值）。直接与注册表比对。
    from fpga_toolbox.registry import TOOL_REGISTRY
    assert len(parsed[1]["result"]["tools"]) == len(TOOL_REGISTRY), parsed[1]


def test_mcp_all_resources_and_prompts_render() -> None:
    """每个 resource 与每个 prompt 都必须能真的渲染出内容。

    未覆盖的三个函数（tools_doc / 两个 prompt 渲染器）暴露的真实风险：
    resource 或 prompt 只在**被请求时**才执行渲染，若某条路径有 bug，客户端
    拉到的是 500/-32603，而 server 启动与 tools/list 一切正常 —— 缺陷直到
    真实使用时才暴露。这里把每个 uri / name 都拉一遍。
    """
    print("coverage: every MCP resource and prompt renders")
    from fpga_toolbox.mcp_resources import (
        list_prompts,
        list_resources,
        read_resource,
    )

    srv = McpServer()
    # ---- 每个 resource 都能读且内容非空 ----
    for meta in list_resources():
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read",
                        "params": {"uri": meta["uri"]}})
        assert "result" in r, (meta["uri"], r)
        content = r["result"]["contents"][0]
        assert content["uri"] == meta["uri"], content
        assert content["mimeType"] == "text/markdown", content
        assert len(content["text"].strip()) > 100, (meta["uri"], content["text"][:100])
        # 直接调底层也要一致（MCP 与库内两个出口不能漂移）
        assert read_resource(meta["uri"])["text"] == content["text"]

    # tools resource 必须真的列出全部工具（不是空表）
    tools_res = read_resource("fpga-toolbox://tools")["text"]
    from fpga_toolbox.registry import TOOL_REGISTRY
    for name in TOOL_REGISTRY:
        assert f"`{name}`" in tools_res, name

    # ---- 每个 prompt 都能渲染，且参数被插入 ----
    for meta in list_prompts():
        args = {}
        for a in meta.get("arguments", []):
            if a.get("required"):
                args[a["name"]] = f"<{a['name']}>"
        r = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                        "params": {"name": meta["name"], "arguments": args}})
        assert "result" in r, (meta["name"], r)
        msgs = r["result"]["messages"]
        assert msgs and msgs[0]["role"] == "user", (meta["name"], msgs)
        body = msgs[0]["content"]["text"]
        assert len(body) > 100, (meta["name"], body[:100])
        # prompt 必须真的指导调用工具，而不是一段空话
        assert "`" in body, (meta["name"], body[:120])
        for k, v in args.items():
            assert v in body, (meta["name"], k, body[:200])

    # 三个命名 prompt 的存在性与用途（避免有人删掉后静默失效）
    names = {p["name"] for p in list_prompts()}
    assert {"rtl-to-mutation-audit", "debug-failing-simulation",
            "analyze-eda-campaign"} <= names, names
    # EDA campaign prompt 必须提到四个解析工具
    eda = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/get",
                      "params": {"name": "analyze-eda-campaign", "arguments": {}}})
    eda_body = eda["result"]["messages"][0]["content"]["text"]
    for t in ("parse_uvm_log", "parse_dc_pt_qor", "aggregate_seed_medians",
              "generate_remote_run_script"):
        assert t in eda_body, (t, eda_body[:300])
    # debug prompt 必须给出症状→诊断码的分流表（这是它的全部价值）
    dbg = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "prompts/get",
                      "params": {"name": "debug-failing-simulation",
                                 "arguments": {"log_path": "/tmp/x.log"}}})
    dbg_body = dbg["result"]["messages"][0]["content"]["text"]
    assert "E050" in dbg_body and "/tmp/x.log" in dbg_body, dbg_body[:300]


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


def test_mcp_resources_and_prompts() -> None:
    """resources/list|read 与 prompts/list|get 必须返回真实内容。

    之前这两个方法固定返回空列表 —— 客户端拿不到诊断码速查/协议规范，只能把
    这些上下文写进提示词，每次 session 重来一遍。空列表还有一个更隐蔽的坏处：
    客户端探测到「能力存在但为空」后不会重试，问题永远不会被发现。
    """
    print("coverage: MCP resources/list|read and prompts/list|get")
    srv = McpServer()

    # capabilities 必须声明 resources/prompts（否则客户端根本不来探测）
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18"}})
    caps = init["result"]["capabilities"]
    assert "resources" in caps and "prompts" in caps, caps
    # 新版协议必须被原样回显（不是回落到旧版本）
    assert init["result"]["protocolVersion"] == "2025-06-18", init["result"]

    # ---- resources ----
    rl = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    res = rl["result"]["resources"]
    assert len(res) >= 3, res
    uris = {r["uri"] for r in res}
    assert "fpga-toolbox://diagnostics/codes" in uris, uris
    for r in res:
        assert set(r) >= {"uri", "name", "mimeType"}, r

    codes = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                        "params": {"uri": "fpga-toolbox://diagnostics/codes"}})
    text = codes["result"]["contents"][0]["text"]
    # 内容必须是**真实**诊断码表，不是占位符
    assert "E001" in text and "W0" in text, text[:300]
    assert "| 码 | 含义 |" in text, text[:200]
    assert codes["result"]["contents"][0]["mimeType"] == "text/markdown"

    protos = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/read",
                         "params": {"uri": "fpga-toolbox://protocols"}})
    ptext = protos["result"]["contents"][0]["text"]
    assert "axi4_stream" in ptext and "apb" in ptext, ptext[:300]

    # 未知 uri：-32602 且列出可用 uri（方便 Agent 自纠）
    bad = srv.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/read",
                      "params": {"uri": "fpga-toolbox://nope"}})
    assert bad["error"]["code"] == INVALID_PARAMS, bad
    assert bad["error"]["data"]["available"], bad
    # 缺 uri 参数
    miss = srv.handle({"jsonrpc": "2.0", "id": 6, "method": "resources/read",
                       "params": {}})
    assert miss["error"]["code"] == INVALID_PARAMS, miss

    # ---- prompts ----
    pl = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "prompts/list"})
    prompts = pl["result"]["prompts"]
    assert len(prompts) >= 3, prompts
    names = {p["name"] for p in prompts}
    assert "rtl-to-mutation-audit" in names, names
    for p in prompts:
        assert set(p) >= {"name", "description"}, p

    got = srv.handle({"jsonrpc": "2.0", "id": 8, "method": "prompts/get",
                      "params": {"name": "rtl-to-mutation-audit",
                                 "arguments": {"rtl_file": "dut.sv"}}})
    msgs = got["result"]["messages"]
    assert msgs and msgs[0]["role"] == "user", msgs
    body = msgs[0]["content"]["text"]
    # prompt 必须把调用方参数真正插进去，并且覆盖真实工具流程
    assert "dut.sv" in body, body[:300]
    for tool in ("extract_interfaces", "emit_testbench", "run_sim", "run_mutation"):
        assert tool in body, (tool, body[:300])

    # 未知 prompt
    badp = srv.handle({"jsonrpc": "2.0", "id": 9, "method": "prompts/get",
                       "params": {"name": "nope"}})
    assert badp["error"]["code"] == INVALID_PARAMS, badp
    assert badp["error"]["data"]["available"], badp


def test_mcp_structured_content_matches_text() -> None:
    """structuredContent 必须与 content[0].text 里的 JSON **完全一致**。

    两个出口服务于不同客户端：强类型客户端读 structuredContent，只认文本的
    读 content。只填一个或内容不一致，会让其中一半客户端拿到空/错的数据。
    """
    print("coverage: MCP structuredContent mirrors the tool envelope")
    srv = McpServer()
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "audit_harness", "arguments": {}}})
    res = r["result"]
    assert "structuredContent" in res, res.keys()
    structured = res["structuredContent"]
    textual = json.loads(res["content"][0]["text"])
    assert structured == textual, (structured.keys(), textual.keys())
    # 强类型字段必须可直接消费
    assert structured["tool"] == "audit_harness", structured
    assert isinstance(structured["data"], dict), structured
    assert structured["data"]["passed"] == structured["data"]["total"], structured["data"]
    # 失败调用同样要带 structuredContent（客户端需要 code/path 做分支）
    bad = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "extract_interfaces",
                                 "arguments": {"rtl_file": "/missing.sv"}}})
    assert bad["result"]["isError"] is True, bad
    assert bad["result"]["structuredContent"]["ok"] is False, bad


def test_mcp_protocol_version_negotiation() -> None:
    """协议协商：支持列表内的版本原样回显，未知版本回落最新版而非报错。"""
    print("coverage: MCP protocol version negotiation")
    from fpga_toolbox.mcp_server import LATEST_PROTOCOL, PROTOCOL_VERSIONS

    srv = McpServer()
    for ver in PROTOCOL_VERSIONS:
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": ver}})
        assert r["result"]["protocolVersion"] == ver, (ver, r["result"])
    # 未知/未来版本：回落最新版（旧客户端仍可连，不会因版本不识别被拒）
    future = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                         "params": {"protocolVersion": "2099-01-01"}})
    assert future["result"]["protocolVersion"] == LATEST_PROTOCOL, future["result"]
    # 声明了 resources/prompts 能力，且与实现一致（都返回非空）
    caps = future["result"]["capabilities"]
    assert caps["resources"] and caps["prompts"], caps


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
    test_mcp_resources_and_prompts()
    test_mcp_structured_content_matches_text()
    test_mcp_protocol_version_negotiation()
    test_mcp_all_resources_and_prompts_render()
    test_mcp_main_entry_points()
    test_mcp_batch_requests()
    print("\nALL MCP SERVER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
