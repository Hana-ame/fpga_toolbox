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
  omni_b0) run_one omni_b0 tb_soc_omni_bus_hub -GBUF_CYCS=0 "$HERE/soc_omni_bus_hub.sv" "$HERE/tb_soc_omni_bus_hub.sv" ;;
  omni_b2) run_one omni_b2 tb_soc_omni_bus_hub -GBUF_CYCS=2 "$HERE/soc_omni_bus_hub.sv" "$HERE/tb_soc_omni_bus_hub.sv" ;;
  omni_b4) run_one omni_b4 tb_soc_omni_bus_hub -GBUF_CYCS=4 "$HERE/soc_omni_bus_hub.sv" "$HERE/tb_soc_omni_bus_hub.sv" ;;
  all)
    for s in omni_b0 omni_b2 omni_b4; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
