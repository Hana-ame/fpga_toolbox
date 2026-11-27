# 123 · 消除 `test_full_coverage.py` 在纯 Python 环境下对 `slang`/`verilator` 的硬依赖（182）

> 对应验收项：`changelog/111-acceptance-review.md` #182（P1）

## 现象

在未安装 `slang` 与 `verilator` 的纯 Python 环境中运行 `python3 tests/test_full_coverage.py`，以下 3 个用例失败，同时导致 `scripts/verify_invariants.py` Layer 4 中断：

1. `test_review_fixes_registry_parser_and_types`：无条件遍历 `("builtin", "slang")`，缺 `slang` 时抛出 `RuntimeError`。
2. `test_sv_parser_tokenizer_and_header_fixes`：同上，无条件遍历 `("builtin", "slang")`。
3. `test_numeric_bounds_are_enforced_at_entry`：校验合法 `timeout_sec` 时断言 `run_sim` 返回 `ok=True`，无 Verilator 时因 `Verilator requirement unmet: verilator not found` 失败。

## 修复

**`tests/test_full_coverage.py`**：

1. 在 `test_review_fixes_registry_parser_and_types` 与 `test_sv_parser_tokenizer_and_header_fixes` 中通过 `shutil.which("slang")` 动态构造待测后端列表：有 `slang` 时测 `("builtin", "slang")`，无 `slang` 时仅测 `("builtin",)`。
2. 在 `test_numeric_bounds_are_enforced_at_entry` 中调用 `fpga_toolbox.eda_env.probe_eda_env().get("verilator_ok")`：
   - 有 Verilator ≥ 5.040 时维持原有 `ok is True` 断言；
   - 无 Verilator 时断言合法值通过入口数值边界校验（错误信息不含 `must be >= 1`）并准确落到环境守卫 `E020`，保持对“不把合法值误拒”与“不静默通过”的双重验证。

## 验证

- 无 `slang` / 无 `verilator` 环境下 `python3 tests/test_full_coverage.py` 75/75 用例全部通过（`ALL FULL-COVERAGE TESTS PASSED`）。
- `python3 scripts/verify_invariants.py` Layer 4 完整跑完不再崩溃（零 EDA 环境下覆盖 161/169 个函数，仅 8 个需 `slang`/`verilator` 实机的函数未触发）。
