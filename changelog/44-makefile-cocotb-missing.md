# Changelog — 契约路径 Makefile 无 cocotb 时报晦涩的路径错误

## 背景

排查契约路径 Makefile（37/40/41/42）的边界：无 cocotb 环境下 `make` 的报错质量。
`/tmp` 清理后重建 fixture 才复现（之前 cocotb venv 完整，掩盖了这条路径）。

## 缺陷 93 — 无 cocotb 时 `make` 报晦涩的路径错误

旧 Makefile：

```makefile
include $(shell cocotb-config --makefiles)/Makefile.sim
```

无 cocotb 时 `cocotb-config` 不在 PATH → `$(shell ...)` 展开成**空串** →
`include /Makefile.sim` → 连锁报错：

```
make: cocotb-config: No such file or directory
Makefile:18: /Makefile.sim: No such file or directory
make: *** No rule to make target '/Makefile.sim'.  Stop.
rc=2
```

用户完全不知道该 `pip install cocotb`——看到的是"找不到 /Makefile.sim"这种
毫无指向性的路径错误（注意开头的 `/`，是变量为空导致的）。

## 修复

`include` 前加 cocotb 依赖前置检查：

```makefile
COCOTB_CONFIG := $(shell command -v cocotb-config 2>/dev/null)
ifeq ($(COCOTB_CONFIG),)
$(error cocotb not found: install it with 'pip install cocotb cocotbext-axi'
  (needs cocotb >= 2.0). cocotb-config must be on PATH — it ships with the
  cocotb package.)
endif
include $(shell $(COCOTB_CONFIG) --makefiles)/Makefile.sim
```

## 修复后

```
$ make SIM=verilator
Makefile:24: *** cocotb not found: install it with 'pip install cocotb
   cocotbext-axi' (needs cocotb >= 2.0). cocotb-config must be on PATH —
   it ships with the cocotb package.  Stop.
rc=2
```

明确指向"装 cocotb"，而非晦涩的路径错误。

## 有 cocotb 时不受影响

用重建的 cocotb 2.1.0 venv 验证：`cocotb-config` 可用 → `$(COCOTB_CONFIG)`
正确展开 → 正常编译运行。有 cocotb 时这条 `ifeq` 分支不触发。

## 回归测试

`test_contract_path_waveform_and_w014` 内新增 2 项（在 PATH 无 cocotb-config
的干净环境里真跑一次 make）：
- `make without cocotb gives install hint (not cryptic path error)`
- `make without cocotb exits non-zero`

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 2 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
tests/test_full_coverage.py           ALL FULL-COVERAGE TESTS PASSED
audit_harness                         13 / 13
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```

## 已知限制（记录，非本轮修）

契约路径 Makefile 未加 `-I <inc_dir>`：RTL 若含 `` `include "defs.svh" ``
且 inc 在 RTL 同级目录，cocotb 编译时会报 `Cannot find include file`。
这是**功能增强**（支持带 include 的 RTL），不是回归——单文件 RTL 正常。
本轮只修无 cocotb 的报错质量这条回归/真实缺陷，inc 支持留待后续。

## 过程踩坑

- 重建 fixture 时混淆了 `extract_interfaces`（只产 contract.yaml + dut.sv）
  与 `emit_testbench`（产 TB），误判"emit 没生成产物"。实际是前者。
- `/tmp` 清理导致 `/tmp/ex1`、`/tmp/ct_venv` 全丢，多次 `cd` / `find` 失败。
  改用 `make_project` + `extract_interfaces` 重建可复现 fixture。
- 修 Makefile 后测试断言 `cocotb-config --makefiles)/Makefile.sim` 不再匹配
  （改用 `$(COCOTB_CONFIG)` 变量间接引用）——测试自己抓到了，改断言匹配新写法。
- 新断言里 `dict(os.environ)` 时 `os` 未在该作用域导入 → NameError，补 import。
