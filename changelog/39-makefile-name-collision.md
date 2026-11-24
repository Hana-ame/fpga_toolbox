# Changelog — RTL 与生成产物同名：wrapper 被覆盖、产物销毁

## 背景

排查契约路径 Makefile（35/37）的边界时测到一个未覆盖场景：`--rtl-file` 指向的
RTL **文件名与生成的产物同名**（如 RTL 就叫 `reg_slave_axil_wrap.sv`）。

## 缺陷 87 — wrapper 被覆盖、产物销毁、静默 ok=True

`emit_testbench --spec-path` 会把 RTL 复制进 out_dir（让产物自包含）。
当 RTL 文件名与生成的 wrapper 同名时：

```
$ python3 -m fpga_toolbox emit_testbench --spec-path contract.yaml \
    --out-dir /tmp/out --rtl-file ./reg_slave_axil_wrap.sv
ok: True        ← 静默返回成功
```

但 out_dir 里的 `reg_slave_axil_wrap.sv` 此时是**原始 RTL**，刚生成的
wrapper 被覆盖销毁：

| | 正常 | 同名冲突时 |
|---|---|---|
| `dumpvars` 出现次数 | 2 | **0** |
| wrapper 模块定义 | 有 | **无** |

下游 `make` 会拿一份**只有 RTL 定义、没有 TB 结构**的文件去编译——失败方式
完全不可预测，而工具已报 `ok=True`。

## 修复（两层，第二层才是关键）

1. 加冲突检测：RTL 名与任一生成产物同名 → fail-fast。

2. **检测位置**：最初放在"写产物之后、复制 RTL 之前"。这样虽然拦住了 RTL
   复制，但 **wrapper/Makefile 已经写进 out_dir 了**——out_dir 被污染（实测
   out3 里有 4 个产物文件）。

   把检测**提到写任何产物之前**（`artifacts` 定义后、第一个 `write_text` 前），
   冲突时 out_dir 只有测试预放的 RTL，**零产物文件被写入**。

   第二层才是真正的修复——第一层只是"少写了几个文件"。

## 验证

```
$ emit_testbench --spec-path contract.yaml --rtl-file ./reg_slave_axil_wrap.sv
ok: false
error: --rtl-file 'reg_slave_axil_wrap.sv' has the same name as a generated
       artifact (reg_slave_axil_wrap.sv); copying it into /tmp/out would
       overwrite the generated wrapper. Rename the RTL file or choose a
       different --out-dir.

$ ls /tmp/out/
（空目录 —— 零产物文件被写入）
```

反向验证：删掉前置检测块 → 5 项断言全 FAIL（Makefile/cocotb tb 被写入、
RTL 被 wrapper 覆盖）。

## 过程踩回自己的坑（2 次）

1. **移动代码块时删掉了 `apb_slave_model` 的写入**：把冲突检测提到前面时，
   连带删掉了紧邻的 `Path(apb_model_path).write_text(...)`。fusion 测试的
   `apb_slave_model.sv generated` 立即 FAIL —— 抓到了。
2. **冲突检测拿 `artifacts` 的 key 去比**：key 是 `unit_isolation_wrapper`
   这种别名，永远比不中 RTL 文件名，检测形同虚设。应用 **value 的文件名**
   （value 是完整路径）。这是我第一版检测"形同虚设"的直接原因。

## 回归测试

`test_contract_path_waveform_and_w014` 内新增 6 项断言：
- `name collision fails fast`
- `collision error names the offending artifact`
- `Makefile not written on collision`
- `cocotb testbench not written on collision`
- `pre-existing RTL not overwritten by generated wrapper`
- （+ 冲突时 `out_dir` 零产物文件被写入）

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 6 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```
