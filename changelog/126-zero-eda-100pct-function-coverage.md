# 126 · 零 EDA 环境下达成 100% 函数覆盖率（169/169，六层不变量全通过）

## 现象

在无 `slang`、无 `verilator` 的纯 Python 环境下运行完整 `python3 scripts/verify_invariants.py`，Layer 4 报出 8 个未覆盖函数（161/169）：

- 纯 Python 辅助函数：`eda_env.py:parse_verilator_version`、`sim_runner.py:_collect_waveforms`、`sim_runner.py:_collect_logs`
- `slang` JSON 解析与补齐管线：`sv_parser.py:_run_slang`、`_parse_with_slang`（含内部 `_walk`）、`_enrich_from_builtin`、`slang_instantiation_targets`（含内部 `_walk`）

这些函数的解析与文件收集逻辑本身并不依赖外部 EDA 二进制，却因缺乏离线单测而在无 EDA 环境下成为覆盖盲区。

## 改进

**`tests/test_full_coverage.py`**：新增 `test_zero_eda_helper_and_slang_json_pipeline()`：

1. 直接覆盖 `parse_verilator_version` 的正常版本号与非法输入；
2. 在临时目录构造 `.vcd`（非空）、`.fst`（空）、`.log` 与非目标文件，验证 `_collect_waveforms` 与 `_collect_logs` 的过滤、排重与 `empty` 判定；
3. 使用 `unittest.mock.patch` 模拟 `shutil.which("slang")` 与 `subprocess.run`，注入带 banner 的合成 `slang --ast-json` 与 `--cst-json` 输出，端到端验证 `_run_slang` → `_parse_with_slang` → `_enrich_from_builtin`（补齐 `apb_if.slave` 的 `interface_type`/`modport`）与 `slang_instantiation_targets`（提取 `vendor_ip`）。

## 验证

- `python3 tests/test_full_coverage.py`：77/77 用例全通过。
- `python3 scripts/verify_invariants.py`（无 `slang`、无 `verilator` 完整六层）：
  - `Layer 4: 100.0% function coverage (169/169 functions across 22 modules)`
  - **`ALL 6 INVARIANT LAYERS PASSED`**
