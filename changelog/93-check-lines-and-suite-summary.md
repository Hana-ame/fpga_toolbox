# 154 / 155 · `[CHECK]` 明细行被静默丢弃，又会篡改套件级结论

## 154 · 无 `id` 的 `[CHECK]` 行被直接丢弃

```
[CHECK] status=FAIL exp=1 got=2
```

`_parse_verilator_checks` 里：

```python
if not fields.get("id"):
    continue        # 静默丢弃
```

一条**明确的失败证据**就此消失 —— 报告里既看不到它失败，也没有任何提示说
「有一行解析不了」。这与 XML 分支（缺陷 119）、JSON 分支（缺陷 153）已经确立
的口径完全不一致（那两处都把无名项判成 `UNNAMED_TESTCASE` FAIL）。

修复：无 `id` 时保留为 `rule_id="UNNAMED_TESTCASE"`、`status="FAIL"`，`detail`
带上原始行（截断 200 字符）。

## 155 · 明细 `status=FAIL` 让整个套件判 FAIL

```
== sim: apb (top=tb_apb) ==
[CHECK] id=C1 | status=FAIL | exp=1 | got=2
PASS: apb all 8 checks passed
```

`verify_suite_identities` 把 `\bFAIL\b` 关键词检查放在了**汇总行检查之前**，
于是明细行里的 `FAIL` 先把套件标成 FAIL，之后 `PASS:` 汇总行再也不能纠正
（`elif results.get(current) != "FAIL"`）。结果：

```json
{"observed": {"apb": "FAIL"}, "summaries": {"apb": "PASS"},
 "failed": ["apb"], "all_verified": false}
```

`observed` 与 `summaries` 自相矛盾。

修复：**先判汇总行**，汇总行才是套件级结论的权威；`FAIL`/崩溃关键词检查移到
之后，并且只在「还没有 PASS 汇总」时生效 —— 这样「明细失败 + 无汇总行」
（真崩溃）仍然判 FAIL，不会因重排而放行。

## 验证

| 日志 | 修复前 | 修复后 |
|---|---|---|
| `[CHECK] status=FAIL`（无 id） | 整行丢弃 | `UNNAMED_TESTCASE=FAIL` |
| 明细 FAIL + `PASS:` 汇总 | 套件 FAIL（矛盾） | 套件 PASS，明细仍记 FAIL |
| 明细 FAIL + `FAIL:` 汇总 | FAIL | FAIL（不变） |
| 明细 FAIL + 无汇总行 | FAIL | FAIL（不变） |
| 崩溃痕迹 + 无汇总行 | FAIL | FAIL（不变） |
| 干净 `PASS:` | PASS | PASS（不变） |

自审计 19/19；四套测试全绿。

回归测试：`test_check_lines_and_suite_summary_precedence`。
