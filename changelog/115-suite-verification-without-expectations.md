# 190–191 · 不传 `expected_suites` 时完全不核对套件；`STARTED` 漏成「没失败」

两个缺陷都在套件身份核对这条链上，合起来的效果是：**一个崩溃的套件可以被
报告成核对通过**。

## 190 · 无预期套件时整段核对被跳过

```python
identity = verify_suite_identities(combined, expected_suites) if expected_suites else None
```

不传 `expected_suites` 时 `suite_identity` 恒为 `null`：

```json
{"ok": true, "suite_identity": null}
```

而日志里明明有两个套件的完整汇总行（`apb PASS` / `axi4 PASS`）。既没核对
身份、也没检查完成度 —— 调用方看到 `ok=true` + `null` 会以为「没什么需要核对的」。

修复：始终核对；`expected=[]` 表示「不要求特定套件」，但观察到的每个套件仍
必须自己证明跑完。

## 191 · `STARTED` 状态未转成 FAIL，且 `failed` 只从预期列表取

`verify_suite_identities` 的收尾逻辑有两处漏：

```python
# (a) 只转 PASS，STARTED 漏掉
for name, status in list(results.items()):
    if status == "PASS" and name not in summaries:
        results[name] = "FAIL"

# (b) failed 只在 expected_suites 里找
failed = [s for s in expected_suites if results.get(s) != "PASS" and s not in missing]
```

实测「apb 打印 PASS，axi4 只打印 header 就崩溃」：

```json
{"observed": {"apb": "PASS", "axi4": "STARTED"},
 "failed": [], "all_verified": true}
```

`axi4` 状态是 `STARTED`（从未打印汇总行），(a) 放过它；(b) 又因为没有
`expected_suites` 而给出空 `failed`，最终 `all_verified=true` —— 一个真实崩溃
被判为核对通过。

修复：

- (a) 所有非 FAIL 且无自身汇总行的套件统一转 FAIL
      （`if status != "FAIL" and name not in summaries`）；
- (b) `failed` 除预期列表外，追加**实际观察到的** FAIL（保序去重）。

`all_verified` 语义不变（无缺失且无失败），但因为 `failed` 现在覆盖观察结果，
无预期时若有套件崩溃会正确变成 `False`；「有没有观察过」仍由 `observed_any`
单独表达（自审计 A6「无预期不假报警」不受影响，19/19 通过）。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 不传 `expected_suites`，日志 2 套件全 PASS | `suite_identity=null` | 完整核对，`observed_any=true` |
| `apb PASS` + `axi4` 崩溃，无预期 | `failed=[]`, `all_verified=true` | `failed=['axi4']`, `all_verified=false` |
| 同上，有预期 `[apb, axi4]` | `failed=['axi4']` ✓ | 不变 |
| 无预期 + 全干净 | `all_verified=true` | 不变（不假报警） |
| 完全没有套件 | `all_verified=true`, `observed_any=false` | 不变 |
| 截断的重跑 | `observed='STARTED'` | `observed='FAIL'`（更强） |

四套测试全绿；六层门禁 6/6，函数覆盖 **170/170**，`audit_harness` 19/19。

回归测试：`test_suite_verification_works_without_expected_list`。
