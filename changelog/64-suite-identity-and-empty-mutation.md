# Changelog — 套件「打 PASS 后崩溃」仍判通过 + 空变异模式静默执行

上一轮引入 baseline 预检后继续审计同类「静默假通过」，这轮又找到两处，根因相同：
**只看"某条证据存在"，不看"证据是否属于这个对象 / 是否覆盖完整边界"**。

## 1. 套件打印过 PASS 之后崩溃，仍被判 PASS（两处）

### 1a. `verify_suite_identities`：任意含 `\bPASS\b` 的行即判套件 PASS

```python
if re.search(r"\bFAIL\b|$fatal|ERROR:", line):
    results[current] = "FAIL"
elif re.search(r"\bPASS\b", line) and results.get(current) != "FAIL":
    results[current] = "PASS"        # <- [CHECK] ... status=PASS 也算
```

套件打印 `[CHECK] id=C1 | status=PASS` 之后、在 `$finish` 之前崩溃（断言挂 /
段错误 / 被超时杀掉），没有 FAIL 字样、也没有 `$fatal` 输出 → 判 **PASS**。
而它根本没跑完。

实测：

```
== sim: apb (top=tb) ==
[CHECK] id=C1 | status=PASS
%Error: Assertion failed
-> verify_suite_identities: {'apb': 'PASS'}, all_verified=True
```

### 1b. `result_normalizer` 的完成度只比**总数**

```python
if len(_summary_list) < len(_started):   # 汇总行总数 vs 启动套件数
```

A 打印两条 `PASS:`（重复）、B 崩溃一条都没有时，总数 2 >= 2 仍成立 ——
B 被放行。实测 `overall_status = PASS`，`VERILATOR_NO_SUMMARY` 根本没触发。

### 修复

- `verify_suite_identities` 增加 `summaries` 字段，按**套件边界**归集
  该套件自己的 `PASS:`/`FAIL:` 结束汇总行；启动过但**没有自己的汇总行**的
  套件改判 `FAIL`（不再沿用中途看到的 PASS）。同时把崩溃信号词扩到
  `Assertion failed` / `Segmentation fault`。
- `result_normalizer` 改用 `ident["summaries"]` 做**逐套件**核对，替换掉
  总数比较（`VERILATOR_NO_SUMMARY` 的 detail 现在点名具体是哪个套件）。

验证（真实日志不误伤）：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| CHECK PASS 后崩溃 | PASS（假） | FAIL ✅ |
| 正常跑完有 `PASS:` 汇总 | PASS | PASS ✅ |
| A 重复 2 条 PASS + B 崩溃 | PASS（假） | FAIL ✅ |
| A/B 各有汇总行 | PASS | PASS ✅ |
| 真实 apb 日志（1 suite） | PASS | PASS ✅ |
| 真实 pcie 日志（9 suite） | PASS | PASS ✅ |

## 2. 变异 spec 的 `old: ""` 被当成「匹配任意位置」静默执行

`b"" in original_bytes` 恒为真，于是 `original.replace(b"", new, 1)` 把 `new`
**插到文件开头**（实测）。变异"成功"、进入分母影响得分，却完全没有语义 ——
用户以为是"在某处替换"，实际是在 offset 0 插了一段文本。

修复：`old_b` 为空时标 `ERROR` 并明确说明「空搜索模式匹配任意位置、会把 new
插到偏移 0，不是有意义的变异」，且**不动文件**。该变异随后因
`evaluated == 0` 让整次运行 fail（分数未定义），而不是给出一个虚高/虚低的分数。

## 验证

- 4 个测试套件全过 / ruff 0 错 / `verify_invariants --full` **6 层全过**
  （142/142 函数覆盖）/ docs HTML 同步。
- 新增 2 个回归测试：`test_suite_identity_requires_per_suite_summary`、
  `test_mutation_rejects_empty_old_pattern`。
