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
    "$VLT" --binary --build-jobs 4 -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME -Wno-IMPLICITSTATIC --top-module "$top" -Mdir "$out" "$@" >/dev/null
  fi
  "$bin"
}

case "${1:-all}" in
  spi_apb)     run_one spi_apb     tb_spi_apb_bidir        "$HERE/spi_apb_bidir.sv"            "$HERE/tb_spi_apb_bidir.sv" ;;
  eth_axi4)    run_one eth_axi4    tb_eth_axi4_bidir       "$HERE/eth_axi4_bidir.sv"           "$HERE/tb_eth_axi4_bidir.sv" ;;
  pcie_ltssm)  run_one pcie_ltssm  tb_apb_pcie_axi4_ltssm  "$HERE/apb_pcie_axi4_ltssm_fabric.sv" "$HERE/tb_apb_pcie_axi4_ltssm.sv" ;;
  ddr_init_sr) run_one ddr_init_sr tb_axi4_ddr_init_sr     "$HERE/axi4_ddr_init_sr_ctrl.sv"    "$HERE/tb_axi4_ddr_init_sr.sv" ;;
  all)
    for s in spi_apb eth_axi4 pcie_ltssm ddr_init_sr; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
