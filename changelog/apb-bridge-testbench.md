# Changelog — emit_testbench 支持 AXI↔APB 桥（Unreleased）

## Summary
用真实 AXI4-Lite↔APB 桥实测 `emit_testbench` 时发现：生成的 cocotb 测试台
**无法运行**（P_003 RVALID timeout，240ns 失败）。这是 `emit_testbench` 对其
核心用途（AXI↔APB 桥验证）的实际缺口。修复后测试台首次对真正的 AXI↔APB 桥
运行通过。

## Root Cause
生成的 cocotb 测试台只驱动 AXI4-Lite 侧（`AxiLiteMaster`），完全不驱动 APB 侧
—— 而桥的 APB 侧是 master，需要连 APB slave 才能完成事务。APB 侧 input
（pready/prdata/pslverr）被 tie 0，桥发起读后等不到 pready，触发 P_003
RVALID timeout。

## Fixed
- **APB slave 响应器**：`_emit_cocotb` 生成 APB slave 响应器
  （`cocotb.start_soon`），响应桥的 APB 读写（write 存 mem、read 返回 mem、
  拉高 pready）。APB 端口经 wrapper 连到 obs_ 探针，cocotb 从 `dut.obs_*` 驱动。
- **APB obs_ 探针/tie_off 位宽**：改用该端口实际宽度（paddr/pwdata/prdata=32，
  pstrb=4），修 WIDTHEXPAND。
- **APB input 也连 obs_ 探针**：不再 tie_off 常量，cocotb APB slave 响应器能
  驱动它们。

## Result
AXI↔APB 桥的 cocotb 测试台 `test_axil_rules` **PASS**（4470ns 完成，
TESTS=1 PASS=1 FAIL=0）—— `emit_testbench` 生成的测试台首次对真正的 AXI↔APB
桥运行通过。此前所有验证都用纯 APB 寄存器文件，从未真正运行过。
