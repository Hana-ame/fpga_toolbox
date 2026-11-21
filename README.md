# fpga_toolbox — 面向 AI Agent Harness 的模块化 FPGA 验证工具箱

[![CI](https://github.com/Hana-ame/fpga_toolbox/actions/workflows/ci.yml/badge.svg)](https://github.com/Hana-ame/fpga_toolbox/actions/workflows/ci.yml)
[![Release](https://github.com/Hana-ame/fpga_toolbox/actions/workflows/release.yml/badge.svg)](https://github.com/Hana-ame/fpga_toolbox/actions/workflows/release.yml)

把「读 RTL → 抽接口 → 校验契约 → 生成 testbench → 归一化仿真结果 → 出可视化报告」拆成 8 个原子工具，Python 实现（≥3.10），仅依赖 PyYAML。外部 Harness 或 MCP Server 通过 `registry.py` / `cli.py` 统一调用，所有工具共享同一 `ToolResult` 返回信封（`ok / tool / data / diagnostics / error`）。

## 安装

```bash
pip install git+https://github.com/Hana-ame/fpga_toolbox.git

# 本地开发（含 ruff）
git clone git@github.com:Hana-ame/fpga_toolbox.git
cd fpga_toolbox && pip install -e ".[dev]"
```

## 目录结构

```text
fpga_toolbox/
├── __init__.py                 # 顶层导出 call_tool / list_tool_schemas
├── __main__.py                 # 支持 python -m fpga_toolbox <tool> --参数 值
├── cli.py                      # 命令行入口：子命令由 inputSchema 自动生成
├── registry.py                 # 8 个原子工具注册表 + MCP JSON Schema
├── core/
│   ├── types.py                # ToolResult 信封与 IR 数据类
│   ├── sv_parser.py            # Verilog/SV 词法与递归下降语法解析器
│   └── validator.py            # 边界契约语义检查 + RTL 端口交叉校验
├── repo_indexer.py             # index_repo + resolve_deps（依赖闭包）
├── sandbox_copier.py           # copy_sandbox（保留目录结构 + SHA-256）
├── iface_extractor.py          # extract_interfaces（AXI4-Lite / Stream 抽取）
├── tb_emitter.py               # validate_contract + emit_testbench（代码生成）
├── result_normalizer.py        # normalize_sim_result（JUnit XML / JSON / SVA 日志）
└── visual_renderer.py          # render_visual_report（SVG 拓扑 + HTML 视图）
```

## 工具一览

| 工具 | 输入 | 输出 |
| --- | --- | --- |
| `index_repo` | `project_root` | 模块表、例化关系、头文件索引、参数 |
| `resolve_deps` | `target_module` (+`repo_index`) | BFS 依赖闭包、DAG 边、RTL/header 文件列表 |
| `copy_sandbox` | `project_root` `relative_files` `sandbox_dir` | 隔离副本 + 每文件 SHA-256 前缀 |
| `extract_interfaces` | `rtl_file` (+`module_name` `emit_draft_yaml`) | 协议分类、端口映射、越界端口、可选契约草稿 |
| `validate_contract` | `spec_path` (+`rtl_file`) | 语义诊断（E001–E012）与端口交叉校验结果 |
| `emit_testbench` | `spec_path` `out_dir` | wrapper / SVA checker / bind / cocotb 四个文件 |
| `normalize_sim_result` | `cocotb_results_json` / `sva_log_path` | `VerificationReport` JSON |
| `render_visual_report` | `report` `dep_graph` `extracted_interfaces` | HTML 看板 + 独立 SVG |

## 快速调用

三个层次，按需要选：

```bash
# 1) 包级入口（安装后可直接用 fpga-toolbox）
python3 -m fpga_toolbox list                    # 列出工具
python3 -m fpga_toolbox schemas                 # 导出 MCP / Function-Calling Schema
python3 -m fpga_toolbox index_repo --project-root ./my_soc
python3 -m fpga_toolbox copy_sandbox \
  --project-root ./my_soc \
  --relative-files rtl/a.sv inc/defs.svh \
  --sandbox-dir /tmp/sb --no-clean

# 2) 单文件直跑（工具就在 fpga_toolbox/ 下，调试单个工具时最省事）
python3 fpga_toolbox/iface_extractor.py --rtl-file ./dut.sv --module-name reg_slave
python3 fpga_toolbox/repo_indexer.py --project-root ./my_soc
python3 fpga_toolbox/tb_emitter.py --spec-path contract.yaml                # 只校验
python3 fpga_toolbox/tb_emitter.py --spec-path contract.yaml --out-dir tb/  # 生成

# 3) 复杂对象参数用 @file.json
python3 -m fpga_toolbox render_visual_report \
  --report @report.json --dep-graph @deps.json \
  --extracted-interfaces @ifaces.json --out-html-path out/visual.html
```

参数名同时接受 `--kebab-case` 与 `--snake_case`；布尔参数用 `--clean` / `--no-clean`；失败统一返回退出码 1 并输出带 `error` 的 JSON。

> 0.3.0 起移除了 `python -m fpga_toolbox.cli call <tool> --args '{...}'`，改用上面的子命令写法；依赖旧写法的脚本需要改一行。

作为库使用：

```python
from fpga_toolbox import call_tool, list_tool_schemas
res = call_tool("index_repo", {"project_root": "./my_soc"})
```

## 安全契约

`copy_sandbox` 会写盘并清理目录，因此带以下硬性约束：

- **拒绝**把 `sandbox_dir` 设为工程根目录本身，或其任意祖先目录（防止 `rmtree` 误删源工程）；
- **拒绝**文件系统根目录与 home 目录；
- 清理只针对带 `.fpga_sandbox` 标记的目录；对非空且无标记的目录默认拒绝，需显式 `force=true`；
- 保留相对目录层级，`rtl/a/common.sv` 与 `rtl/b/common.sv` 不会互相覆盖，`include` 相对路径继续有效；
- 拒绝绝对路径与含 `..` 的 `relative_files`。

## 契约示例

```yaml
boundary:
  module_name: reg_slave
  rtl_files: [rtl/reg_slave.sv, inc/defs.svh]
  clock: {port: aclk, freq_mhz: 100.0}
  reset: {port: aresetn, active_low: true, sync: true}
  axi4_lite:
    addr_width: 12
    data_width: 32
    role: slave
    port_map: {awaddr: s_axi_awaddr, awvalid: s_axi_awvalid, /* 19 个信号 */}
  out_of_scope_ports:
    - {port: irq_o, dir: output, width: 1, observe_only: true}
test_plan:
  protocol_rules: {max_resp_cycles: 16, allow_unaligned_addr: false}
  register_rules:
    - {rule_id: REG_001, name: CTRL_REG, offset: 0, reset_value: 0, access: RW}
  illegal_addr_rules:
    - {rule_id: ILL_001, offset: 252, expected_resp: SLVERR}
  stress_profile: {random_backpressure: true, iterations: 32, seed: 20261120}
```

时钟/复位按名称识别，覆盖 `aclk` `clk` `core_clk` `clock`、`aresetn` `resetn` `rst_n` `s_axi_aresetn` `nrst` 等形式，极性由名字推断（`_n` / 尾缀 `n` / `nrst` 视为低有效）；未识别时返回 `W010`/`W011` 警告并回落到 `aclk`/`aresetn`。

## 测试

```bash
pip install pyyaml
python tests/test_regression.py     # 无需 pytest，全部通过时退出码 0
```

覆盖：sandbox 误删防护、同名文件保留、未标记目录拒绝清理、六种复位命名识别与极性、wrapper 端口重复连接、cocotb 文件可编译、非法仿真结果报错、JUnit XML 解析、完整链路，以及 CLI 的三种调用方式（`-m fpga_toolbox` / `tools.py` / `@file.json`）与失败退出码。

## CI / CD

`.github/workflows/ci.yml` — push 到 `main` 或提交 PR 时触发：

| Job | 内容 |
| --- | --- |
| `lint` | `ruff check`（E4/E7/E9/F/I）+ 用 `ast.parse(feature_version=(3,10))` 卡最低版本语法兼容 |
| `test` | 矩阵：Ubuntu 24.04 × Python 3.10、Ubuntu × 3.11/3.13、Windows × 3.13；跑回归、CLI 契约、`pip install .`、入口点 |
| `build` | 构建 sdist + wheel，装 wheel 后校验导入与入口点，产物上传为 artifact |

`.github/workflows/release.yml` — 推 `v*` tag（或手动触发）时触发：校验 tag 与 `__version__` 一致 → 跑回归 → 构建 → 创建 GitHub Release 并上传产物 → 若配置了 `PYPI_API_TOKEN` 则发布到 PyPI，未配置则自动跳过。

发版流程：

```bash
# 1. 改 fpga_toolbox/__init__.py 与 pyproject.toml 里的版本号
# 2. 提交后打 tag 并推送
git tag v0.2.0 && git push origin main --tags
```

## 已知限制

- `test_plan.stress_profile.iterations` / `random_backpressure`、`register_rules.write_mask`、`protocol_rules.allow_unaligned_addr` 目前只做校验，生成器未消费；cocotb 用例只校验复位值与非法地址响应，不含写回读与反压压测。
- SVA 检查器覆盖 VALID 稳定性、复位期间响应、响应超时三类规则；协议完整一致性仍建议配合 VIP 或形式验证。
- 解析器面向可综合 RTL 的常规写法，不处理宏展开、generate 内部的层次化端口推导。
