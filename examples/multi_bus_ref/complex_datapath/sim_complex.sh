#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT="$(cd "$HERE/.." && pwd)"
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
  xlt_64_32)  run_one xlt_64_32  tb_axi4_64_to_apb_32    "$HERE/axi4_64_to_apb_32_bridge.sv" "$HERE/tb_axi4_64_to_apb_32.sv" ;;
  bridge_gen) run_one bridge_gen tb_axi4_64_to_apb_32_bridge_bridge "$HERE/axi4_64_to_apb_32_bridge.sv" "$HERE/tb_axi4_64_to_apb_32_bridge_gen.sv" ;;
  ooo_fabric) run_one ooo_fabric tb_axi4_ooo_reorder     "$PARENT/axi4_full_slave.sv" "$HERE/axi4_ooo_reorder_fabric.sv" "$HERE/tb_axi4_ooo_reorder.sv" ;;
  bridge_ooo) run_one bridge_ooo tb_axi4_ooo_reorder_fabric_bridge "$HERE/axi4_ooo_reorder_fabric.sv" "$HERE/tb_axi4_ooo_reorder_bridge_gen.sv" ;;
  pcie_repack) run_one pcie_repack tb_pcie_repack_loopback "$HERE/pcie_tlp_repack_loopback.sv" "$HERE/tb_pcie_repack_loopback.sv" ;;
  all)
    for s in xlt_64_32 bridge_gen ooo_fabric bridge_ooo pcie_repack; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
