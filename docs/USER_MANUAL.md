# `fpga_toolbox` 完整使用说明书、设计原理与实战示例

> 版本：`v0.5.0+`（13 个注册原子工具 · 12 类总线协议 · 100% 函数测试覆盖率 · Verilator 5.054 + slang 12.2 + cocotb 2.1.0 + Synopsys VCS/DC/PT 自动化）

---

## 1. 设计原理与核心架构

`fpga_toolbox` 的核心设计思想是 **“确定性验证流水线（Deterministic Verification Pipeline）”**——把 RTL 静态分析、契约校验、测试台生成、仿真防呆、变异杀伤与可视化审计拆解为 **13 个无状态原子工具**，所有工具统一返回结构化信封：

```json
{
  "ok": true,
  "tool": "<tool_name>",
  "data": { ... },
  "diagnostics": [ { "severity": "WARNING|ERROR", "code": "W013|E040", "path": "...", "message": "..." } ],
  "error": null
}
```

### 四大核心机制原理

1. **双后端 AST 解析（`core/sv_parser.py` + `iface_extractor.py`）**：
   - 优先调用 `slang 12.2` 获取工业级 IEEE 1800 SystemVerilog AST（自动处理跨文件 `import pkg::*;`、宏与参数求值）；无 `slang` 时显式降级到内置词法/递归下降解析器（支持 ANSI 与 Verilog-1995 非 ANSI 模块，并发出 `W013` 告警，绝不静默降级）。
   - 按「标准接口 + 绑定层」匹配 12 类总线协议（`axi4_lite`、`axi4`、`axi4_stream`、`apb`、`ahb_lite`、`ddr3`、`ddr4`、`ddr5`、`hbm`、`pcie`、`spi`、`ethernet`），输出规范端口映射 `port_map`、真实方向/位宽表 `port_details`、以及双向拆分的 `split_port_map`。命名风格（`i_psel` / `psel_i` / `p_sel` / `s0_psel` / `PSel` / `mem_ddr3_dq_out`）由绑定层统一处理，详见 `FEATURES.md` 的「标准接口 + 绑定层」一节。
2. **双路径测试台生成（`tb_emitter.py` + `protocol_tb_emitter.py`）**：
   - **契约路径（AXI4-Lite / 双侧 AXI4-Lite↔APB 桥）**：由 YAML/JSON 寄存器契约编译为不可变 `CompiledUnitIR`，生成隔离 wrapper、SVA 协议检查器（含 `[SVA:HBEAT]` 心跳防静默通过）、bind 文件、cocotb 测试台、自包含 `Makefile`；检测到双侧 APB master 时自动生成 `{mod}_apb_slave_model.sv` 彻底消除 `WIDTHEXPAND` 与 `P_003` 超时。
   - **多协议自动路径（`protocol="auto"`）**：直接从 `slang` AST 生成显式端口连接（无 `.*` 隐式编译错）、位宽自适应、复位极性自适应（兼容高/低有效复位）的自检 SV 测试台与增量编译 Verilator 运行脚本（PCIe 自动生成 Gen3/4/5 × x1/x4/x16 共 9 档）。
3. **五层防“假通过”守卫（`tb_linter.py` + `sim_runner.py` + `result_normalizer.py` + `mutation_runner.py`）**：
   - `lint_tb` 拦截时钟/复位错误（`C1/C2`）、单边握手死等（`R2/R3/R4`）与静默通过（`S1–S4`）。
   - `run_sim` 强制 `stdbuf -oL -eL` 保日志，校验每个命名套件的身份（`suite_identity`），拒绝截断日志与缺失汇总（`VERILATOR_NO_SUMMARY` / `COCOTB_INCOMPLETE`），并校验波形新鲜度（`E024` / `W014`）。
   - `run_mutation` 真实修改 DUT 源码 → 触发重编译与回归 → 在 `try/finally` 中逐字节恢复（兼容非 UTF-8 编码）并检查 `E030` 残余变异。
4. **Synopsys 商用签核桥接（`eda_campaign.py`）**：
   - 内置 VCS/UVM 1.2 黄金正则解析与旧 `simv` 拦截（`E050`）、DC K-2015.06 多时钟组最差 WNS/总违例汇总及 `UID-423`/`UID-119`/`UID-530` 陷阱拦截（`E046`–`E049`）、PrimeTime 参数化黑盒清理（支持 Verilog 转义标识符如 `\u_rd/0` 并逐根输出标量 `assign = 1'b0;`）。

---

## 2. 安装与环境准备

```bash
# 1. 进入仓库目录（Python >= 3.10，依赖 PyYAML）
cd fpga_toolbox-main
export PYTHONPATH="$(pwd):$PYTHONPATH"

# 2. （可选）挂载离线/本地 EDA 工具链
export PATH="$HOME/.local/verilator/bin:$HOME/.local/bin:$PATH"

# 3. 确认工具注册表与环境探活
python3 -m fpga_toolbox list
python3 -m fpga_toolbox audit_harness
```

---

## 3. 13 个注册原子工具使用说明

支持三种调用方式：
- **统一 CLI**：`python3 -m fpga_toolbox <tool_name> --param value`（object/array 参数支持 `@file.json` 或行内 JSON）
- **单文件直跑**：`python3 fpga_toolbox/<module>.py --param value`
- **Python / MCP 调用**：`from fpga_toolbox.registry import call_tool; call_tool("<tool_name>", {...})`

| # | 工具名 | 核心参数 | 作用与典型输出 |
|---|---|---|---|
| 1 | `index_repo` | `--project-root` | 扫描全仓 SV/V 模块依赖图，标记未知实例化（`W102`） |
| 2 | `resolve_deps` | `--project-root --top-module` | 计算顶层模块编译依赖闭包与排序文件清单 |
| 3 | `copy_sandbox` | `--project-root --relative-files --sandbox-dir` | 带符号链接逃逸拦截与安全标记校验的沙箱隔离拷贝 |
| 4 | `extract_interfaces` | `--rtl-file [--backend auto\|slang\|builtin] [--emit-draft-yaml]` | 提取时钟/复位/12 类总线接口、真实方向与位宽（`port_details`） |
| 5 | `validate_contract` | `--spec-path [--rtl-file]` | 校验寄存器契约（含位域重叠 `E013`、越界 `E014`） |
| 6 | `emit_testbench` | `--out-dir [--spec-path] [--rtl-file] [--protocol auto\|apb\|axi4\|...]` | 生成 cocotb/SVA/Makefile 或多协议自检 SV 测试台 |
| 7 | `lint_tb` | `--target-path` | 静态扫描测试台时钟、握手与静默通过缺陷（`C/R/S` 规则） |
| 8 | `run_sim` | `--sim-script [--suite] [--expected-suites] [--waveform] [--require-waveform]` | 运行仿真、持久化全量日志、核验套件身份与波形新鲜度 |
| 9 | `run_mutation` | `--workdir --mutation-spec --test-cmd [--min-score 80]` | 注入真实 RTL 变异、统计杀伤率并保证 `try/finally` 恢复 |
| 10 | `normalize_sim_result` | `--module-name [--cocotb-results-json] [--verilator-log-path] [--sva-log-path]` | 归一化所有仿真日志与逐笔 `[CHECK]` 为统一 JSON 报告 |
| 11 | `render_visual_report` | `--report --dep-graph --extracted-interfaces --out-html-path` | 生成带 SV 语法高亮、方向/位宽标签与分项目标签页的离线 HTML+SVG |
| 12 | `clean_pt_netlist` | `--in-verilog --out-verilog [--blackbox-modules]` | 清理 DC 网表中的参数化黑盒并插入标量 `assign = 1'b0;` 供 PT 读取 |
| 13 | `audit_harness` | `[--strict]` | 运行 19 项故障注入自审计，验证工具链所有护栏未被破坏 |

---

## 4. 实战使用示例（Examples）

### 示例 1：一键提取任意 RTL 的全部接口、方向与位宽

```bash
python3 -m fpga_toolbox extract_interfaces \
  --rtl-file examples/multi_bus_ref/omni_hub/soc_omni_bus_hub.sv
```

一次识别出该模块共存的 6 套标准接口（`spi`、`apb`、`axi4`、`ethernet`、`pcie`、`ddr4`）及每个端口的 `direction` 与 `width`。

### 示例 2：多协议自动生成测试台 → 静态 Lint → Verilator 实跑（含波形）

```bash
# 1) 自动生成 AXI4-Full 测试台与运行脚本
python3 -m fpga_toolbox emit_testbench \
  --rtl-file examples/multi_bus_ref/axi4_full_slave.sv \
  --out-dir /tmp/tb_axi4 \
  --protocol auto

# 2) 静态检查生成的测试台（期望 0 error）
python3 -m fpga_toolbox lint_tb --target-path /tmp/tb_axi4

# 3) 运行仿真并强制生成新鲜 VCD 波形
python3 -m fpga_toolbox run_sim \
  --sim-script /tmp/tb_axi4/run_axi4_full_slave_axi4.sh \
  --suite axi4 --expected-suites '["axi4"]' \
  --waveform --require-waveform
```

### 示例 3：双侧 AXI4-Lite ↔ APB 桥（契约路径 + 自动 APB 从设备模型 + Makefile）

```python
from fpga_toolbox.registry import call_tool

# 1. 从桥 RTL 生成草稿契约 YAML
call_tool("extract_interfaces", {
    "rtl_file": "examples/axi_apb_ref/axi/axi_apb_bridge.sv",
    "emit_draft_yaml": "/tmp/bridge_spec.yaml",
})

# 2. 生成隔离 wrapper + apb_slave_model.sv + SVA + cocotb TB + Makefile
res = call_tool("emit_testbench", {
    "spec_path": "/tmp/bridge_spec.yaml",
    "rtl_file": "examples/axi_apb_ref/axi/axi_apb_bridge.sv",
    "out_dir": "/tmp/bridge_tb",
})
print(res["data"]["artifacts"])
# 进入 /tmp/bridge_tb 直接执行 `make` 即可跑通 cocotb + Verilator 仿真
```

### 示例 4：Synopsys DC/PT 论文复跑与黑盒网表清理

```python
from fpga_toolbox.eda_campaign import parse_uvm_log, parse_dc_pt_qor
from fpga_toolbox.registry import call_tool

# 1. 清理 DC 网表中的 RDBUS/CLKCROSS* 黑盒供 PrimeTime link_design
call_tool("clean_pt_netlist", {
    "in_verilog": "/dev/shm/base/DLR_TOP.v",
    "out_verilog": "/dev/shm/base/DLR_TOP_clean.v",
})

# 2. 解析 DC/PT 多时钟组 QoR（跨组取最差 WNS 并拦截 UID-423/119/530）
qor = parse_dc_pt_qor(open("/dev/shm/base/report/qor.rep").read())
assert qor["ok"] and qor["data"]["timing_clean"]

# 3. 解析 VCS/UVM 黄金日志（自动拦截旧 simv 截断日志 E050）
uvm = parse_uvm_log(open("/tmp/run.log").read(), expected_frames=10000)
assert uvm["ok"] and uvm["data"]["passed"]
```

### 示例 5：一键跑全套参考仿真与仓库全部测试

```bash
# Python 全量门禁（100% 函数覆盖率）
ruff check fpga_toolbox tests
python3 tests/test_regression.py
python3 tests/test_fusion_and_cross_repo.py
python3 tests/test_eda_campaign.py

# 按领域拆分的回归套件（也可单独跑出问题的那个领域）
python3 tests/test_sv_parser_coverage.py          # SV 解析器双后端
python3 tests/test_protocol_emitter_coverage.py  # 7 协议 TB 模板
python3 tests/test_iface_binding_coverage.py     # 接口识别 + canonical 绑定
python3 tests/test_sim_and_normalizer_coverage.py # 仿真结果与伪 PASS 拦截
python3 tests/test_security_coverage.py          # 路径逃逸 / 注入 / 越界
python3 tests/test_repo_and_sandbox_coverage.py  # 仓库索引 / 沙箱原子性
python3 tests/test_tooling_coverage.py           # CLI / registry / 可视化 / 门禁

# Verilator 实时多协议参考仿真（34 套仿真）
bash examples/multi_bus_ref/sim.sh all
bash examples/multi_bus_ref/complex_datapath/sim_complex.sh all
bash examples/multi_bus_ref/soc_fabric/sim_soc.sh all
bash examples/multi_bus_ref/full_conversion_suite/sim_full_conv.sh all
bash examples/multi_bus_ref/omni_hub/sim_omni.sh all
```

---

## 5. 常见错误码速查

> 下表只列**最常遇到**的码。完整诊断码总表（含 `E000`–`E051`、`W001`–`W053`
> 全部 40 个码及触发条件）见 [`FEATURES.md`](FEATURES.md) 第 4 节。

| 错误码 | 触发模块 | 含义与处理 |
|---|---|---|
| `W013` | `iface_extractor` | `slang` 不可用或解析失败，已显式降级内置解析器 |
| `W014` / `E024` | `sim_runner` | 开启 `--waveform` 但未产出新波形（`require_waveform` 时升为错误） |
| `W102` | `repo_indexer` | 检测到未定义子模块实例化（闭包不完整） |
| `E013` / `E014` | `validate_contract` | 寄存器位域重叠或超出数据总线位宽 |
| `E023` | `run_sim` | 期望套件缺失（静默跳过）或仿真失败 |
| `E030` / `E031` | `run_mutation` | 检测到未恢复的残余变异 / 变异杀伤得分低于阈值 |
| `E050` | `eda_campaign` | UVM 日志截断或缺失 `UVM_ERROR :`（疑似拿错旧 `simv`） |
| `E046`–`E049` | `eda_campaign` | DC K-2015.06 `UID-423`（假成功）/ `UID-119` / `UID-530` / `Signal 15` |
