# Changelog — `normalize_sim_result` 对畸形结果的静默放行与裸异常

模块 docstring 明确承诺：

> Unrecognized input is reported as an error instead of silently yielding an
> empty PASS/NO_RESULTS report.

但实现有三处违背这个承诺，其中两处是**静默假通过**。

## 1. `checks` 数组混入非对象 → 裸 `AttributeError`

```python
checks = [{
    "rule_id": str(c.get("rule_id", "UNKNOWN")), ...   # c 可能是 str / None
} for c in raw["checks"]]
```

`{"checks": ["oops"]}` 得到：

```
ok=False  error="'str' object has no attribute 'get'"
```

用户完全看不出是**结果文件的 checks 数组**混进了字符串 —— 报错里没有字段名、
没有索引。修复：先校验元素类型，给出

```
results 'checks' must be a list of objects; element(s) at index [0] are
not objects (got types ['str']).
```

## 2. `tests` 数组混入非对象 → **静默过滤 → ok=True**

```python
} for t in tests if isinstance(t, dict)]     # 非 dict 被静默丢掉
```

`{"tests": ["oops", null, 42]}` 变成 0 条检查 → `summary.total=0` →
`overall_status=NO_RESULTS` 且 **`ok=True`**。调用方看到「成功、零检查」，而真相是
**结果文件根本无法解析**。这是本轮最危险的一处：`failed == 0` 且 `ok=True`，
下游任何「检查 ok 就继续」的逻辑都会被骗过去。

修复：与 `checks` 同样校验元素类型，明确报错。

## 3. 无名 `<testcase/>` 被判 PASS —— 比「什么都没有」还宽松

JUnit XML 里 `<testcase/>`（既无 `name` 也无 `classname`）不携带任何身份信息：

```
无 testcase 的 XML           -> NO_RESULTS（total=0）        ✅ 正确
只有一个 <testcase/> 的 XML  -> PASS（rule_id="UNKNOWN"）    ❌ 假通过
```

一份**完全没有断言**的 XML 反而被判通过，而真正空的结果是 NO_RESULTS。这违反同一
设计原则（「零证据 = 没跑」）。修复：无名 testcase 记 `UNNAMED_TESTCASE` +
`status=FAIL`，detail 说明「无法归属到具体检查，因此不是通过证据」。

## 4. `engine` 字段写死 `"Verilator 5.054"`

`_parse_verilator_checks` 把每条 `[CHECK]` 一律标成 `"Verilator 5.054"`。日志可能
来自任意版本（本仓库 CI 就跨 5.020/5.054，用户环境更是各异）—— 报告里的版本
信息是编的。改为从日志抓 `Verilator <x.y[.z]>`，抓不到就留空（不猜）。

## 不误伤

| 输入 | 修复前 | 修复后 |
|---|---|---|
| `checks: [{...}]` 合法 | PASS | PASS |
| `tests: [{...}]` 合法 | PASS | PASS |
| 有名字、无 failure 的 testcase | PASS | PASS |
| 有名字 + `<failure>` | FAIL | FAIL |
| 有名字 + `<skipped>` | NO_RESULTS | NO_RESULTS |
| 截断/非 XML/空文件 | 结构化错误 | 结构化错误（不变） |

## 验证

- 4 个测试套件全过 / ruff 0 错 / `verify_invariants --full` **6 层全过**
  （143/143 函数覆盖）/ docs HTML 同步。
- 新增回归 `test_normalizer_rejects_malformed_and_unnamed_results`，覆盖三类畸形
  输入 + 合法输入不误伤 + engine 版本从日志抓取。
