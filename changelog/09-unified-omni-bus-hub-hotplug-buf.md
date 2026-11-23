# 09 — Unified 6-Interface All-Directional Translation & Shared 256B Memory Hub (Hot-Plug, Tri-Domain Sleep, `BUF_CYCS=0/2/4`)

## Summary
Built `examples/multi_bus_ref/omni_hub/soc_omni_bus_hub.sv` uniting all 6 standard interfaces (`SPI`, `APB4`, `AXI4`, `Ethernet`, `PCIe`, `DDR4`) around one `BUF_CYCS`-buffered, 256-byte shared backing memory with full hot-plug disconnect/reconnect, power-on init, negotiation failure, and tri-domain sleep/wake handshakes.

## Live Verification & Mutation Evidence
- `extract_interfaces` (`slang 12.2`): all 6 interfaces extracted simultaneously with real directions and bit-widths (`pcie(11)`, `ddr4(6)`, `axi4(29)`, `apb(9)`, `spi(4)`, `ethernet(7)`).
- `lint_tb`: 0 errors.
- `run_sim` (`Verilator 5.054`): **18/18 checks PASS** across `BUF_CYCS = 0, 2, 4` (combined **115/115 Verilator + 7/7 cocotb = 122 checks PASS**).
- `run_mutation`: **3/3 hot-plug & sleep RTL mutations KILLED** (`OM01_ETH_HOTPLUG_BROKEN`, `OM02_PCIE_HOTPLUG_BROKEN`, `OM03_DDR_SR_GUARD_BROKEN`, tightened after mutation testing caught missing `eth_an_done==0` and `ltssm_state==DETECT` assertions) — combined **15/15 (100%)**.
