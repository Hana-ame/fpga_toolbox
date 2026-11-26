# 153 · cocotb JSON 里无名结果项被当成「通过证据」

## 现象

```json
{"tests": [{"name": "t1", "status": "passed"}, {"status": "passed"}]}
```

```
normalize_sim_result(cocotb_results_json=...)
→ {"overall_status": "PASS",
   "summary": {"total": 2, "passed": 2, "failed": 0, "pass_rate": 1.0},
   "checks": [{"rule_id": "t1", "status": "PASS"},
              {"rule_id": "UNKNOWN", "status": "PASS"}]}
```

第二条测试**没有任何标识**，却被计入 `passed`，最终整体 PASS。
一条无从归属的「通过」不构成任何真实检查证据。

`{"checks": [{"rule_id": "c1", "status": "PASS"}, {"status": "PASS"}]}`
的 `checks` 分支有完全相同的问题（兜底成 `"UNKNOWN"`）。

## 根因

缺陷 119 修好了 XML 分支（`<testcase />` 无名 → `UNNAMED_TESTCASE` FAIL），
但当时只改了 XML 路径。JSON 的两条分支用的是：

```python
"rule_id": str(t.get("name", t.get("rule_id", "UNKNOWN")))
"rule_id": str(c.get("rule_id", "UNKNOWN"))
```

「缺失」被静默替换成一个看起来正常的字符串 `"UNKNOWN"`，于是下游完全看不出
这条检查项是不可归属的。

## 修复

两个 JSON 分支与 XML 侧口径统一：`name`/`rule_id` 缺失或纯空白 → 生成一条
`rule_id="UNNAMED_TESTCASE"`、`status="FAIL"` 的检查，`detail` 说明「无法归属，
不算通过证据」。

`name` 为空但 `rule_id` 存在时仍用 `rule_id`（合法标识，不误报）。

## 验证

| 输入 | 修复前 | 修复后 |
|---|---|---|
| `tests` 含无名项 | `PASS`，`total=2,p passed=2` | `FAIL` + `UNNAMED_TESTCASE` |
| `tests` 含空白 name | `PASS` | `FAIL` |
| `checks` 含无名项 | `PASS` | `FAIL` + `UNNAMED_TESTCASE` |
| 全部有名且通过 | `PASS` | 不变（不假报警） |
| 用 `rule_id` 作标识 | `PASS` | 不变 |

自审计 19/19；四套测试全绿。

回归测试：`test_json_results_unnamed_entries_are_not_pass_evidence`。
