# 03 · 双侧 AXI4-Lite + APB 桥支持与零 WIDTHEXPAND 重构

## [Unreleased] — 双侧设备支持与零 WIDTHEXPAND 重构

### Added & Fixed
- **重新设计 `emit_testbench` 支持双侧 AXI4-Lite + APB 桥设备**（`fpga_toolbox/tb_emitter.py`）：
  - 当 `boundary.apb` 存在且 `role == "master"` 时，自动生成可综合/Verilator `-Wall` 零告警的行为级 APB 从机响应模型 `{mod}_apb_slave_model.sv`（支持可配 `WAIT_CYCLES`、寄存器复位值、`RW/RO/W1C/W1S/RC/RSVD`、`write_mask`/`read_mask` 与 `illegal_addr_rules` → `PSLVERR` 传播）。
  - `{mod}_axil_wrap.sv` 改为按规范位宽（`APB_ADDR_W`、`APB_DATA_W`、`APB_STRB_W`）声明内部 APB 互连并实例化 `u_apb_slv`，彻底消除旧版将 32-bit `PRDATA` 接 `1'b0` 或大小写失配导致的 `WIDTHEXPAND` 与交易挂死问题。
  - 在 `tests/test_fusion_and_cross_repo.py` 新增 `test_dual_side_apb_slave_emitter`（含 Verilator `--lint-only -Wall` 零 `WIDTHEXPAND/WIDTHTRUNC` 门禁）；实机通过 `Verilator 5.054 + cocotb 2.1.0 + cocotbext-axi` 端到端验证 `axi_apb_bridge.sv`（6/6 PASS：复位值、RW 写回读、RO 不可变、非法地址 `SLVERR` 传播与 8 轮随机反压压测）。
- **清理 CI 门禁与路径问题**：修复 4 处 `ruff` 报错、删除根目录残留 `oob.json`/`overlap.json`，并将 `sim_runner.py` 脚本调用统一为 `script_p.resolve()`。
