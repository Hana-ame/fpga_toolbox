# Changelog — `run_sim` 把契约路径的 Makefile 当 shell 脚本执行（code 127）

## 背景

契约路径（`emit_testbench --spec-path`）的产物是一个 **Makefile**（上一轮 37
加的运行入口），而多协议路径产出的是可执行 `run_*.sh`。`run_sim` 只认识后者。

## 缺陷 88 — `run_sim` 把 Makefile 当 shell 脚本执行

实测：把契约路径的 Makefile 传给 `run_sim`：

```
$ run_sim --sim-script /tmp/out/Makefile --suite reg_slave
ok: false
error: Simulation exited with code 127.
```

`code 127` = command not found。**用户完全不知道应该用 `make`**，只会看到一句
毫无指向性的"退出码 127"。

## 修复

`run_sim` 入口检测 Makefile 类文件（`.mk` 后缀 / `Makefile`/`makefile`/
`GNUmakefile` 名），返回可操作指引：

```
ok: false
error: /tmp/out/Makefile is a Makefile, not a shell script — run it with make,
       e.g. `make -C /tmp/out SIM=verilator`. To capture a waveform add
       VERILATOR_TRACE=1 (the generated Makefile already sets
       COCOTB_PLUSARGS=+WAVE). run_sim drives a POSIX sim script ...
hint: use make
```

## 验证

```
$ run_sim --sim-script .../Makefile
ok: false
error: ... run it with make, e.g. `make -C /tmp/out SIM=verilator`.
        To capture a waveform add VERILATOR_TRACE=1 ...
```

反向验证：禁用 Makefile 检测 → 2 项断言 FAIL。

## 回归测试

`test_waveform_and_log_capture` 内新增 3 项（**不依赖 Verilator**，类型检测在
版本检查之前）：
- `run_sim rejects a Makefile with actionable hint`
- `Makefile error suggests using make`
- `Makefile error mentions VERILATOR_TRACE for waveform`

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 3 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```
