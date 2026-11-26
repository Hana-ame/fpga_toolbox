# 137 · 不带 `timescale 的 DUT 会让生成的 TB 构建失败

## 现象

`emit_testbench` 为任意 APB 从机生成的 TB 头部写 `` `timescale 1ns/1ps ``。
若用户 RTL **文件里不写 timescale**（而是靠综合/仿真工程的全局编译选项，
这在真实项目里极常见），Verilator 5.x 报：

```
%Warning-TIMESCALEMOD: dut.sv:1:8: Timescale missing on this module as other
                           modules have it (IEEE 1800-2023 3.14.2.3)
%Error: Exiting due to 1 warning(s)
```

Verilator 默认 warning-as-error，所以这是**硬失败**。而 `emit_testbench`
返回 `ok=True`，`run_*.sh` 才在构建阶段 rc!=0 —— 产物「生成成功」但「跑不起来」。

之所以此前一直没暴露：`examples/multi_bus_ref/` 下 7 个参考 DUT 全都带了
`` `timescale 1ns/1ps `` 头，掩盖了这条路径。

## 根因

run 脚本的 verilator 调用只抑制了 `UNUSED` / `UNUSEDSIGNAL` /
`DECLFILENAME` / `PINMISSING`，没有处理 timescale 不一致。TB 自己声明了
timescale，DUT 没有，组合起来就触发 `TIMESCALEMOD`。

## 修复

给 verilator 传 `--timescale 1ns/1ps`：

```
"$VLT" --binary --build-jobs 4 $TRACE_FLAG --timescale 1ns/1ps \
  -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME -Wno-PINMISSING \
  --top-module "$top" -Mdir "$out" "$@"
```

这与 `tb_emitter.py` 里已有的 `COMPILE_ARGS += --timescale 1ns/1ps` 口径
一致。**没有**用 `-Wno-TIMESCALEMOD` 压制：那只是让告警消失，DUT 的时间单位
仍未确定；`--timescale` 是真的给所有模块统一了时间单位。

## 验证

- 无 timescale 的 APB 从机（从参考 DUT 删掉首行 `timescale 得到）：
  修复前 `%Error: Exiting due to 1 warning(s)`；修复后
  `PASS: apb all 8 checks passed`，0 warning。
- 7 个协议全量回归（apb/axi4/ahb_lite/ddr3/ddr4/hbm/pcie）重新生成 + 运行：
  全部 `rc=0`、0 warning、PASS（pcie 9/9 suite）。
- 回归测试：`test_tb_builds_for_rtl_without_timescale`。
