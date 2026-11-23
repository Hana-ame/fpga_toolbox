# 07 — SPI/Ethernet Classifiers, Bidirectional Conversions, Power-On/Negotiation/Failure/Sleep Handshakes & Built-in Shared MEM

## Summary
Added native `spi` and `ethernet` classifiers to `fpga_toolbox/iface_extractor.py`, built 4 system-level dual/tri-interface SoC fabric DUTs with built-in 256B shared memory arrays in `examples/multi_bus_ref/soc_fabric/`, verified power-on init, all 4 SPI modes, Ethernet/PCIe negotiation success & failure cases, and low-power sleep/wake handshakes live on Verilator 5.054 + slang 12.2, and achieved **12/12 (100%)** total RTL mutation kills.

## New SoC Fabric Suites
1. **`spi_apb_bidir.sv`** (`APB Slave` + `SPI Slave` + `SPI Master` + built-in 256B MEM): POR init guard, SPI Mode 0/1/2/3 (`CPOL×CPHA`) writes read back via APB, APB→SPI Master egress, and `pm_sleep_req/ack` sleep-reject & wake retention (`4/4 PASS`, `SM01_SPI_CPHA_BROKEN` KILLED).
2. **`eth_axi4_bidir.sv`** (`AXI4 Slave` + `Ethernet GMII` + built-in 256B DMA MEM): Auto-Negotiation failure (`peer_cap=00` dropping RX & rejecting TX with `SLVERR`), 100M downgrade, 1000M full-speed, bad-CRC drop, bidirectional ETH↔AXI4 DMA, and EEE low-power sleep/wake handshake (`6/6 PASS`, `SM02_ETH_AN_GUARD_BROKEN` KILLED after closing RX-during-AN_FAIL gap).
3. **`apb_pcie_axi4_ltssm_fabric.sv`** (`PCIe TLP` + `APB Slave` + built-in 256B shared MEM): POR→Detect→Polling→Config→FAIL (deskew failure rejecting TLPs), Recovery→L0, cross-protocol PCIe↔APB shared MEM reads/writes, and L0↔L1 sleep/wake retention (`4/4 PASS`, `SM03_PCIE_LTSSM_GATE_BROKEN` KILLED).
4. **`axi4_ddr_init_sr_ctrl.sv`** (`AXI4 Slave` + `DDR4 PHY` + built-in 256B DRAM): POR/CKE/MRS/ZQCL init guard rejecting early AXI writes, full init to `READY`, AXI→DRAM R/W, and Self-Refresh `sr_req/sr_ack` sleep/wake retention (`4/4 PASS`, `SM04_DDR_SR_GUARD_BROKEN` KILLED).
