# 功能增强 · 零依赖 MCP stdio server（`serve-mcp`）

## 背景

工具侧早已具备完整的 `list_tool_schemas()` 与 `call_tool()`，以及 MCP JSON
Schema 导出，但缺少**传输层入口** —— Agent 客户端（Claude Desktop / IDE 插件 /
自研 agent）无法直接接入，只能自己包一层 subprocess。

MCP 跑在 stdio 上，用 newline-delimited JSON-RPC 2.0 通信。本实现只用标准库，
**不引入 `mcp` PyPI 包** ——「零 EDA 强依赖，仅 PyYAML」的承诺对新入口同样成立。

## 实现

`fpga_toolbox/mcp_server.py` + CLI 子命令 `serve-mcp`。

支持的方法：`initialize` / `notifications/initialized` / `tools/list` /
`tools/call` / `ping` / `shutdown` / `exit`，以及 `resources/list`、
`prompts/list` 的空列表响应（部分客户端会并发探测）。

### 四条硬约定（全部来自真实协议约束）

1. **stdout 只能有协议消息。** 工具执行期间把 `sys.stdout` 临时重定向到
   stderr，任何 `print` 都写进 stderr。否则客户端解析 JSON 失败，现象是
   「工具超时」而非报错，极难定位。
2. **工具失败 ≠ 协议失败。** 工具返回 `ok: false` 时 `tools/call` 仍是正常
   响应，带 `isError: true`，完整 `ToolResult` 信封（含 diagnostics/data）
   在 `content[0].text` 里。走 JSON-RPC error 会让客户端当成传输层故障重试。
3. **一条坏消息不能杀死 server。** 坏 JSON → `-32700`，未知方法 → `-32601`，
   非法参数 → `-32602`，之后继续服务（测试专门验证「连续 4 条坏消息后仍响应」）。
4. **通知与 falsy id 要分清。** 用 `"id" in msg` 而不是 `msg.get("id")` 判断，
   否则 `id: 0` / `id: ""` 这类**合法请求**会被当成通知吞掉。

另支持 JSON-RPC 2.0 批量请求（数组）。

## 测试

`tests/test_mcp_server.py` 共 9 个用例，含真实子进程端到端：

| 用例 | 验证 |
|---|---|
| initialize | 版本回显、能力声明、不支持版本回落 |
| tools/list + tools/call | 13 工具的 schema 与注册表一致；完整信封透出 |
| 业务失败 | `isError: true` 且**无** `error` 字段 |
| stdout 洁净性 | 每行输出都是合法 JSON |
| 错误码 | -32700/-32601/-32602 + 坏消息后仍存活 |
| 通知 / falsy id | 通知无响应；`0`/`""`/`false` 是合法请求 |
| CLI 子进程 | 真跑 `python3 -m fpga_toolbox serve-mcp`，3 条响应、13 个工具 |
| main()/`--help` | rc=0、写 stderr、stdout 干净 |
| 批量请求 | 数组响应、过滤通知 |

## 客户端配置

```json
{"mcpServers": {"fpga-toolbox": {
  "command": "python3", "args": ["-m", "fpga_toolbox", "serve-mcp"]}}}
```
