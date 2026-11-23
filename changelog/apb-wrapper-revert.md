# Changelog — APB wrapper 宽度回退（Unreleased）

## Summary
尝试修 `emit_testbench` 生成的 APB obs_ 探针/tie_off 位宽（pstrb=4、prdata=32），
连续多次失败并引入 PINMISSING 回归（wrapper 无法编译）。最终回退到稳定状态，
WIDTHEXPAND（4 处）作为已知限制保留。

## Attempted Fix
- `apb_dirs` 从只存 direction 改为存 `(direction, width)`
- APB 端口的 obs_ 探针和 tie_off 用该端口实际宽度（paddr/pwdata/prdata=32，
  pstrb=4，其余=1）

## Why Reverted
每次改动 emit_testbench 生成的嵌套 f-string 都极易出错（连续 5+ 次失败），
每次修改都引入 PINMISSING 回归（wrapper 无法编译，11 个 PINMISSING）。

## Known Limitation
WIDTHEXPAND（4 处：pstrb 期望 4 位但 obs_ 探针 1 位、prdata 期望 32 位但
tie_off 1'h0）是 `emit_testbench` 对 AXI↔APB 桥（双侧设备）的设计范围限制，
非简单 bug。完整修复需重新设计 APB 侧支持（生成 APB slave 模型驱动），
超出 `emit_testbench` 当前设计范围。

## Reverted To
`483d8d3`（CI 全绿、wrapper 能编译、PINMISSING=0）
