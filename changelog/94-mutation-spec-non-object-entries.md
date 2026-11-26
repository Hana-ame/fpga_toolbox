# 156 · mutation spec 里的非对象元素被静默丢弃

## 现象

```json
{"mutations": ["oops", {"id":"m1","file":"dut.sv","old":"assign b = a;","new":"assign b = ~a;"}]}
```

```
run_mutation(...)
→ {"ok": true, "total": 1, "evaluated": 1,
   "results": [{"id": "m1", "status": "SURVIVED"}]}
```

`"oops"` 这条变异**凭空消失**，`total` 只剩后面的有效项，工具报 `ok=true`。
用户会以为 spec 里所有变异都跑过了。

全是非对象元素时更糟：

```json
{"mutations": ["oops"]}
→ error="No mutation could be evaluated (0 error(s), 0 unmatched out of 0).
        The mutation score is undefined..."
```

错误信息把「元素类型不对」说成「没有可评估的变异」，完全没提根本原因。

## 根因

`load_mutations`：

```python
for idx, m in enumerate(items):
    if not isinstance(m, dict):
        continue          # 静默丢弃
```

与 `result_normalizer` 的 `tests`/`checks` 分支（缺陷 119/153 已修）、
`_parse_verilator_checks`（缺陷 154 已修）是**同一类**问题：不可解释的输入被
静默过滤，而不是报错。

## 修复

非对象元素不再丢弃：收集它们的下标与类型，随后 `raise ValueError`：

```
Mutation spec entries must be objects with file/old/new; got non-object
element(s) at ['[0]='str']. Every entry must be a mapping so no mutation is
silently skipped.
```

外层仍按既有约定包成 `Invalid mutation spec: ...`。

省略 `id` 时自动生成 `M01/M02/...` 是**合法用法**（结果里能看到生成的 id，
可被 `only_id` 引用），保持不动。

## 验证

| spec | 修复前 | 修复后 |
|---|---|---|
| `["oops"]` | `0 error(s), 0 unmatched out of 0` | `must be objects ... silently skipped` |
| `["oops", {有效项}]` | `total=1, ok=true`（oops 消失） | 报错 |
| 合法单条 | `total=1, ok=true` | 不变 |
| 省略 `id` | 生成 `M01` 并可引用 | 不变 |

四套测试全绿；`audit_harness` 19/19。

回归测试：`test_mutation_spec_non_object_entries_are_rejected`。
