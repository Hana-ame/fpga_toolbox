# 功能增强 · Agent 控制面打通 + MCP 生产化 + v0.6.0

本轮收口四项 P0/P1：打通 Agent 控制面缺口、MCP 生产化、发布流水线对齐、v0.6.0 发版。
（目标 2「拆分 protocol_tb_emitter + 桥接 DUT 联合 TB」按建议留给 v0.7.0。）

## 1 · 暴露 eda_campaign 分析能力（P0）

**缺口**：`eda_campaign.py` 已实现 `parse_uvm_log` / `aggregate_seed_medians` /
`parse_dc_pt_qor` / `verify_paired_baseline_reproducibility` /
`generate_remote_run_script`，但只注册了 `clean_pt_netlist` —— Agent 通过 MCP
完全看不到 VCS/DC/PT 的解析能力。

**实现**：新增 4 个工具包装并注册（工具数 13 → 17）。关键设计：

- 每个工具接受 **`*_text` 或 `*_path`**：日志可能来自文件，也可能由上游直接给出。
  两者都空时给结构化错误（说明该传哪个参数），而不是 KeyError/FileNotFoundError。
- **不需要任何商业 EDA 许可证** —— 解析的是「别人跑出来的日志」。这是无许可证
  机器与 Agent 场景的常态，也是为什么这些工具必须能在零 EDA 环境工作。
- `generate_remote_run_script` 的 `ValueError`（注入/危险路径）转成结构化错误 +
  E024，避免冒泡成 MCP 传输层异常（客户端会当成 server 崩了）。

顺带修掉两个真实缺陷：

1. `aggregate_seed_medians` 返回**裸 dict**（无 ok 信封），而
   `parse_uvm_log`/`parse_dc_pt_qor` 返回带信封的 ToolResult。旧包装一律按信封解，
   于是 `ok` 恒为 False、`data` 恒为空 —— **一次成功的聚合看起来像失败**。
2. 解析工具的错误信封 `tool` 字段写成内部模块名 `eda_campaign`，调用方无法区分
   是哪个工具失败。`_read_text_arg` 现在接收并透传真实工具名。

## 2 · `run_sim` 后端分发（P0）

**缺口**：`run_sim` 硬绑 Verilator：`probe_eda_env()` 不满足版本就整个拒绝。于是
`emit_testbench --spec-path` 产出的 **cocotb Makefile 永远跑不通**（它甚至不是
shell 脚本），iverilog 用户也被一刀切挡在门外。

**实现**：`backend` 参数（`auto` / `verilator` / `cocotb` / `iverilog`），
按后端分发环境校验与执行方式：

| backend | 执行方式 | 必需工具链 |
|---|---|---|
| `verilator` | `bash run_*.sh <suite>` | verilator >= 5.040 |
| `cocotb` | `make -C <dir> SIM=verilator` | make + cocotb |
| `iverilog` | `bash run_*.sh <suite>` | iverilog + vvp |
| `auto` | Makefile → cocotb，否则 verilator | 取决于判定结果 |

只校验**被选中后端真正需要的**东西，缺什么在 E020 里点名（不再一律报
"Verilator requirement unmet"）。两种形态错配当场拒绝并给正确用法：
Makefile 交给 `bash` 会得到 127「command not found」，脚本交给 `make` 会报
"No targets" —— 这些错误信息完全不指向真实原因。

**实测踩到的真实缺陷**：`subprocess.run(cwd=...)` 只改工作目录，**不改环境里的
`PWD`**。cocotb Makefile 惯用 `VERILOG_SOURCES = $(PWD)/dut.sv`，于是它在仓库根
目录找源文件而不是脚本所在目录：

```
make: *** No rule to make target '/home/.../fpga_toolbox/dut.sv'
```

手工 `make` 完全正常（shell 会维护 `PWD`），只有被 `run_sim` 拉起时失败 ——
典型的「工具调用与手工执行不一致」。修复：显式 `run_env["PWD"] = 脚本目录`。

验证：「契约 → cocotb TB → 运行 → 归一化」闭环实跑 `TESTS=1 PASS=1 FAIL=0`；
iverilog 后端实跑 PASS 且日志落盘。

## 3 · MCP 生产化（P1）

- **协议协商**：支持 `2025-06-18` / `2025-03-26` / `2024-11-05` / `2024-10-07`，
  未知版本回落最新（旧客户端仍可连）。
- **`structuredContent`**：`tools/call` 同时给 `content[0].text` 与强类型
  `structuredContent`，两者内容**完全一致**。只填一个会让一半客户端拿到空结构。
- **3 个只读 resources**：诊断码速查表、11 类协议规范、工具清单。内容从
  `docs/FEATURES.md` 与 `canonical_iface.py` **动态构建**，不维护副本，因此不会漂移。
- **3 个 prompts**：`rtl-to-mutation-audit`、`debug-failing-simulation`、
  `analyze-eda-campaign`。把高频标准流程沉淀下来，减少 Agent 侧提示词冗余。

测试新增 4 个用例（resources/prompts 全量渲染、structuredContent 一致性、
协议版本协商），并把两处写死的常量（工具数 13、回落版本 `2024-11-05`）改为引用
注册表/常量 —— 否则每次新增工具或升版都要改测试，容易变成「测试跟着实现走」。

## 4 · 发布流水线 + v0.6.0（P1）

`release.yml`：
- 发布前跑**完整** 6 层门禁（零 EDA 模式），不再只跑 `test_regression.py`；
- 接入 `sync_changelog_index.py --check` 与 `test_mcp_server.py`；
- wheel 冒烟改到**干净 venv**，覆盖 `--version`、`list`、`audit_harness`、
  `serve-mcp` 握手 + `tools/list`。只跑 `fpga-toolbox list` 发现不了
  「`--version` 崩」「安装后缺依赖」这类只在打包后才暴露的问题。

版本号三处同步到 **0.6.0**（`pyproject.toml` / `__init__.py` / MCP `SERVER_VERSION`）。
`SERVER_VERSION` 改为从 `__version__` 派生 —— 否则发版时容易只改前两处，
客户端看到过期版本号。

## 验证

| 项目 | 结果 |
|---|---|
| 门禁（零 EDA，release 口径） | **6/6**，Layer 4 `195/195`（排除 5 个 slang 门控并说明） |
| 门禁（完整 EDA） | **6/6**，Layer 4 `200/200` |
| `audit_harness` | **22/22**（19 → 22，新增 A20/A21/A22） |
| 工具数 | 13 → **17**，registry / MCP tools/list / USER_MANUAL 三处一致 |
| wheel 冒烟（干净 venv） | `--version` ok、`audit_harness 22/22`、MCP 握手 `2025-06-18` + 17 工具 |
| cocotb 后端闭环 | `TESTS=1 PASS=1 FAIL=0` |
| iverilog 后端闭环 | PASS，日志落盘 |
| changelog 自检 | 135 文件、编号唯一、INDEX 完整、摘要同步 |
