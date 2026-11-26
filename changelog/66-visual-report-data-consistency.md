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

## 附：新增诊断码漏登记 + 本地门禁盲区（CI 暴露）

首次推送后 **4 个 test job 全红**，失败点不是代码而是文档门禁：

```
✗ D13 2 个诊断码文档未提及：W052, W053
```

新增的 W052/W053 没有写进 `docs/FEATURES.md` 的诊断码表。这暴露了一个**流程
盲区**：本地 `verify_invariants --fast` 的 Layer 5 只查「13 个工具都在
USER_MANUAL 里」「HTML 同步」「无死链」，**不查诊断码登记** —— 那条检查只在
CI 的 `examples/axi_apb_ref/axi/docs_check.py`（D13）里跑。于是本地全绿、CI 全红。

修复（两层）：

1. **补文档**：`docs/FEATURES.md` 增加 W052/W053 两行；`README.md` 的
   `W050–W051` 改 `W050–W053`；`docs/USER_MANUAL.md` 的 `W001–W051` 改
   `W001–W053`。
2. **把 D13 前移到本地门禁**：`verify_invariants.py` Layer 5 增加第 4 项检查，
   与 `docs_check.py` 的 D13 严格同口径 ——
   - 「声明」取包内**字符串字面量** `"E0xx"`/`"W0xx"`（不用宽松 `\b`，否则
     注释里提到的码也算）；
   - 文档侧扫 `README.md` + 全部 `docs/*.md`；
   - 认**范围概括**写法（`E000–E015` 视为覆盖区间内所有码），否则
     USER_MANUAL 用范围列举时会被全部误判为缺失。

   验证该检查真有效：临时从 FEATURES.md 删掉 W052/W053 → 检查报
   `['W052', 'W053']`；恢复后通过（43 个诊断码全覆盖）。

这条流程修复的价值大于单个缺陷：以后「加了诊断码忘了写文档」在本地就会红，
不必等 CI。
