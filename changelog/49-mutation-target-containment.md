# Changelog — `run_mutation` 变异目标沙箱约束（防越界改写）

## 背景

`run_mutation` 的变异目标来自用户/AI 手写的 YAML/JSON spec 里的 `file` 字段。
旧实现直接 `fp = wd / m["file"]` 就 `read_bytes` / `write_bytes`，**没有任何沙箱
约束**。三类真实越界向量（均已实测复现）：

1. **绝对路径**：`file: "/path/outside/workdir.sv"` → 直接改写外部文件。
2. **`..` 相对路径**：`file: "../target.sv"` → 改写工作目录的兄弟/父目录文件。
3. **符号链接逃逸**：沙箱内 `link.sv -> /outside/real.sv`（合法相对路径字符串，
   但 `is_file()` / `write_bytes` 都跟随链接）→ 改写链接指向的外部文件。

## 为什么这是真缺陷而不是「正常功能」

`try/finally` 的还原**只在正常走到**时才生效。任何一次 crash / `kill -9` /
断电 / OOM 落在 `write_bytes(mutated)` 与 `finally: write_bytes(original)` 之间，
外部文件就被**永久破坏**。变异测试最不能接受的就是「测完把被测代码改坏了」，
而这条路径会把破坏扩散到工作目录之外、工具调用方根本不知道的文件上。

`copy_sandbox` 早就修过同一类问题（沙箱不得是 root 的祖先、符号链接逃逸防护），
但 `run_mutation` 漏掉了 —— 这是防护矩阵的缺口。

## 修复

`fpga_toolbox/mutation_runner.py`：

- 新增 `_resolve_mutation_target(wd, file_str)`：拒绝绝对路径与含 `..` 的相对
  路径；把目标 `resolve()` 后要求仍落在 `wd.resolve()` 之内（覆盖符号链接逃逸）。
  越界返回 `None`。
- 变异主循环：越界目标记 `status=ERROR`，detail 指明「mutation target must be a
  relative path inside the working directory」，**跳过而不改写**。
- `check_residual_mutations` 用同一套约束，越界目标不参与残留检查（既不该读外部
  文件，也避免外部文件恰好匹配 `old`/`new` 而误报残留）。
- 注解用 `Optional[Path]`（与文件既有 `typing` 风格一致）。

## 测试

`tests/test_full_coverage.py::test_mutation_target_containment`：

- 绝对路径越界 → 外部文件内容不变
- `../` 相对路径越界 → 外部文件内容不变
- 沙箱内符号链接指向外部 → 外部文件内容不变，detail 含 `inside the working directory`
- **合法目标仍正常变异并还原**（`killed == 1`，`good.sv` 已还原成原文）

## 验证

- `tests/test_regression.py` / `test_full_coverage.py` / `test_fusion_and_cross_repo.py` / `test_eda_campaign.py` 全过
- `ruff check fpga_toolbox tests scripts` 0 错
- `audit_harness` 14/14，`verify_invariants.py --fast` 5/5
