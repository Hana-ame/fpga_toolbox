# Changelog — 契约路径 Makefile 不支持 RTL 的 `include 依赖

## 背景

上一轮（44）修了无 cocotb 的报错质量。本轮继续测契约路径 Makefile 的边界：
RTL 含 `` `include "defs.svh" `` 时能否编译。`make_project`（测试 fixture 助手）
生成的 RTL 首行就是 `` `include "defs.svh" ``，inc 在项目根 —— 这是真实布局。

## 缺陷 94 — 不复制 inc、Makefile 无 `-I` → 编译失败

实测：

```
%Error: reg_slave.sv:1:10: Cannot find include file: 'defs.svh'
make: *** [.../Makefile.verilator:65: sim_build/Vtop.mk] Error 1
```

两个叠加原因：
1. `emit_testbench` 只复制 RTL 单文件，**不复制 inc 目录**；
2. Makefile 的 `COMPILE_ARGS` 只有 `--timescale`，**没有 `-I inc`**。

## 修复

1. `emit_testbench` 复制 RTL 时，按顺序查找 inc 目录并复制进 out_dir：
   RTL 同级 → RTL 父目录 → 项目根（实测三种布局都见过；旧实现只查 RTL
   同级，漏掉 `make_project` 的项目根布局）。
2. Makefile 加条件 `-I`：

```makefile
INCDIR ?= inc
ifneq ($(wildcard $(INCDIR)),)
COMPILE_ARGS += -I$(INCDIR)
endif
```

`$(wildcard ...)` 守卫保证无 inc 时不变（不产生空 `-I`）。

## 验证

```
$ emit_testbench --spec-path contract.yaml --rtl-file rtl/reg_slave.sv
ok: true
$ ls out_dir/inc/ → defs.svh          ← inc 已复制进 out_dir
$ Makefile 含 INCDIR + -I$(INCDIR)    ← 条件 include 路径
$ make SIM=verilator                  ← 不再报 Cannot find include file
```

## 回归测试

`test_contract_path_waveform_and_w014` 内新增 5 项：
- `emit with include RTL ok`
- `inc/ copied into out_dir`
- `Makefile defines INCDIR`
- `Makefile adds -I$(INCDIR) conditionally`

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 5 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
tests/test_full_coverage.py           ALL FULL-COVERAGE TESTS PASSED
audit_harness                         13 / 13
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```
