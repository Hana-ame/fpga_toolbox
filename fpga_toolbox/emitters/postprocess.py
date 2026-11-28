"""生成 TB 的后处理：`lint_off` 注入、波形 dump 注入、Verilator 运行脚本。

从原 ``emitters/orchestrator.py`` 拆出 —— 编排层（``emit_protocol_testbench``）
此前把「选模板」和「后处理 + 落盘」都堆在一个 477 行的函数里。这三件事对所有
协议**完全一致**（不依赖具体协议语义），拆到独立文件后：

  * 编排层专注「选哪个模板 + 三道闸门」；
  * 后处理集中在 ``postprocess.py``，新增协议无需改动。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from .pcie import _PCIE_GENS, _PCIE_LANES


def add_lint_off(tb_src: str) -> str:
    """在生成的 TB 里关掉 PINMISSING。

    DUT 常带有 TB 不关心的额外端口（调试口、test_mode、scan_en 等）。模板用
    `.*` 通配连接，只覆盖 TB 里声明了同名变量的端口，其余会触发 Verilator 的
    PINMISSING 警告——而 `emit` 的 run 脚本默认把 warning 当错误，构建直接
    "Exiting due to 1 warning(s)" 失败，工具却已返回 ok=True。
    """
    out = []
    in_header = False
    for line in tb_src.splitlines(keepends=True):
        out.append(line)
        if line.startswith("module tb_"):
            # module 头可能是单行 `module tb_x;`，也可能是多行参数化形式
            # （module 名后跟 #( ... );）——后者要等到 `);` 才结束。
            if line.rstrip().endswith(";"):
                in_header = False
                out.append(
                    "  // DUT 可能有 TB 不关心的额外端口（调试/测试/scan），\n"
                    "  // 显式连接覆盖不到会触发 PINMISSING，故显式关闭。\n"
                    "  /* verilator lint_off PINMISSING */\n"
                )
            else:
                in_header = True
        elif in_header and line.rstrip().endswith(";"):
            in_header = False
            out.append(
                "  // DUT 可能有 TB 不关心的额外端口（调试/测试/scan），\n"
                "  // 显式连接覆盖不到会触发 PINMISSING，故显式关闭。\n"
                "  /* verilator lint_off PINMISSING */\n"
            )
    return "".join(out)


def add_waveform_dump(tb_src: str, top_module: str) -> str:
    """给生成的 TB 在 initial 块最前面注入 $dumpfile / $dumpvars。

    为什么要用环境变量控制：波形是可选的（会让仿真慢、文件大），不该默认开启。
    但 TB 一旦编译成二进制，dump 目标就在运行期固定了——所以这里用
    `$value$plusargs` 从仿真命令行读 +WAVE / +WAVE_FILE，无需重新生成 TB。

    关键事实（实测）：Verilator 在**没有** `--trace` 时只打一条
    `-Info: $dumpvar ignored, as Verilated without --trace`，仿真照常
    PASS、exit 0，波形文件根本不存在。$dumpvars 写在 TB 里只是必要条件，
    run 脚本必须同时加 --trace（由 WAVE=1 控制），二者缺一不可。

    注入位置：第一个 `initial begin` 之后的第一行。此时 timescale/clk 已定义，
    且在任何激励之前开始记录，不会漏掉开头几个周期的信号。
    """
    snippet = (
        "    // ---- waveform dump (optional; enable with +WAVE on the sim command line) ----\n"
        '    if ($test$plusargs("WAVE")) begin\n'
        '      if ($value$plusargs("WAVE_FILE=%s", wave_path)) begin\n'
        '        $dumpfile(wave_path);\n'
        "      end else begin\n"
        '        $dumpfile("wave.vcd");\n'
        "      end\n"
        f"      $dumpvars(0, {top_module});\n"
        "    end\n"
    )
    # 在第一个 initial begin 之后插入
    marker = "  initial begin\n"
    idx = tb_src.find(marker)
    if idx < 0:
        return tb_src  # 模板形态变了：不静默破坏，保持原样
    insert_at = idx + len(marker)
    out = tb_src[:insert_at] + snippet + tb_src[insert_at:]

    # $value$plusargs 的目标必须是一个已声明的变量（Verilator 会报
    # "Can't find definition of variable: 'wave_path'" 直接拒绝编译——
    # 这是实际跑一遍才发现的）。声明放在 module 顶部、endmodule 之前。
    decl = '  string wave_path;\n'
    if decl not in out:
        close = out.rfind("endmodule")
        if close > 0:
            out = out[:close] + decl + out[close:]
    return out


def _emit_run_script(
    proto: str,
    mod: str,
    rtl_abs: Path,
    tb_abs: Path,
    *,
    pcie_suites: Optional[List[str]] = None,
    pcie_matrix: bool = False,
) -> str:
    """生成 suite 派发脚本。

    suite 列表必须与 TB 模板**实际支持的配置**一致：
    * TB 走 3x3 矩阵（DUT 声明了 GEN/LANES）时才是 9 个 suite，且必须把
      ``-GGEN`` / ``-GLANES`` 传给 verilator 才能覆盖参数；
    * TB 是固定配置时只有一个 suite，**不能**再传 ``-GGEN/-GLANES``——
      对没有该参数的设计跑 ``verilator -GGEN=3`` 会得到
      ``Parameters from the command line were not found in the design`` 并以
      非 0 退出，9 个「suite」全部是编译失败（旧 `sim.sh` 就踩过这个）。
    """
    # top 必须等于 TB 文件里实际声明的 module 名。直接取 tb_abs.stem ——
    # 调用方保证文件名 == module 名（协议模板与桥接模板都遵守），这样桥接
    # TB（module 名是 tb_<mod>_bridge 而非 tb_<mod>_<proto>）也能正确驱动。
    # 旧实现用 f"tb_{mod}_{proto}" 硬算，桥接下会生成 --top-module tb_x_axi4
    # 而实际 module 是 tb_x_bridge，Verilator 报 "not found in design"。
    top = tb_abs.stem
    if proto == "pcie":
        names = list(pcie_suites or [])
        if not names:
            names = [f"pcie_g{g}x{n}" for g in _PCIE_GENS for n in _PCIE_LANES]
        cases: List[str] = []
        for sname in names:
            flags = ""
            if pcie_matrix:
                # suite 名形如 pcie_g4x16，直接解析出需要覆盖的参数值。
                mm = re.match(r"pcie_g(\d+)x(\d+)$", sname)
                if mm:
                    flags = f" -GGEN={mm.group(1)} -GLANES={mm.group(2)}"
            cases.append(f'  {sname}) run_one {sname} {top}{flags} "{rtl_abs}" "{tb_abs}" ;;')
        all_s = " ".join(names)
    else:
        all_s = proto
        cases = [f'  {proto}) run_one {proto} {top} "{rtl_abs}" "{tb_abs}" ;;']
    cases_s = "\n".join(cases)

    return f"""#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
VLT="${{VERILATOR:-verilator}}"

# 波形开关：WAVE=1 时给 verilator 加 --trace / --trace-fst，并让 TB 的
# $dumpfile 落到 WAVE_DIR（默认脚本目录）。
# 注意：Verilator 在没有 --trace 时**静默忽略** $dumpvars（只打一条 -Info），
# 仿真仍 PASS、exit 0，但波形文件不存在。所以 --trace 必须与 TB 里的
# $dumpfile/$dumpvars 配对，run_sim 再用 require_waveform 对产物做断言。
WAVE="${{WAVE:-0}}"
WAVE_FORMAT="${{WAVE_FORMAT:-vcd}}"
TRACE_FLAG=""
if [ "$WAVE" = "1" ]; then
  case "$WAVE_FORMAT" in
    fst) TRACE_FLAG="--trace-fst" ;;
    *)   TRACE_FLAG="--trace" ;;
  esac
fi

run_one() {{
  local name="$1"; shift
  local top="$1"; shift
  local out="$HERE/obj_$name"
  local bin="$out/V$top"
  # 波形开关变化必须重建：带 --trace / --trace-fst 与不带是三套二进制。
  # 复用旧 binary 会造成两种假成功：
  #   * 加了 WAVE=1 却没有波形（旧 binary 无 trace）
  #   * WAVE_FORMAT=fst 却产出 VCD 内容（旧 binary 是 --trace 编的）
  # 所以标记文件里记下**格式**，不只是"开没开"。
  #
  # want_tag 必须在 if/else **之外**声明：若放在某一支里，另一支根本不执行
  # 声明，而脚本用 `set -u`，之后 `printf '%s' "$want_tag"` 报
  # `unbound variable` 并以 rc=1 失败。实测踩坑：全新目录**首次**构建
  # （走 then 分支，binary 不存在）100% 失败；第二次运行 binary 已存在、
  # 走 else 分支反而正常 —— 这个反差把缺陷完全掩盖住了。
  local want_tag="off"
  if [ "$WAVE" = "1" ]; then want_tag="$WAVE_FORMAT"; fi
  local need_build=0
  if [ ! -x "$bin" ]; then
    need_build=1
  else
    for arg in "$@"; do
      if [ -f "$arg" ] && [ "$arg" -nt "$bin" ]; then
        need_build=1
      fi
    done
    local have_tag=""
    if [ -f "$out/.wave_mode" ]; then have_tag="$(cat "$out/.wave_mode" 2>/dev/null || true)"; fi
    if [ "$want_tag" != "$have_tag" ]; then
      need_build=1
    fi
  fi
  echo "== sim: $name (top=$top) =="
  if [ "$need_build" -eq 1 ]; then
    rm -rf "$out"
    # --timescale 1ns/1ps：TB 头部写了 `timescale，但大量真实 RTL 把它交给
    # 编译选项、文件里不写。Verilator 5.x 对「部分模块有 timescale、部分没有」
    # 报 TIMESCALEMOD，并在默认配置下当 error 处理（"Exiting due to N
    # warning(s)"），于是一个完全合法的 DUT 直接构建失败 —— 实测无 timescale
    # 的 APB 从机 emit 返回 ok=True 但 run 脚本 rc!=0。
    # 这里用 --timescale 统一给**所有**模块同一个 timescale（与 tb_emitter 的
    # COMPILE_ARGS 口径一致），比 -Wno-TIMESCALEMOD 更正确：既不压制告警，
    # 又让 DUT 的时间单位真正确定下来。
    "$VLT" --binary --build-jobs 4 $TRACE_FLAG --timescale 1ns/1ps -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME -Wno-PINMISSING --top-module "$top" -Mdir "$out" "$@" >/dev/null
    printf '%s' "$want_tag" > "$out/.wave_mode"
  fi
  if [ "$WAVE" = "1" ]; then
    # +WAVE 让 TB 里的 $dumpvars 真正执行；+WAVE_FILE 指定落盘路径。
    # 只用环境变量不生效：TB 读的是 plusargs，不是 env。
    local wave_file="${{WAVE_DIR:-$HERE}}/wave_${{name}}.${{WAVE_FORMAT:-vcd}}"
    "$bin" +WAVE "+WAVE_FILE=$wave_file"
  else
    "$bin"
  fi
}}

case "${{1:-all}}" in
{cases_s}
  all)
    for s in {all_s}; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
"""
