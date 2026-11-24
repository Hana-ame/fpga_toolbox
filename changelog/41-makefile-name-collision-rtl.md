# Changelog — RTL 文件名就叫 `Makefile`：模板覆盖 RTL，产物损坏

## 背景

继续排查契约路径 Makefile 的边界（39/40 修了同名冲突与 run_sim 误用）。
测到一个未覆盖场景：`--rtl-file` 指向的 RTL **文件名就叫 `Makefile`**。

## 缺陷 89 — Makefile 模板覆盖 RTL，产物损坏

`emit_testbench --spec-path` 会把 RTL 复制进 out_dir。当 RTL 文件名就叫
`Makefile` 时：

1. RTL 先被复制为 `out_dir/Makefile`（内容 = RTL）；
2. 随后 Makefile **模板**写同一路径 → **RTL 内容被模板覆盖**。

实测：

```
$ emit_testbench --spec-path contract.yaml --rtl-file ./Makefile
ok: true        ← 静默返回成功
```

但 out_dir 里的 `Makefile` 已是模板而非 RTL，且：

- `grep "module dut" Makefile` → **0 命中**（RTL 内容丢失）
- `VERILOG_SOURCES` 引用的 `Makefile` 实际是模板 → 下游 `make` 编译必然失败

**为什么上一轮（39）的冲突检测没抓到**：那次检测只比 `artifacts` 里的
**产物**文件名（wrapper/checker/bind/cocotb tb），**漏了 Makefile 本身**
——因为 Makefile 是模板名，不在 artifacts 里。

## 修复

冲突检测的 `_reserved` 列表加上 `"Makefile"`：

```python
_reserved = [Path(a).name for a in artifacts.values()] + ["Makefile"]
_collisions = [n for n in _reserved if n == _rtl_name]
```

（同时修正错误信息生成：`_collisions` 现在是文件名列表，不再对字符串套
`Path(c).name`。）

## 验证

```
$ emit_testbench --spec-path contract.yaml --rtl-file ./Makefile
ok: false
error: --rtl-file 'Makefile' has the same name as a generated artifact (Makefile);
       copying it into /tmp/out would overwrite the generated wrapper ...

$ ls /tmp/out/        → out_dir 未被创建（零污染）
```

反向验证：把 `_reserved` 里的 `"Makefile"` 去掉 → 4 项断言全 FAIL
（`RTL named Makefile fails fast` / `Makefile template not written over RTL`
等）。

## 回归测试

`test_contract_path_waveform_and_w014` 内新增 4 项断言：
- `RTL named Makefile fails fast`
- `collision error mentions Makefile`
- `Makefile template not written over RTL on collision`（关键）
- `cocotb testbench not written on Makefile collision`

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 4 项，共 223 ok）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```
