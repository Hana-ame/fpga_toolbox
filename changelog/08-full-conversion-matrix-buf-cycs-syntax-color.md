# 08 — Real Direction/Width Extraction, SV Syntax Highlighting, Configurable `BUF_CYCS` Bridges & Full 256B Conversion Matrix

## Summary
Fixed the missing direction/width bug in `iface_extractor.py` + `visual_renderer.py`, added zero-JS SystemVerilog syntax color highlighting with line numbers, and implemented configurable `BUF_CYCS=0/2/4` buffered bridges with full 256-byte address-space sweeps across `APB→AXI4`, `PCIe→AXI4`, and `AXI4→DDR3` (completing all 9 requested conversion directions).

## Fixes & Additions
- **Direction & Width Fix (`fpga_toolbox/iface_extractor.py`, `fpga_toolbox/visual_renderer.py`)**:
  - Emitted `port_details` (`port`, `direction`, `width`) from `slang 12.2` AST on every extracted interface; rendered colored `input`/`output` direction pills and exact bit widths (`1-bit`, `4-bit`, `16-bit`, `32-bit`, `64-bit`, `256-bit`, `512-bit`) with automated audit asserting zero `-` placeholders.
- **SystemVerilog Syntax Highlighting (`fpga_toolbox/visual_renderer.py`)**:
  - Added `_highlight_sv()` rendering dark-theme syntax colors (`sv-kw`, `sv-num`, `sv-cmt`, `sv-sys`) with line numbers across all 17 project tabs and standalone reports.
- **Configurable `BUF_CYCS` Buffered Bridges (`examples/multi_bus_ref/full_conversion_suite/`)**:
  - `apb_to_axi4_buf_bridge.sv`, `pcie_to_axi4_buf_bridge.sv`, `axi4_to_ddr3_buf_ctrl.sv` — all verified live across `BUF_CYCS = 0, 2, 4` (9 suites, full 256B/64-word address-space sweeps, POR/LTSSM/L1/Self-Refresh checks, `21/21 PASS`).
