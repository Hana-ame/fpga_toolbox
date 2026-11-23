# Changelog 索引

按迭代阶段拆分为独立变更记录，避免单文件臃肿：

- `01-v0.5.0-fusion.md`：双仓融合基线（12 原子工具、参考靶场、协议编辑器与对比报告）
- `02-independent-audit-and-third-party.md`：外部独立审计、第二轮实机验证与第三方仓库实测修复
- `03-dual-side-bridge-support.md`：`emit_testbench` 双侧桥 APB 从机模型重构、零 `WIDTHEXPAND` 与 `cocotb + Verilator` 端到端通过
- [04-multi-bus-hbm-pcie-ddr-ahb.md](04-multi-bus-hbm-pcie-ddr-ahb.md) — Native AHB-Lite / DDR3 / DDR4 / HBM / PCIe Gen3·4·5 (×1/×4/×16) classifiers, simulatable SV benchmark, and 7-stage live Verilator + mutation verification
- [05-multi-protocol-emitter-and-pcie-3x3.md](05-multi-protocol-emitter-and-pcie-3x3.md) — Multi-protocol `emit_testbench` (APB/AXI4/AHB/DDR3/DDR4/HBM/PCIe 3×3), native `[CHECK]` parsing, and per-project tabbed interface report pages
- [06-complex-dual-if-repack-ooo-loopback.md](06-complex-dual-if-repack-ooo-loopback.md) — Dual-interface translation (AXI4-64↔APB32), AXI4 OoO/same-ID fabric, PCIe 3DW/4DW/Split-CplD loopback, 256B golden scoreboards, and 3/3 middle-logic mutation kills
- [07-soc-fabric-spi-eth-pm-an-ltssm-mem.md](07-soc-fabric-spi-eth-pm-an-ltssm-mem.md) — SPI/Ethernet classifiers, bidirectional conversions (SPI↔APB, ETH↔AXI4, APB+PCIe, AXI4→DDR), POR/negotiation-fail/sleep handshakes, built-in shared MEM, and 12/12 (100%) RTL mutation kills
- [08-full-conversion-matrix-buf-cycs-syntax-color.md](08-full-conversion-matrix-buf-cycs-syntax-color.md) — Real direction/width pills (zero `-`), SV syntax color highlighting, configurable `BUF_CYCS=0/2/4` bridges (`APB→AXI4`, `PCIe→AXI4`, `AXI4→DDR3`), and full 256B sweeps (97 Verilator + 7 cocotb checks)
- [09-unified-omni-bus-hub-hotplug-buf.md](09-unified-omni-bus-hub-hotplug-buf.md) — Unified 6-IF all-directional hub (`soc_omni_bus_hub.sv`), Ethernet/PCIe hot-plug disconnect/reconnect, tri-domain sleep/wake, `BUF_CYCS=0/2/4`, 115 Verilator + 7 cocotb checks, and 15/15 (100%) RTL mutations

## eda_campaign 集成

- [10-synopsys-vcs-dc-pt-campaign-integration.md](10-synopsys-vcs-dc-pt-campaign-integration.md) — Synopsys VCS/UVM `parse_uvm_log`、DC/PT `parse_dc_pt_qor`、PT 黑盒网表清理与远端 `csh`/`/dev/shm` 启动器。⚠ 诊断码已从 `E040–E045` 平移为 `E046–E050`（`E040` 被 `audit_harness` 占用）

## eda_campaign 审计

- [20-eda-campaign-audit.md](20-eda-campaign-audit.md) — 审计 `clean_netlist_for_pt` 发现 2 个真实缺陷：参数化 `#(...)` blackbox 实例完全不被清理（`removed=0`）、注释中的 `endmodule` 导致成块扫描错位并往注释里注入 assign。附一次自我纠偏：位选择不应被合并成根 net
- [21-eda-campaign-audit-2.md](21-eda-campaign-audit-2.md) — 第二轮审计剩余 5 个函数：`generate_remote_run_script` **命令注入**（实测可执行任意命令）、`aggregate_seed_medians` 传错层级静默返回空、配对基线用不完整数据判「逐位一致」
- [22-eda-campaign-audit-3.md](22-eda-campaign-audit-3.md) — 第三轮审 `parse_uvm_log` 正则层：千分位 `10,000` 被截成 `10`（**可致假通过**）、`delivered=` 依赖字段顺序

## 集成层修复

- [23-clean-pt-netlist-tool-registration.md](23-clean-pt-netlist-tool-registration.md) — `tool_clean_pt_netlist` 是 ToolResult 信封却不注册（CLI/MCP 都调不到）；README 仍称 `eda_campaign`「尚未实现」；顺带把硬编码 `tools==12` 断言改为下限
- [24-pt-netlist-residual-guard.md](24-pt-netlist-residual-guard.md) — `clean_pt_netlist` 只检查「有没有清理动作」不检查「清理是否生效」，缺分号网表静默产出仍含黑盒的坏网表；新增产物自检 + `E051` + A10 自审计项

## 跨工具一致性

- [25-call-tool-required-and-outdir-cwd.md](25-call-tool-required-and-outdir-cwd.md) — 跨工具 schema↔签名扫描发现：`out_dir` 为空时 4 个产物静默写进调用方 cwd；根因是 `call_tool` 从不校验 schema 的 `required`
- [26-call-tool-enum-and-type-validation.md](26-call-tool-enum-and-type-validation.md) — `call_tool` 也不校验 enum 与 type：非法协议名报错指向 RTL，「string 传 int」漏出 CPython 内部错误；同时保留数字字符串兼容（MCP 序列化）

## 输出侧一致性

- [27-sva-heartbeat-anti-silent-pass.md](27-sva-heartbeat-anti-silent-pass.md) — SVA checker 通过时零输出，导致「全部通过」与「checker 没跑」都归一化成 `NO_RESULTS`；加心跳探针使三态可区分
- [28-cocotb-incomplete-report.md](28-cocotb-incomplete-report.md) — cocotb 每记录一条 check 就落盘却无「已跑完」信号，测试崩在第 1 条断言时残缺报告仍判 PASS；加 `completed` 标记 + `COCOTB_INCOMPLETE`
- [29-verilator-missing-summary.md](29-verilator-missing-summary.md) — Verilator 套件在 CHECK 之后崩、无 `PASS:`/`FAIL:` 汇总行时仍被判 PASS；改用「启动套件数 vs 汇总行数」计数校验（逐名匹配会误伤 `PASS: all 20 ...` 这类日志）

## 输入侧一致性

- [30-lint-tb-sv-silent-pass.md](30-lint-tb-sv-silent-pass.md) — `scan_silent_pass` 只被喂 `.sh`/`.py`，SV 测试台从不进入该扫描器，于是 README 头号宣传场景（TB 只 `$display("DONE")` 就结束、无任何判定）返回 `ok=True`；新增 `S4` 规则 + 自审计 `A11`

## 文档交付

- [31-docs-html-derivation.md](31-docs-html-derivation.md) — 文档 HTML 版定位为派生产物（markdown 唯一事实源）：写派生器时踩到 3 个真实坑——`<strong>` 被转义成可见文本、把 `.md` 无脑改 `.html` 造成 3 个死链、页面出现两个 `<h1>`；附产物自检（死链/锚点/转义泄漏）与 16 项回归断言

## 合并期缺陷修复（multi-bus-pass 合并审查）

- [10-pure-apb-draft.md](10-pure-apb-draft.md) — `extract_interfaces --emit-draft-yaml` 曾对纯 APB 设备写入空 `axi4_lite` 段，导致自家 validator 报 17 条 E001；仅在有 AXI 接口（或两种都没有）时写出
- [11-sandbox-symlink-escape.md](11-sandbox-symlink-escape.md) — `copy_sandbox` 曾跟随项目内符号链接把项目外文件拷进沙箱（隔离泄漏）；补 `resolve(strict=True)` 真实路径校验
- [12-unknown-instantiation-w102.md](12-unknown-instantiation-w102.md) — `index_repo` 曾静默丢弃仓库外模块的例化；改用 slang CST 的 `HierarchyInstantiation` 精确检出并报 `W102`，`resolve_deps` 透传 `closure_is_complete`
- [13-windows-decoding-and-process-lessons.md](13-windows-decoding-and-process-lessons.md) — Windows cp1252 解码崩溃（`subprocess text=True` → `UnicodeDecodeError` → `AttributeError`），以及三个开发过程错误（stash 致代码漏提交、脆断言、测试依赖 CI 缺失环境）
- [14-multi-bus-tb-port-guard.md](14-multi-bus-tb-port-guard.md) — 多总线 TB 生成器未校验必需端口，对 APB3 风格 DUT 产出 `PINNOTFOUND` 坏 TB 却报 `ok=True`；新增 `REQUIRED_TB_PORTS` 生成前校验
- [15-ddr-hbm-pcie-port-guard.md](15-ddr-hbm-pcie-port-guard.md) — DDR3/DDR4/HBM/PCIe 模板用 `.*` 兜底，DUT 端口名不匹配（如三态 `inout ddr3_dq`）时生成 `Can't find definition of variable` 坏 TB 却报 `ok=True`；补齐 4 协议端口校验
- [16-port-direction-guard.md](16-port-direction-guard.md) — 端口方向反向（如 `pready` 写成 input）时 TB 的握手等待永久挂起，工具却报 `ok=True`；新增 `EXPECTED_TB_DIR` 方向校验
- [17-tb-vectors-scale-with-width.md](17-tb-vectors-scale-with-width.md) — APB 测试向量写死 32 位字面量，窄总线上 `WIDTHDIGITS` 截断、断言值失真；按 `dw`/`aw` 缩放值与十六进制位数，并保留部分写语义
- [18-emitter-robustness-matrix.md](18-emitter-robustness-matrix.md) — 用自动畸形矩阵扫出 3 个缺陷：缺 reset 时硬连 `rst_n`（PINNOTFOUND）、额外端口触发 PINMISSING 阻断构建、多模块文件盲取 `mods[0]`
- [19-wildcard-to-explicit-connections.md](19-wildcard-to-explicit-connections.md) — 6 协议模板的 `.*` 通配在 DUT 有额外端口时报硬错误（`Can't find definition of variable`），上轮的 `lint_off PINMISSING` 无效；改为显式连接，并修 `lint_off` 对多行 module 头不生效
