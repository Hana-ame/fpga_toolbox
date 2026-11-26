# Changelog — 审计报告自检不看数据自洽性 + 畸形 report 抛裸 AttributeError

## 1. `render_visual_report` 的自检只查 HTML 标签，不查数据

这个工具的职责是「生成一份 FPGA 验证审计报告」，它自带 `_check_html_consistency`
自检机制 —— 但那个自检只查：

- `<table>` / `<tbody>` / `<html>` / `<body>` 标签是否配平（W050）
- 渲染出来的行数是否等于 `len(checks)`（W051）

**完全不检查 report 数据本身是否自相矛盾。** 于是下面四种报告全部
`ok=True`、`consistency_ok=True`、零诊断，被原样印进 HTML：

```
summary: {"total": 2, "passed": 2, "failed": 0}
checks:  [{rule_id: A, status: FAIL}]                    -> 无诊断（修复前）
```

读者打开报告，头部写着「通过率 100% / 2 2 PASS」，下面的表格里全是红色 FAIL ——
无从判断该信哪个。这是审计报告最核心的职责面，漏检等于报告本身不可信。

### 修复：新增 W052 / W053

- **W052**：`summary.total` / `summary.passed` / `summary.failed` 与 `checks`
  实际统计不符（只在调用方**声明了**该字段时核对，缺字段不算矛盾）。
- **W053**：`overall_status` 与行级结论矛盾
  - `PASS` 但没有任何检查通过
  - `FAIL` 但没有任何 FAIL（且无 SKIP）

只报 WARNING 而不改 `ok` —— 调用方可能故意渲染「外部汇总 + 本机明细」的混合
报告；但矛盾必须**可见**，不能静默印出去。

实测：

| report | 修复前 | 修复后 |
|---|---|---|
| summary.passed=2 而 checks 全 FAIL | consistency_ok=True | False + 3×W052 |
| summary.total=5 而实际 1 条 | True | False + W052 |
| overall=PASS 但行全 FAIL | True | False + W053 |
| overall=FAIL 但行全 PASS | True | False + W053 |
| 自洽报告 | True | True（不误报） |

## 2. 畸形 `report` 抛裸 `AttributeError`

`report` 是外部（或 AI）生成的 JSON，任何嵌套字段的形状都可能不对。旧实现一路
`c.get(...)` 下去，六种畸形输入全部得到同一句无信息量的异常：

```
report.checks              = ["oops"]                 -> "'str' object has no attribute 'get'"
report.checks              = [None]                   -> "'NoneType' object has no attribute 'get'"
report.project_pages       = ["x"]                    -> 同上
report.capability_matrix   = ["x"]                    -> 同上
report.summary             = "x"                      -> 同上
report.mutation_summary.results = ["x"]               -> 同上
```

报错里既没有字段名也没有索引。修复：在渲染**之前**统一校验会参与渲染的集合/映射
字段（`checks` / `project_pages` / `capability_matrix` / `summary` /
`coverage_summary` / `mutation_summary` / `mutation_summary.results` /
`dep_graph.dependency_closure`），给出可定位的结构化错误：

```
report.checks must be a list of objects; element(s) at index [0] are not
objects (got types ['str']).
```

这与上一轮 `normalize_sim_result` 的修复是同一口径（同一类问题在两处出现）。

## 验证

- 4 个测试套件全过 / ruff 0 错 / `verify_invariants --full` **6 层全过**
  （145/145 函数覆盖）/ docs HTML 同步。
- 新增回归 `test_visual_renderer_consistency_and_shape_validation`：四种数据矛盾
  必须给诊断、自洽报告不误报、六种畸形形状给可定位错误且不得出现
  `attribute 'get'`。
