#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VLT="${VERILATOR:-verilator}"

run_one() {
  local name="$1"; shift
  local top="$1"; shift
  local out="$HERE/obj_$name"
  local bin="$out/V$top"
  local need_build=0
  if [ ! -x "$bin" ]; then
    need_build=1
  else
    for arg in "$@"; do
      if [ -f "$arg" ] && [ "$arg" -nt "$bin" ]; then
        need_build=1
      fi
    done
  fi
  echo "== sim: $name (top=$top) =="
  if [ "$need_build" -eq 1 ]; then
    rm -rf "$out"
    "$VLT" --binary --build-jobs 4 -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME --top-module "$top" -Mdir "$out" "$@" >/dev/null
  fi
  "$bin"
}

case "${1:-all}" in
  apb)        run_one apb        tb_apb       "$HERE/apb_slave.sv"       "$HERE/tb_apb.sv" ;;
  axi4_full)  run_one axi4_full  tb_axi4_full "$HERE/axi4_full_slave.sv" "$HERE/tb_axi4_full.sv" ;;
  ahb)        run_one ahb        tb_ahb_lite  "$HERE/ahb_lite_slave.sv"  "$HERE/tb_ahb_lite.sv" ;;
  ddr3)       run_one ddr3       tb_ddr3      "$HERE/ddr3_mem_ctrl.sv"   "$HERE/tb_ddr3.sv" ;;
  ddr4)       run_one ddr4       tb_ddr4      "$HERE/ddr4_mem_ctrl.sv"   "$HERE/tb_ddr4.sv" ;;
  hbm)        run_one hbm        tb_hbm       "$HERE/hbm_pc_ctrl.sv"     "$HERE/tb_hbm.sv" ;;
  axi4_stream) run_one axi4_stream tb_axs_fifo_axi4_stream "$HERE/axi4_stream_dut.sv" "$HERE/tb_axi4_stream.sv" ;;
  ddr5)       run_one ddr5       tb_ddr5_ctrl_real_ddr5  "$HERE/ddr5_mem_ctrl.sv" "$HERE/tb_ddr5.sv" ;;
  spi)        run_one spi        tb_spi_master_real_spi  "$HERE/spi_master.sv"   "$HERE/tb_spi.sv" ;;
  ethernet)   run_one ethernet   tb_eth_mac_real_ethernet "$HERE/eth_mac.sv"     "$HERE/tb_ethernet.sv" ;;
  pcie_g3x1)  run_one pcie_g3x1  tb_pcie      -GGEN=3 -GLANES=1  "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g3x4)  run_one pcie_g3x4  tb_pcie      -GGEN=3 -GLANES=4  "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g3x16) run_one pcie_g3x16 tb_pcie      -GGEN=3 -GLANES=16 "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g4x1)  run_one pcie_g4x1  tb_pcie      -GGEN=4 -GLANES=1  "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g4x4)  run_one pcie_g4x4  tb_pcie      -GGEN=4 -GLANES=4  "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g4x16) run_one pcie_g4x16 tb_pcie      -GGEN=4 -GLANES=16 "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g5x1)  run_one pcie_g5x1  tb_pcie      -GGEN=5 -GLANES=1  "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g5x4)  run_one pcie_g5x4  tb_pcie      -GGEN=5 -GLANES=4  "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  pcie_g5x16) run_one pcie_g5x16 tb_pcie      -GGEN=5 -GLANES=16 "$HERE/pcie_tlp_ep.sv" "$HERE/tb_pcie.sv" ;;
  all)
    for s in apb axi4_full axi4_stream ahb ddr3 ddr4 ddr5 hbm spi ethernet pcie_g3x1 pcie_g3x4 pcie_g3x16 pcie_g4x1 pcie_g4x4 pcie_g4x16 pcie_g5x1 pcie_g5x4 pcie_g5x16; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
