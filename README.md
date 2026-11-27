# fpga_toolbox v0.5.0 — 融合版 AI Agent FPGA 验证工具箱

将 **`fpga_toolbox`** 的 AI Agent / MCP 控制面与 **`agent/verilog`** 沉淀出的硬件验证与反静默通过护栏融为一体：

- **控制面（零 EDA 强依赖，仅 PyYAML）**：13 个原子工具统一走 `ToolResult(ok / tool / data / diagnostics / error)` 信封，自动导出 MCP JSON Schema 与 CLI 子命令。
- **验证与防假通过护栏**：支持 APB 协议分类、SV `interface/modport` 识别与可选 `slang` AST 后端、位域契约校验（兼容 `apb-regfile-spec/v2`）、三套静态反踩坑 Lint（时钟竞争 / AXI 握手 / 静默通过）、参数化变异测试（`try/finally` 强还原 + 残留拦截）、Verilator ≥ 5.040 封装与故障注入自审计。
- **参考靶场（`examples/axi_apb_ref/`）**：完整保留原 `agent/verilog` 的 AXI4-Lite ↔ APB 串行/并发桥、25 个 Verilator 测试套件、VIP/UVM-lite 与 `regfile_spec.json` 作为跨库集成回归基准。

## 13 个原子工具一览

| # | 工具 | 职责 |
|---|---|---|
| 1 | `index_repo` | 解析 RTL 仓库模块、例化关系与头文件索引 |
| 2 | `resolve_deps` | BFS 计算目标模块传递依赖闭包与 DAG 边 |
| 3 | `copy_sandbox` | 带防误删保护与 SHA-256 校验的沙箱隔离复制 |
| 4 | `extract_interfaces` | 抽取 AXI4-Lite / AXI4-Full / AXI4-Stream / APB / AHB-Lite / DDR3 / DDR4 / DDR5 / HBM / PCIe / SPI / Ethernet / SV `interface` 与时钟复位（支持可选 `slang` AST） |
| 5 | `validate_contract` | 校验 YAML/JSON 边界契约与 `apb-regfile-spec/v2` 位域规则（E000–E015，E015 为位域 bits 顺序反了 hi<lo 的专门提示） |
| 6 | `emit_testbench` | 契约路径：生成 SV wrapper / SVA / bind 与含写回读、反压压测的 cocotb 测试平台；`protocol=` 路径：直接从 RTL 生成自检 SV testbench + Verilator 运行脚本，支持 `apb`/`axi4`/`ahb_lite`/`ddr3`/`ddr4`/`hbm`/`pcie`（PCIe 自动展开 Gen3·4·5 × x1·x4·x16 共 9 套件） |
| 7 | `normalize_sim_result` | 归一化 cocotb XML/JSON、SVA 日志与 Verilator 多套件日志（含逐套件身份核对） |
| 8 | `render_visual_report` | 渲染独立 SVG 拓扑 + HTML 规则审计看板，内置 HTML 标签与行数一致性自检 |
| 9 | `lint_tb` | 静态反踩坑检查：时钟沿竞争（C1–C2）、AXI 握手死锁（R1–R4）、静默通过（S1–S4，含 SV 测试台「只 `$display` 无判定」）|
| 10 | `run_sim` | 封装 Verilator ≥ 5.040 + slang 仿真、`stdbuf` 日志保活与逐套件身份校验 |
| 11 | `run_mutation` | 参数化 RTL 变异测试，含 `try/finally` 强还原与残留变异拦截（E030） |
| 12 | `clean_pt_netlist` | 清理 DC 网表中未解析的参数化黑盒（`RDBUS`/`CLKCROSSBUS`/`CLKCROSSDFF`）并插入标量 tie-off，使 PrimeTime `link_design` 可通过 |
| 13 | `audit_harness` | 故障注入式自审计（A1–A19），证明验证护栏出错时不会静默通过 |

### 多总线原生测试台（`protocol=`）

不需要写契约，直接从 RTL 的 slang AST 生成可编译、自检的 SystemVerilog testbench 与 Verilator 运行脚本：

```bash
python3 -m fpga_toolbox emit_testbench \
  --rtl-file examples/multi_bus_ref/pcie_tlp_ep.sv \
  --out-dir /tmp/tb_pcie --protocol pcie
bash /tmp/tb_pcie/run_pcie_tlp_ep_pcie.sh all     # Gen3/4/5 × x1/x4/x16 共 9 套件
```

`protocol` 支持 `apb` / `axi4` / `ahb_lite` / `ddr3` / `ddr4` / `hbm` / `pcie` / `auto`。
每个生成的 TB 都以 `[CHECK] id=... | proto=... | status=PASS|FAIL` 输出逐笔事务结果，
`normalize_sim_result` 会把这些行解析成 rule 级 check。

`examples/multi_bus_ref/` 是配套靶场：7 类协议 RTL + `complex_datapath/`（AXI4-64↔APB32、
AXI4 OoO 重排、PCIe repack loopback）+ `full_conversion_suite/`（可配 `BUF_CYCS=0/2/4` 的
APB→AXI4 / PCIe→AXI4 / AXI4→DDR3）+ `soc_fabric/`（SPI/ETH/PM/LTSSM）+ `omni_hub/`。

### 诊断码补充

除工具表里的 `E000–E015` / `W001` / `W010–W013` / `W050–W057` / `W101` 外：

- `W102` — `index_repo` 检出「形态上是例化、但仓库内无定义」的模块（厂商 IP / 加密 IP /
  未克隆子模块）。此时 `resolve_deps` 会返回 `closure_is_complete: false`，明确告知依赖
  闭包不完整，不要把该文件清单直接当作可编译沙箱。

**`eda_campaign`（Synopsys VCS/DC/PT 论文复跑集成）专用码 `E046–E050`**：

| 码 | 含义 |
|---|---|
| `E046` | DC K-2015.06 `UID-423`，或输出 <700B 且仅含 `No paths.` 的假成功报告 |
| `E047` | `set_max_fanout` 施加在 net 对象报 `UID-119` |
| `E048` | K-2015.06 调用 `write_db` / `-format db` 报 `UID-530` |
| `E049` | `Received Signal 15` 或 `No space left on device` |
| `E050` | 旧 `simv` / 截断日志（少于 3 行或缺 `UVM_ERROR :` 汇总）|
| `E051` | `clean_pt_netlist` 清理后输出网表仍残留黑盒实例（实例语法未被匹配，PT 仍会 link 失败）|

> 注：这批码原设计用 `E040–E045`，但 `E040` 已被 `audit_harness` 占用（自审计护栏失效），
> 故整体平移至空闲的 `E046–E050`，避免同一码两种语义。

## 快速验证与测试

```bash
pip install pyyaml
python3 tests/test_regression.py
python3 tests/test_fusion_and_cross_repo.py
python3 -m fpga_toolbox audit_harness
python3 -m fpga_toolbox list
```

## 离线文档

### 功能手册与示例

Markdown 是唯一事实源，HTML 由它派生（带侧栏目录 + 页内搜索，完全自包含、
双击即可离线打开）。

| Markdown | HTML | 内容 |
|---|---|---|
| [`docs/FEATURES.md`](docs/FEATURES.md) | [`docs/FEATURES.html`](docs/FEATURES.html) | **功能手册**：13 个工具的用途、参数与真实输出，诊断码总表，三种调用方式，边界与已知限制 |
| [`docs/EXAMPLES.md`](docs/EXAMPLES.md) | [`docs/EXAMPLES.html`](docs/EXAMPLES.html) | **示例集**：10 个可直接运行的端到端示例（契约生成链、多协议 TB、静默通过检测、变异测试、三源日志归一化、PT 网表清理、自审计、沙箱逃逸） |

重新生成 HTML（改了 md 之后必须执行，CI 会校验同步）：

```bash
python3 docs/build_docs_html.py           # 生成
python3 docs/build_docs_html.py --check   # 校验是否同步
```

### EDA 工具资料

`docs/eda-tutorials/` 存放可直接用浏览器打开的离线资料（纯文档，不含代码）：

| 文件 | 内容 |
|---|---|
| [`eda-tools-dark-guide.html`](docs/eda-tutorials/eda-tools-dark-guide.html) | Synopsys 六大 EDA 工具的指令、Flag、参数范围与错误码参考（夜间版） |
| [`paper-repro-guide.html`](docs/eda-tutorials/paper-repro-guide.html) | Paper 1 (DLR-MAC) / Paper 2 (RS+GBN) 实验复跑教程解读 |
| [`multi_bus_verification_report.html`](docs/eda-tutorials/multi_bus_verification_report.html) | 六接口全向核心（`examples/multi_bus_ref/omni_hub/`）的验证审计报告 |
| [`EXPERIMENT_REPRO_GUIDE.md`](docs/eda-tutorials/EXPERIMENT_REPRO_GUIDE.md) | Synopsys VCS/DC/PT 集成方案（对应 `fpga_toolbox.eda_campaign` 库模块，已实现并接入 CI） |

另见 [`docs/TEST_REPORT_AND_PLAN.md`](docs/TEST_REPORT_AND_PLAN.md) 与
[`docs/verification-schemes-report.html`](docs/verification-schemes-report.html)。
