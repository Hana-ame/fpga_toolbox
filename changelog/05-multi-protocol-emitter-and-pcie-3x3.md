# 05 — Multi-Protocol Testbench Emitter, PCIe 3×3 Matrix, and Per-Project Interface Report Pages

## Summary
Eliminated the `emit_testbench` single-protocol restriction by adding `fpga_toolbox/protocol_tb_emitter.py`, expanded PCIe verification to the full 3×3 matrix (Gen3/Gen4/Gen5 × x1/x4/x16 = 9 suites), distinguished `axi4` from `axi4_lite` in `iface_extractor.py`, and upgraded `visual_renderer.py` to render per-project tabbed interface report pages plus standalone `reports/<project>.html` files.

## Changes
- **Multi-Protocol Emitter (`fpga_toolbox/protocol_tb_emitter.py`, `fpga_toolbox/tb_emitter.py`)**:
  - Added `protocol` dispatch to `tool_emit_testbench(..., protocol="auto")`, generating compilable, `lint_tb`-clean SV testbenches and Verilator run scripts directly from `slang 12.2` AST for `apb`, `axi4`, `ahb_lite`, `ddr3`, `ddr4`, `hbm`, and `pcie` (all 9 Gen3/4/5 × x1/x4/x16 parameter sweeps).
- **AXI4-Full Classification (`fpga_toolbox/iface_extractor.py`)**:
  - Added `_AXI4_BURST_PATS` to promote AXI4 interfaces with burst/ID signals (`awlen`/`arlen`/`wlast`/`rlast`/`awburst`/`arburst`) to `axi4` (29 signals, 0 `out_of_scope`).
- **Per-Project Interface Report Pages (`fpga_toolbox/visual_renderer.py`, `fpga_toolbox/result_normalizer.py`)**:
  - Added native `[CHECK]` per-transaction parsing in `result_normalizer.py` (zero fabricated `cocotb` labels, zero duplicate rows).
  - Upgraded `visual_renderer.py` with pure-CSS per-project tabs (Overview, AXI↔APB Bridge, APB, AXI4-Full, AHB-Lite, DDR3, DDR4, HBM, PCIe 3×3 Grid) and generated standalone HTML pages in `examples/multi_bus_ref/reports/`.

## Live Verification Evidence
- All 7 protocols (15 suites including 9 PCIe 3×3 combinations) auto-emitted via `emit_testbench(protocol="auto")`, passed `lint_tb` (0 errors), and passed live Verilator 5.054 simulation (**45/45 real `[CHECK]` transactions PASS**).
- 5/5 real RTL mutations killed (`100.0%` score, 0 residual mutations).
- `ruff`, `test_regression.py`, `test_fusion_and_cross_repo.py`, and `audit_harness` (9/9) all green.
