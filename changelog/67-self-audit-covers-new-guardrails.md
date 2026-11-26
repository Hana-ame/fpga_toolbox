# Changelog — 自审计矩阵扩展到 A15–A18，并修掉「探针一崩就整个审计崩」

## 背景

`audit_harness` 的定位是「给护栏本身做故障注入，证明它们出错时不会静默通过」。
但最近几轮修的缺陷（变异 baseline、套件身份、报告自洽、畸形 results）**都不在
矩阵里** —— 也就是说，这些护栏如果将来被回退，自审计本身发现不了，只能靠
回归测试套件。自审计矩阵是"护栏的护栏"，覆盖面上有洞，这个工具的核心承诺就
打了折扣。

## 修复 1：新增 A15–A18

| 项 | 验证什么 | 对应缺陷 |
|---|---|---|
| **A15** | `run_mutation` 有 baseline 预检：坏 test_cmd（永远非零）必须被拦、不能报假 100%；同时验证「baseline 通过 + 真杀掉变异」的正向路径确实给出 `killed=1 evaluated=1 score=100` | 113 |
| **A16** | 套件打印 `[CHECK] ... status=PASS` 后崩溃不算通过；正常跑完（有自身 `PASS:` 汇总行）才算 | 117 |
| **A17** | 审计报告 `summary` 与 `overall_status` 和 `checks` 矛盾时必须给 W052/W053、`consistency_ok=False` | 120 |
| **A18** | 畸形 results 必须报错而不是静默 NO_RESULTS：`tests: [非对象]` / `checks: [非对象]` → `ok=False` 且不得是裸 `AttributeError`；无名 `<testcase/>` → `overall_status=FAIL` | 119 |

矩阵从 14 项扩到 **18 项，18/18 通过**。

## 修复 2：探针必须容忍「被测护栏彻底坏掉」

加完 A18 后做了一次**回退验证**（临时把缺陷 119 的修复去掉，看 A18 是否报 FAIL）：

```
AttributeError: 'str' object has no attribute 'get'
```

整个 `audit_harness` **崩了**，调用方拿到的是 traceback，而不是「A18 失败」。

这是个真问题：自审计的全部价值在于「护栏被回退时必须报 FAIL」。如果探针在护栏
坏掉时自己抛异常，那它就没法履行这个职责 —— 而且崩溃比 FAIL 更糟（FAIL 至少
指出是哪一项、为什么；崩溃只说"某处出错了"）。

修复：加 `_audit_call(fn)` 包装器，把**任意异常**折算成 `(None, "Type: msg")`，
由各项检查自己判 `_e is None` 并写进 detail。A15/A17/A18 全部改用它。

再验证一次回退（去掉 baseline 预检）：

```
ok=False failed=['A15_MUTATION_BASELINE_PRECHECK']
detail: raised=["NameError: name 'baseline' is not defined", ...]
```

干净地报 FAIL 且 detail 指名了异常类型与消息。

## 文档同步

- `README.md`：`A1–A14` → `A1–A18`
- `docs/FEATURES.md`：JSON 示例 `total: 10` → `18`；能力表补齐 A11–A18 一行一项
- `docs/USER_MANUAL.md`：「运行 13 项」→「运行 18 项」

（顺带发现 FEATURES.md 的能力表原本只列到 A10，A11–A14 一直没写 —— 属于文档滞后，
这次一并补全。）

## 验证

- `audit_harness` **18/18**（strict 模式）
- 两次回退验证：去掉缺陷 119 / 缺陷 113 的修复后，A18 / A15 分别干净报 FAIL
  而不是崩溃
- 4 个测试套件全过 / ruff 0 错 / `verify_invariants --full` **6 层全过**
  （145/145 函数覆盖）/ 本地 `docs_check.py`（D1–D13）全绿 / docs HTML 同步
