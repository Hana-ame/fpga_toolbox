# 142 / 143 · 归一化报告忽略 `%Error` 且「零核对」被当作核对通过

## 142 · 日志里的 `%Error` 被完全忽略

```
%Error: some_sv.sv:10:5: syntax error
PASS: apb all 8 checks passed
```

`normalize_sim_result(verilator_log_path=...)` 返回：

```json
{"overall_status": "NO_RESULTS",
 "summary": {"total": 0, "passed": 0, "failed": 0},
 "checks": [], "diagnostics": []}
```

一个**编译都没过**的仿真被归一成「无结果」：既不失败，也没有任何诊断指向
`%Error` 行。`result_normalizer` 全文没有解析 `%Error`/`%Warning` 的代码。

修复：把 `%Error` 行聚成一条 `rule_id="VERILATOR_ERROR"`、
`status="FAIL"` 的检查，`detail` 带行数与首行内容（截断 200 字符）。
`overall_status` 随之变 `FAIL`，`summary.failed >= 1`。

## 143 · `all_verified: true` 与 `observed: {}` 并排出现

`all_verified` 的定义是「预期的套件没有缺失、没有失败」，因此
`expected_suites` 为空时必然为 `true`：

```json
{"expected": [], "observed": {}, "missing": [], "failed": [],
 "all_verified": true}
```

语义本身没错（自审计 A6 依赖它对「无预期」不假报警），但调用方看到
`all_verified=true` 很容易读成「套件核对通过」，实际是什么都没核对。

修复（**不改变** `all_verified` 语义）：

```json
{"all_verified": true,
 "observed_any": false,     // 日志里有没有观察到任何 suite
 "verified_any": false}     // 观察到了、且无缺失/失败
```

`observed_any=false` 明确表示「没核对」。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `%Error` + `PASS` 混合日志 | `NO_RESULTS`，0 检查 | `FAIL`，`VERILATOR_ERROR=FAIL` |
| 无 suite 头日志 | `all_verified=true`（误导） | 追加 `observed_any=false, verified_any=false` |
| 真实 suite 头 + PASS | `all_verified=true` | `observed_any=true, verified_any=true` |
| 干净 PASS 日志 | — | 无 `VERILATOR_ERROR` 假报 |

自审计 `audit_harness` 仍 19/19；四个测试文件全绿。

回归测试：`test_normalizer_surfaces_verilator_errors_and_empty_verification`。
