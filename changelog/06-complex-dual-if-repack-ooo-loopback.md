# 06 — Dual-Interface Complex Datapath Verification (Translation, Out-of-Order, Multi-Format Repack & Golden Scoreboard)

## Summary
Added three non-trivial dual-interface DUTs with intermediate hardware logic in `examples/multi_bus_ref/complex_datapath/`, verified end-to-end with 256-byte address-accurate golden scoreboards on Verilator 5.054 + slang 12.2, and killed 3/3 middle-logic RTL mutations.

## New Dual-Interface DUTs & Verified Scenarios
1. **`axi4_64_to_apb_32_bridge.sv` (`AXI4-Full 64b Slave` + `APB4 32b Master`)**:
   - 64-bit to 2×32-bit width splitting/repacking FSM (`+4` byte address offset, `WSTRB[7:4]`/`[3:0]` mapping), INCR4 + WRAP4 32-byte boundary wrapping, scrambled partial-strobe writes, and 256-byte full address-space sweep + physical APB RAM byte-for-byte audit (`4/4 PASS`).
2. **`axi4_ooo_reorder_fabric.sv` (`AXI4 Slave` + `AXI4 Master` -> `axi4_full_slave.sv`)**:
   - Heterogeneous latency dual-pipe read scheduler (even bank 5 cycles, odd bank 1 cycle) verifying genuine different-ID out-of-order completion (`AR(id=2,0x10) -> AR(id=9,0x14)` returns `order[9,2]`), strict AXI4 same-ID in-order hazard stall (`AR(id=5,0x20) -> AR(id=5,0x24)`), scrambled partial-strobe writes, and shuffled address readback (`3/3 PASS`).
3. **`pcie_tlp_repack_loopback.sv` (RX + TX 128-bit PCIe TLP Streams)**:
   - Same-protocol multi-format packing: 3DW Header (32-bit addr) MWr/MRd, 4DW Header (64-bit addr) MWr/MRd, 2-DW multi-beat payloads, First/Last DW Byte-Enable partial overwrites, and Split Completion (`CplD` split into 2 packets per 2-DW MRd) with shuffled tags (`6/6 PASS`).

## Live Verification & Mutation Evidence
- `extract_interfaces` (`slang 12.2`): dual interfaces extracted (`axi4(slave,29)+apb(master,9)`, `axi4(slave,29)+axi4(master,29)`, `pcie(slave,11)`).
- `lint_tb`: 0 errors across all 3 complex testbenches.
- `run_sim`: **13/13 complex checks PASS** (combined **58/58 Verilator checks PASS** across all 18 suites).
- `run_mutation`: **3/3 middle-logic RTL mutations KILLED** (`CM01_SPLIT_OFFSET_BROKEN`, `CM02_SAME_ID_ORDER_BROKEN`, `CM03_PCIE_4DW_FBE_BROKEN`), combined **8/8 (100%)**.
