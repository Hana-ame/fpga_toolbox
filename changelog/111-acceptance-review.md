# 111 · 验收评审（181–184）：无 EDA 环境下的解析缺陷、测试强依赖与 CI 漏跑

> 评审基线：`fpga_toolbox-fb3498b48211cd245fc4a620f20e1024725bb64e`（`v0.5.0`）
> 评审环境：Python 3.11.2，无 `slang`、无 `verilator`、无 `ruff`（严格按 README 声明的“零 EDA 强依赖，仅 PyYAML”口径验证）
> 结论：**有条件通过** — 核心回归、19/19 自审计与文档同步检查全绿，但存在 3 项 P1 阻断项待修复后再签收。

## 验收实测矩阵

| 检查项 | 结果 | 备注 | 
| ----- | ----- | ----- | 
| `tests/test_regression.py` | ✅ PASS | 全通过（依赖 Verilator/slang 的用例按设计跳过） | 
| `tests/test_fusion_and_cross_repo.py` | ✅ PASS | 跨库解析、位域校验、Lint、变异与自审计全绿 | 
| `tests/test_eda_campaign.py` | ✅ PASS | VCS/DC/PT 解析与网表清理全绿 | 
| `python3 -m fpga_toolbox audit_harness` | ✅ PASS | 19/19 故障注入护栏全绿 | 
| `examples/axi_apb_ref/axi/docs_check.py` | ✅ PASS | D1–D13 通过（47 个诊断码全覆盖；无 Verilator 时跳过 D8/D9） | 
| `docs/build_docs_html.py --check` | ✅ PASS | `FEATURES.html` / `EXAMPLES.html` 与 Markdown 同步且自检通过 | 
| Python 3.10 语法兼容扫描 | ✅ PASS | `fpga_toolbox/`、`tests/`、`docs/`、`examples/`、`scripts/` 全通过 | 
| `scripts/verify_invariants.py`（Layer 1/2/3/5/6） | ✅ PASS | 13/13 Schema↔签名双射、18/18 DUT 解析、5/5 TB 零告警（未装 `ruff`，Layer 3 仅跑 `compileall`） | 
| `tests/test_full_coverage.py` & `verify_invariants.py`（Layer 4） | ❌ FAIL (71/75) | 4 个用例失败，对应下方 181–182 | 

## 181 · 内置 SV 解析器把自定义 `typedef` 端口误判为 1-bit（P1，代码缺陷）

**现象**：无 `slang` 时运行 `test_typedef_port_width_placeholders_are_legal` 失败：

```
AssertionError: {'port': 'dbg_mode_i', 'dir': 'input', 'width': 1, 'tie_off': "1'b0"}
  assert oos["dbg_mode_i"]["tie_off"] == "'0"

```

对 `input dbg_e dbg_mode_i` / `output dbg_e dbg_state_o`（`dbg_e` 为 3-bit `enum`），内置后端输出 `width=1`、`tie_off="1'b0"`，而非 Defect 138/139 要求的未知位宽 `width=0`、`tie_off="'0"`。

**根因**：`fpga_toolbox/core/sv_parser.py:296-326`（`_parse_ansi`）遇到不在 `_TYPES` 中的类型标识符 `dbg_e` 时直接 `break`，随后遍历 `item[idx:]` 只保留最后一个 `IDENT`（`dbg_mode_i`）作为端口名，而 `cur_w` 仍沿用标量默认值 `1`。导致无 `slang` 降级路径下，Defect 138/139 的防 `WIDTHTRUNC` 保护完全失效（原回归仅在安装了 `slang` 的开发机上通过）。

**建议修复**：在 `_parse_ansi`（及 `_parse_non_ansi`）中，当类型段之后、`=`/`[` 之前出现多于 1 个 `IDENT`（或含 `pkg::type_t` 形式）且无显式位宽范围 `[...]` 时，将该端口的 `width` 标记为 `0`（`width_expr` 记录类型名或 `"unknown"`），触发下游 `'0` tie-off 与 `.port()` 空连接保护；同时在测试中显式覆盖 `backend="builtin"`。

## 182 · `tests/test_full_coverage.py` 在纯 Python 环境下强依赖 `slang` / `verilator`（P1，测试缺陷）

**现象**：在未安装 `slang` / `verilator` 的环境中，以下 3 个用例直接失败，导致 `scripts/verify_invariants.py` Layer 4 无法跑通：

1. `test_review_fixes_registry_parser_and_types`（`tests/test_full_coverage.py:1544`）：无条件遍历 `for backend in ("builtin", "slang"):`，`slang` 缺失时 `parse_sv_file` 抛出 `RuntimeError`。

2. `test_sv_parser_tokenizer_and_header_fixes`（`tests/test_full_coverage.py:2625`）：同上，无条件遍历 `("builtin", "slang")`。

3. `test_numeric_bounds_are_enforced_at_entry`（`tests/test_full_coverage.py:3248-3254`）：校验合法 `timeout_sec`（`1`、`300`、`"1700"` 及省略参数）时断言 `call_tool("run_sim", ...)["ok"] is True`，无 Verilator 时因 `Verilator requirement unmet: verilator not found` 失败。

**建议修复**：

* 前两处按 `slang_available()` 决定是否追加 `"slang"` 后端（无 `slang` 时显式打印 skip，保持与 `test_regression.py` 一致）；

* 第三处在无 Verilator 时仅断言合法值通过入口边界校验（错误信息不含 `must be >= 1` 且落到环境检查 `E020`），或通过 `verilator` 可用性守卫区分断言。

## 183 · CI 未接入全覆盖测试与不变量门禁（P1，流程缺陷）

**现象**：`.github/workflows/ci.yml` 的 `test` job 仅运行了 `test_regression.py`、`test_fusion_and_cross_repo.py`、`test_eda_campaign.py`、`audit_harness`、`docs_check.py` 与 `build_docs_html.py --check`，**未运行 `tests/test_full_coverage.py`（75 个用例）和 `scripts/verify_invariants.py`**。

**后果**：上述 181/182 在无 Verilator、无 slang 的 GitHub Actions 环境中本应第一时间暴露，却因门禁未接入 CI 而长期漏网。

**建议修复**：修复 181/182 后，将 `python tests/test_full_coverage.py` 与 `python scripts/verify_invariants.py` 接入 `.github/workflows/ci.yml`（并在至少一个 matrix 节点上测试无 `slang` 降级路径）。

## 184 · 文档同步范围与变更目录小幅漂移（P2，非阻断）

* **`docs/USER_MANUAL.md` / `USER_MANUAL.html` 脱离派生校验**：`docs/build_docs_html.py` 仅校验 `FEATURES.md` 与 `EXAMPLES.md`，未覆盖 `USER_MANUAL.md`。目前 `README.md` 与 `docs/USER_MANUAL.md` 仍停留在“11 类总线协议”或未列出 `ddr5`（`canonical_iface.py` 与 `docs/FEATURES.md` 已支持 `ddr5`）。

* **`changelog/` 编号重号**：同时存在 `10-pure-apb-draft.md` 与 `10-synopsys-vcs-dc-pt-campaign-integration.md`（历史遗留，建议保持链接兼容或在索引中注明）。