# 189 · `coverage_summary` / `mutation_summary` 的非法字段原样透传

## 现象

```python
normalize_sim_result(
    verilator_log_path="a.log",
    coverage_summary={"line": {"x": 1}},          # 应当是数字
    mutation_summary={"mutation_score": "abc"},   # 应当是数字
)
→ ok=true, diagnostics=[]
```

两个字段被**原样塞进报告**：

```json
{"coverage_summary": {"line": {"x": 1}},
 "mutation_summary": {"mutation_score": "abc"}}
```

于是报告里出现「行覆盖率 = 一个 dict」「mutation_score = `'abc'`」，带着非法
类型一路流到 `render_visual_report`。渲染器恰好容错没崩（缺陷 120 已加固），
但**报告数据本身已经失真** —— 下游任何认真做数值比较的消费者都会出错。

## 根因

```python
if coverage_summary is not None:
    report["coverage_summary"] = coverage_summary      # 直接透传，零校验
if mutation_summary is not None:
    report["mutation_summary"] = mutation_summary
```

`call_tool` 的 schema 只校验「整体是 object」，不校验内部字段类型。

## 修复

对上游最关键的数值字段做类型检查，**非法值丢弃该字段**并逐条报 `W014`
（沿用「值被忽略」的既有诊断码），合法字段保留：

```python
for _k in ("line", "branch", "toggle", "fsm", "expr", "total"):   # coverage
for _k in ("mutation_score", "score", "killed", "survived",
           "total", "evaluated"):                                  # mutation
```

## 验证

| 输入 | 修复前 | 修复后 |
|---|---|---|
| `coverage_summary.line = {"x":1}` | 原样进报告，无诊断 | 字段丢弃 + `W014 coverage_summary.line` |
| `mutation_summary.mutation_score = "abc"` | 原样进报告 | 字段丢弃 + `W014 mutation_summary.mutation_score` |
| 合法字段（`branch=50.0`、`killed=3`） | 保留 | 保留 |
| 全部合法 | — | 无 W014 假报 |

四套测试全绿；六层门禁 6/6，函数覆盖 **170/170**。

回归测试：`test_summary_payloads_are_type_checked`。
