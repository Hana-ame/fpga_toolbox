# 重构 · 按领域拆分 `tests/test_full_coverage.py`（5530 行 → 7 个套件）

## 问题

`tests/test_full_coverage.py` 单文件 **5530 行 / 86 个用例**，是历史累积的
"什么都往里塞"的大杂烩：

- 定位失败要先通读全文——文件名 `test_full_coverage` 只说"覆盖全"，不说
  覆盖什么；
- CI 里是**一个** step，任一用例失败都只给一个红叉，看不出是解析器、TB
  模板还是沙箱的问题；
- 局部改动要跑 86 个用例才能确认没波及，反馈回路长；
- 新人无从下手，只能继续 append，正反馈放大膨胀。

## 方案

按**领域**拆成 7 个套件，只搬移代码、**不改任何断言**：

| 文件 | 用例 | 覆盖内容 |
|---|---|---|
| `test_sv_parser_coverage.py` | 12 | builtin/slang 双后端、位宽、typedef、localparam、reset 极性 |
| `test_protocol_emitter_coverage.py` | 18 | 7 协议 TB 模板、端口位宽跟随、握手大小写、参数化 |
| `test_iface_binding_coverage.py` | 9 | 11 个分类器、方言前缀、canonical 绑定、`split_port_map` |
| `test_sim_and_normalizer_coverage.py` | 13 | suite 身份、伪 PASS 拦截、日志去重、summary 类型校验 |
| `test_security_coverage.py` | 13 | 路径逃逸、注入/XSS、变异工具、数值越界 |
| `test_repo_and_sandbox_coverage.py` | 11 | 仓库索引、依赖闭包、沙箱拷贝（含原子性） |
| `test_tooling_coverage.py` | 10 | CLI、registry schema、可视化、网表清洗、门禁自身 |

共享辅助 `_available_backends()` 抽到 `tests/_testkit.py`。文件名**不以
`test_` 开头**——门禁 Layer 4 用 `glob("tests/test_*.py")` 收集套件，辅助
模块不应被当成套件执行。

拆分用 AST 解析原文件、按 `lineno`/`end_lineno` 原样切片，脚本内断言
`sorted(assigned) == sorted(tests)`，保证 86 个用例**不重不漏**。

## 收益

- 单文件最大 1324 行（原 5530 行），平均 ~810 行；
- CI 拆成两个 step（3 + 4 个套件），失败时按领域直接定位；
- 可单独跑出问题的那一类：`python3 tests/test_security_coverage.py`；
- 覆盖率统计不变：门禁会跑遍所有 `tests/test_*.py`，函数覆盖仍 **170/170**。

## 验证

- 7/7 套件独立运行通过（共 86 个用例）；
- 六层门禁 6/6：L4 `170/170 functions across 22 modules`，
  L5 `48 diagnostic codes documented`；
- ruff 0 errors（拆分后各文件的未用 import 已清理）；
- 顺带修掉 3 处 docstring 里的裸反斜杠 `SyntaxWarning`
  （`invalid escape sequence`），改成 raw string。
