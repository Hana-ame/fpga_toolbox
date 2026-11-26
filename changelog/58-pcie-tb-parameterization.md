# Changelog — PCIe TB 模板参数化/位宽必须跟随 DUT（防「产出编译不过的 TB 却报 ok」）

## 背景

`emit_testbench(protocol="pcie")` 从 `extract_interfaces` 拿模块信息生成自检 testbench。
旧模板把三件事写死在模板字符串里，只要 DUT 与「示例 RTL」不完全同形，就产出一份
**Verilator 编译不过**的 TB，而工具照旧返回 `ok=True` + 9 个 suite。

## 为什么是真缺陷

三个同根变体，全部实测复现（工具 `ok=True`，Verilator 直接 `%Error` 非零退出）：

### 变体 1：DUT 未声明 `GEN` / `LANES` / `DW`

固定配置的 PCIe endpoint 非常常见——`pcie_cfg_speed` 直接 `assign 4'd3`，位宽写死 64。
旧模板无条件生成：

```
pcie_fixed_ep #(.GEN(GEN), .LANES(LANES), .DW(DW)) dut (
```

Verilator 报 3 条 `%Error-PINNOTFOUND: Parameter not found: 'GEN' / 'LANES' / 'DW'`，
`rc=1`，9 个 suite 全部编译失败 —— **DUT 一点没验到**，但调用方看到 `ok=True` 和 9 个
「可运行」suite。

```
%Error-PINNOTFOUND: tb_pcie_fixed_ep_pcie.sv:20:20: Parameter not found: 'GEN'
%Error-PINNOTFOUND: tb_pcie_fixed_ep_pcie.sv:20:31: Parameter not found: 'LANES'
%Error-PINNOTFOUND: tb_pcie_fixed_ep_pcie.sv:20:46: Parameter not found: 'DW'
%Error: Exiting due to 3 error(s)
```

### 变体 2：DUT 有 `GEN`/`LANES` 但没有 `DW`

`DW` 过去恒由 `LANES` 推导（`LANES == 16 ? 512 : (LANES == 4 ? 128 : 64)`），模板无条件覆盖它。DUT 只声明
`GEN`/`LANES`、`tdata` 位宽固定 64 时，同样报 `Parameter not found: 'DW'`。

### 变体 3：`pcie_cfg_speed` / `pcie_cfg_lanes` 不是 4/6 位

TB 网线写死 `logic [3:0] pcie_cfg_speed` / `logic [5:0] pcie_cfg_lanes`。DUT 用 8 位
cfg 端口（链路能力字段本来就该按实现放宽）时生成：

```
%Warning-WIDTHEXPAND: tb.sv:26:35: Output port connection 'pcie_cfg_speed'
  expects 8 bits on the pin connection, but ... generates 4 bits.
%Warning-WIDTHEXPAND: tb.sv:26:68: ... 'pcie_cfg_lanes' ... generates 6 bits.
%Error: Exiting due to 2 warning(s), 1 error(s)
```

Verilator 默认**警告即错误**，构建仍然失败 —— 而工具已报 `ok=True`。

## 修复

`fpga_toolbox/protocol_tb_emitter.py`：

- `_emit_pcie_tb` 改为接收 DUT 真实的 `parameters` 与 `port_widths`（由
  `parse_sv_file` 在生成前解析拿到，与既有的「缺端口/方向」校验复用同一次解析）。
- 参数覆盖只对 **DUT 真实声明**的参数发出：`.GEN(GEN)` / `.LANES(LANES)` / `.DW(DW)`
  逐个判断，没有就完全不发（`dut (` 而非 `dut #(...) (`）。
- TB 网线位宽取 RTL 真实端口位宽：`tdata` → 真实位宽，`cfg_speed`/`cfg_lanes` →
  各自真实位宽。`DW` 在 DUT 未声明该参数时退化为 `localparam int DW = <真实位宽>`。
- 只有 `GEN`+`LANES` 都声明时才跑 9 配置矩阵；且仅当 `DW` 确实把 `LANES` 映射到
  `tdata` 位宽（默认 `LANES` 的推导值 == 真实端口位宽）时才一并覆盖 `DW`。
- 固定配置 DUT 退化为单 suite `pcie`，且 cfg 断言从「等于具体 Gen/lane」退化为
  「合法区间/合法集合」检查（链路没起来、Gen=0、lanes 非 1/4/16 仍是确定错误）。
- `_emit_run_script` 的 suite 派发与矩阵一致性挂钩：固定配置**不再**给 verilator 传
  `-GGEN/-GLANES`（对无参数设计传 `-G` 会得到
  `Parameters from the command line were not found in the design`）。

## 验证

四个 DUT 形态实测跑通（Verilator 5.054，全部 suite `rc=0`）：

| DUT 形态 | suites | 修复前 | 修复后 |
|---|---|---|---|
| 示例 `pcie_tlp_ep`（GEN/LANES/DW 齐全） | 9 | 9/9 PASS | 9/9 PASS |
| 固定配置（无任何 parameter） | 1 | 3× PINNOTFOUND 全失败 | PASS |
| 有 GEN/LANES、无 DW | 9 | PINNOTFOUND: 'DW' | 9/9 PASS |
| cfg 端口 8 位 | 9 | 2× WIDTHEXPAND 失败 | 9/9 PASS |

回归：`tests/test_full_coverage.py::test_pcie_tb_matches_dut_parameterization`
锁死三个变体（固定配置零参数覆盖 + 零 `-G` 传参；有 GEN/LANES 无 DW 时矩阵保留但
不覆盖 DW；8 位 cfg 时网线跟着 8 位）。

完整验证：4 个测试套件全过 / ruff 0 错（0.17.6 本地非系统）/ `verify_invariants --fast`
5 层全过 / docs HTML 同步。
