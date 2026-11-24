# Changelog — 自审计新增 A14：生成的 shell 脚本语法/`set -u` 运行期校验

## 背景

缺陷 78 修的 `want_tag: unbound variable`（首次构建 100% 失败）暴露一个盲区：
`emit_testbench` 和 `eda_campaign` 都会**生成 shell 脚本**，但这些脚本从未被
系统校验语法/变量。同类问题若再次引入，只能靠手工发现。

## 新增自审计项 A14_GENERATED_SHELL_SYNTAX

对两个 shell 生成器的产物做两层校验：

1. **`bash -n` + `bash -u -n`**：语法层（抓括号/引号不配对、语法错误）
2. **运行期真跑**（关键）：把 `verilator`/`rm`/`bin` 调用全 stub 成 no-op
   回显，配合 `set -u`，真跑一遍脚本 —— 让"走了某分支却用了未声明变量"
   暴露成 `unbound variable`。

覆盖：
- `protocol_tb_emitter._emit_run_script`（apb + pcie 两种，含 reset 极性/wave_mode 分支）
- `eda_campaign.generate_remote_run_script`（`use_dev_shm=True/False` 两种）

## 关键教训：runtime 检查的"假验证"陷阱

第一版 runtime 检查**形同虚设**：stub 后脚本在 build 失败处因
`obj/.wave_mode` 目录不存在而 `set -e` 早退，**run_one 从未被执行**，
`BIN echo` 出现 0 次 —— 却仍报"通过"。

我通过 `BIN echo` 计数（0 次 = 没跑到）发现这一点，改成 stub verilator 时
同时 `mkdir -p "$out"`，让脚本真正走到 `run_one` 内部。

**这正是反静默通过原则的应用**：一个"跑过了但没跑到位"的检查比不检查更糟。

## 验证

反向验证：重新注入缺陷 78（`want_tag` 声明挪回 else 分支）→
`want_tag: unbound variable` 被 runtime 检查抓到。

```
tests/test_regression.py              ALL CHECKS PASSED
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
tests/test_full_coverage.py           ALL FULL-COVERAGE TESTS PASSED
audit_harness                         14 / 14（新增 A14）
```

## A14 覆盖（实测计数）

```
A14_GENERATED_SHELL_SYNTAX
  checked=4 bad=0
  （apb/pcie × bash-n/bash-u-n + eda/shm=True/False）
```
