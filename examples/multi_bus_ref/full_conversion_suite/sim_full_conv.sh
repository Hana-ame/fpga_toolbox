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
  rm -rf "$out"
  echo "== sim: $name (top=$top) =="
  "$VLT" --binary --build-jobs 4 -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME -Wno-IMPLICITSTATIC --top-module "$top" -Mdir "$out" "$@" >/dev/null
  "$bin"
}

case "${1:-all}" in
  apb2axi_b0)  run_one apb2axi_b0  tb_apb_to_axi4_buf  -GBUF_CYCS=0 "$PARENT/axi4_full_slave.sv" "$HERE/apb_to_axi4_buf_bridge.sv"  "$HERE/tb_apb_to_axi4_buf.sv" ;;
  apb2axi_b2)  run_one apb2axi_b2  tb_apb_to_axi4_buf  -GBUF_CYCS=2 "$PARENT/axi4_full_slave.sv" "$HERE/apb_to_axi4_buf_bridge.sv"  "$HERE/tb_apb_to_axi4_buf.sv" ;;
  apb2axi_b4)  run_one apb2axi_b4  tb_apb_to_axi4_buf  -GBUF_CYCS=4 "$PARENT/axi4_full_slave.sv" "$HERE/apb_to_axi4_buf_bridge.sv"  "$HERE/tb_apb_to_axi4_buf.sv" ;;
  pcie2axi_b0) run_one pcie2axi_b0 tb_pcie_to_axi4_buf -GBUF_CYCS=0 "$PARENT/axi4_full_slave.sv" "$HERE/pcie_to_axi4_buf_bridge.sv" "$HERE/tb_pcie_to_axi4_buf.sv" ;;
  pcie2axi_b2) run_one pcie2axi_b2 tb_pcie_to_axi4_buf -GBUF_CYCS=2 "$PARENT/axi4_full_slave.sv" "$HERE/pcie_to_axi4_buf_bridge.sv" "$HERE/tb_pcie_to_axi4_buf.sv" ;;
  pcie2axi_b4) run_one pcie2axi_b4 tb_pcie_to_axi4_buf -GBUF_CYCS=4 "$PARENT/axi4_full_slave.sv" "$HERE/pcie_to_axi4_buf_bridge.sv" "$HERE/tb_pcie_to_axi4_buf.sv" ;;
  axi2ddr3_b0) run_one axi2ddr3_b0 tb_axi4_to_ddr3_buf -GBUF_CYCS=0 "$HERE/axi4_to_ddr3_buf_ctrl.sv" "$HERE/tb_axi4_to_ddr3_buf.sv" ;;
  axi2ddr3_b2) run_one axi2ddr3_b2 tb_axi4_to_ddr3_buf -GBUF_CYCS=2 "$HERE/axi4_to_ddr3_buf_ctrl.sv" "$HERE/tb_axi4_to_ddr3_buf.sv" ;;
  axi2ddr3_b4) run_one axi2ddr3_b4 tb_axi4_to_ddr3_buf -GBUF_CYCS=4 "$HERE/axi4_to_ddr3_buf_ctrl.sv" "$HERE/tb_axi4_to_ddr3_buf.sv" ;;
  all)
    for s in apb2axi_b0 apb2axi_b2 apb2axi_b4 pcie2axi_b0 pcie2axi_b2 pcie2axi_b4 axi2ddr3_b0 axi2ddr3_b2 axi2ddr3_b4; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
