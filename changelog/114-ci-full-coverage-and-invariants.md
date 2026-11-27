# 114 · CI 接入全覆盖回归套件与六层不变量门禁（183）

> 对应验收项：`changelog/111-acceptance-review.md` #183（P1）

## 现象

`.github/workflows/ci.yml` 的 `test` job 此前只运行了 `test_regression.py`、`test_fusion_and_cross_repo.py`、`test_eda_campaign.py` 与 `audit_harness`，未接入包含 75 个缺陷回归与覆盖用例的 `tests/test_full_coverage.py`，也未运行 `scripts/verify_invariants.py`。

## 修复

**`.github/workflows/ci.yml`**：在 `test` job 中 `EDA campaign parsers` 之后新增两步门禁：

- `Full coverage & defect regression suite`：`python tests/test_full_coverage.py`
- `Six-layer invariant gate`：`python scripts/verify_invariants.py --fast`（覆盖 Layer 1 Schema↔签名双射、Layer 2 抗静默通过矩阵、Layer 3 语法卫生、Layer 5 文档与意图同步、Layer 6 RTL AST 与 TB Lint；在 CI 无 Verilator 环境下避免因 8 个实机 EDA 函数导致的 Layer 4 误报）

## 验证

- 本地无 EDA 环境下实跑两条 CI 新增命令均退出码 0：
  - `python3 tests/test_full_coverage.py` → `ALL FULL-COVERAGE TESTS PASSED`
  - `python3 scripts/verify_invariants.py --fast` → `ALL 5 INVARIANT LAYERS PASSED`
