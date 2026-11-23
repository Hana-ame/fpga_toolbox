# 04 — Multi-Bus Classifiers & Full Live Verification (AHB-Lite / DDR3 / DDR4 / HBM / PCIe Gen3·4·5 ×1·×4·×16)

## Summary
Extended `fpga_toolbox/iface_extractor.py` to natively classify five high-speed and SoC bus protocols beyond AXI4/AXI4-Lite/APB/AXIS, added a complete simulatable SystemVerilog reference suite in `examples/multi_bus_ref/`, and verified the full 7-stage `fpga_toolbox` pipeline live on Verilator 5.054 + slang 12.2.

## Changes
- **Interface Extractor (`fpga_toolbox/iface_extractor.py`)**:
  - Extended clock/reset recognition (`_CLK_RE` / `_RST_RE`) for AHB-Lite `HCLK` / `HRESETn`.
  - Added native protocol classifiers for `ahb_lite`, `ddr3`, `ddr4`, `hbm`, and `pcie` (extracting `pcie_gen` and `pcie_lanes` from `GEN`/`LANES` parameters).
- **Simulatable Multi-Bus Reference Suite (`examples/multi_bus_ref/`)**:
  - `ahb_lite_slave.sv` + `tb_ahb_lite.sv`: pipelined AHB-Lite slave with RW/RO registers and `HRESP=1` error response on illegal addresses.
  - `ddr3_mem_ctrl.sv` + `tb_ddr3.sv`: DDR3 ACT/WR/RD(CL=2)/PRE command decoder and bank/row/col array.
  - `ddr4_mem_ctrl.sv` + `tb_ddr4.sv`: DDR4 `ACT_n` + `BG`/`BA` addressing and active-low `DM_DBI_n` byte-masked write/readback.
  - `hbm_pc_ctrl.sv` + `tb_hbm.sv`: HBM 256-bit pseudo-channel controller with ID tracking and ready/valid backpressure.
  - `pcie_tlp_ep.sv` + `tb_pcie.sv`: Parameterized PCIe TLP endpoint supporting Gen3×1 (`DW=64`), Gen4×4 (`DW=128`), and Gen5×16 (`DW=512`) MWr/MRd/CplD transactions.
  - `sim.sh` & `mutations.yaml`: Unified Verilator 5.054 runner for all 7 suites (`ahb`, `ddr3`, `ddr4`, `hbm`, `pcie_g3x1`, `pcie_g4x4`, `pcie_g5x16`) and 3 real RTL mutation targets.
- **Regression Tests (`tests/test_fusion_and_cross_repo.py`)**:
  - Added `test_multi_bus_classifiers` verifying `ahb_lite`, `ddr3`, `ddr4`, `hbm`, and `pcie` extraction.

## Live Verification Evidence
- `index_repo`: 10 modules indexed (`ok=True`).
- `extract_interfaces` (`slang 12.2`): 5/5 DUTs classified with exact clocks, resets, and PCIe Gen/Lanes metadata.
- `lint_tb`: 0 errors across all 5 SV testbenches.
- `run_sim` (`Verilator 5.054 + slang 12.2`): 7/7 suites PASS (`all_verified=True`).
- `run_mutation`: 3/3 RTL mutations killed (100% score, 0 residual mutations).
- `render_visual_report`: `multi_bus_verification_report.html` generated (`consistency_ok=True`).
