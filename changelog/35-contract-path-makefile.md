# Changelog — 契约路径 cocotb TB 没有运行入口：4 个反直觉的 cocotb 2.x 配置

## 背景

上一轮（33）确认契约路径 cocotb TB 能真跑通，但**只给了 4 个源文件**——
`*_axil_wrap.sv` / `*_axil_sva_checker.sv` / `*_axil_bind.sv` / `test_*_axil.py`，
**没有任何运行入口**。多协议路径（protocol_tb_emitter）会生成可直接 `bash` 的
`run_*.sh`，契约路径却没有——不对称。

要让它跑起来，用户得自己手写 Makefile，而 cocotb 2.x（2.1.0）有一串反直觉的
配置。我把这一串全踩了一遍，每次猜错都得重跑一次构建。

## 实测踩坑清单（Verilator 5.054 + cocotb 2.1.0）

### 1. `TOPLEVEL_LANG` 必须是 `verilog`，写成 `verilator` 会**静默跳过**

```
Skipping simulation as only Verilog is supported on simulator=verilator
```

最阴的一条：写错后**仿真根本没跑**，却仍打印 `TESTS=1 PASS=1`、退出码 0。
连着改了 3 次 Makefile 都在猜这个值，每次都"看起来成功了"。

### 2. 默认 simulator 是 icarus，必须显式 `SIM=verilator`

不带 `SIM=` 时命中 Makefile.icarus，直接 `Skipping simulation`。

### 3. 波形要用 `VERILATOR_TRACE=1`，不是 `VERILATOR_EXTRA_ARGS=--trace`

我一开始传 `VERILATOR_EXTRA_ARGS="--trace"`，抓到的 verilator 命令行里
**根本没有 `--trace`**（`VM_TRACE=0`），于是：

```
-Info: reg_slave_axil_wrap.sv:58: $dumpvar ignored, as Verilated without --trace
```

查 cocotb 的 Makefile 才发现正确的开关是 `VERILATOR_TRACE=1`（它才会在
`COMPILE_ARGS` 里加 `--trace --trace-structs`）。

### 4. `PLUSARGS` 已弃用，正确变量是 `COCOTB_PLUSARGS`

wrapper 里的 `$dumpvars` 由运行期 `+WAVE` plusarg 门控。光有 `--trace` 不够，
必须把 `+WAVE` 传给仿真器。cocotb 2.x 已弃用 `PLUSARGS`，改用
`COCOTB_PLUSARGS`（我最初的测试断言 `PLUSARGS=+WAVE` 因此 FAIL，抓到了这点）。

## 修复

新增 `_emit_cocotb_makefile()`，`emit_testbench --spec-path` 时一并产出
`Makefile`，把上面 4 个坑全部填好：

```makefile
TOPLEVEL_LANG = verilog
SIM ?= verilator
MODULE = test_reg_slave_axil
TOPLEVEL = reg_slave_axil_wrap
VERILOG_SOURCES = reg_slave_axil_wrap.sv reg_slave_axil_sva_checker.sv \
  reg_slave_axil_bind.sv dut.sv
COMPILE_ARGS += --timescale 1ns/1ps
COCOTB_PLUSARGS = +WAVE        # 不加则 --trace 编了也拿不到波形
include $(shell cocotb-config --makefiles)/Makefile.sim
```

同时把 RTL **复制进 out_dir**（只取文件名），让产物自包含——否则 cocotb 在
out_dir 里找不到外部 RTL。

## 验证

全新目录、**零手工编辑**，直接跑生成器产出的 Makefile：

```
$ python3 -m fpga_toolbox emit_testbench --spec-path ... --rtl-file dut.sv
$ cd out && make SIM=verilator VERILATOR_BIN_DIR=... VERILATOR_TRACE=1
** TESTS=1 PASS=1 FAIL=0 SKIP=0
dump.vcd                    6034 字节，145 信号   ← cocotb 自己的
reg_slave_axil_wrap.vcd    42736 字节，145 信号   ← wrapper 自身 $dumpvars
```

反向验证（证明开关有效）：不加 `VERILATOR_TRACE=1` → `TESTS=1 PASS=1` 但
**无任何 vcd**（`$dumpvar ignored`）。

过程缺陷：我最初的测试断言写的是 `PLUSARGS=+WAVE`（弃用变量），实测
`COCOTB_PLUSARGS=+WAVE` 才有效——测试自己抓到了这个不一致。

## 回归测试

`test_contract_path_waveform_and_w014` 新增 8 项 Makefile 断言：
Makefile 存在、`TOPLEVEL_LANG=verilog`、`SIM ?= verilator`、提到
`VERILATOR_TRACE`、内置 `COCOTB_PLUSARGS = +WAVE`、命名 cocotb 模块、
include Makefile.sim、RTL 已复制进 out_dir。

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 8 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```
