# Changelog — `tool_clean_pt_netlist` 未注册为工具 + 文档状态过期

前三轮审计修了 `eda_campaign` 的 8 个缺陷。本轮转向**集成层**，发现功能本身修好了，
但它对用户还是不可见。

## 缺陷 A — 全仓库唯一「是工具却不注册」的函数

`eda_campaign.py` 里只有 `tool_clean_pt_netlist` 用了 `tool_` 前缀且返回标准
`ToolResult` 信封：

```python
return ok_result("clean_pt_netlist", stats)   # {ok, tool, data, diagnostics, error}
```

但它**没有出现在 `TOOL_REGISTRY` 里**。我用脚本比对了全部 `tool_*` 函数与 registry，
确认它是唯一遗漏项：

```
未注册: tool_clean_pt_netlist
```

后果：`call_tool("clean_pt_netlist", ...)` 报 `Unknown tool`，CLI 也没有这个子命令。
用户拿到的是一个「写好但摸不到」的功能——文档里写了怎么用，实际调不到。

修复：注册为第 13 个工具，schema 含 `in_verilog` / `out_verilog` /
可选 `blackbox_modules`。端到端验证：

```
$ python3 -m fpga_toolbox clean_pt_netlist --in-verilog in.v --out-verilog out.v
ok: True, removed_instances: 2
```

输出网表经 Verilator 编译 **0 硬错误**。

## 缺陷 B — README 仍称模块「尚未实现」

上一轮实现了 `eda_campaign` 并把 changelog、文档顶部都改成「已实现」，但
README 的离线文档表还留着旧话：

```
| EXPERIMENT_REPRO_GUIDE.md | ... （**设计稿**，`eda_campaign` 模块尚未实现 ...）|
```

同一仓库里两处说法相反。已改为「对应 `fpga_toolbox.eda_campaign` 库模块，已实现并接入 CI」，
并把文档里的行数声明从 `366 行` 更新为实际的 `504 行`（前几轮修复增加了行数）。

## 顺带修掉一个硬编码断言

注册新工具后 fusion 测试失败：

```
FAIL 12 tools registered (got 13)
```

断言写死 `len(schemas) == 12`。这与之前修过的 `passed == 4` 是同一类问题——正常的
增项被误报为回归。改为**下限断言** `>= 13`（增工具不误报、删工具仍会抓），并新增
`clean_pt_netlist registered in MCP schemas` 的针对性断言。

README 的工具表、工具总数（12 → 13）、章节标题同步更新。

## 验证

- CLI 端到端：`clean_pt_netlist` 可用，清理参数化黑盒 → Verilator 0 硬错误
- `call_tool` / MCP schema / CLI `list` 三处都能看到新工具
- 工具数 12 → 13；audit_harness 仍 9/9（A1–A9 不依赖工具总数）
- eda_campaign / regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 全绿
