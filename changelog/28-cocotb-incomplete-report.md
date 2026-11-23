# Changelog — cocotb 中途崩溃的残缺报告被判 PASS

上一轮修了 SVA 链的「零输出无法区分通过/没跑」。本轮把同一思路套到 cocotb 链，
发现一个不同形态但同性质的问题。

## 缺陷 — 测试崩在第 1 条断言，报告仍判 PASS

cocotb 平台的记录函数每写一条就落盘：

```python
def _rec(rid, cat, ok, detail):
    REPORT["checks"].append({...})
    Path("test_results_{mod}.json").write_text(json.dumps(REPORT, indent=2))
```

但 `REPORT` 里**没有任何「已跑完」的信号**。一次完整的 cocotb 运行有 7 个 `_rec`
调用点（reset / rw_write_readback / ro_immutable / illegal_addr / stress /
STRESS_SUMMARY 等）。若测试中途崩溃（异常、超时、某个 `await` 卡死），JSON 里
只留下前面几条：

| JSON 内容 | 旧实现结果 |
|---|---|
| 完整 5 条（含 STRESS_SUMMARY） | PASS |
| 只有 1 条 reset PASS（第 2 条就崩了） | **PASS（假通过）** |

两者无法区分。这与 SVA 的问题同类——**残缺的产出被当成了完整的结论**。

## 修复

**生成侧**：`REPORT` 增加 `completed` 字段，初值 `False`，Phase 5 drain 结束后
置 `True` 并落盘：

```python
REPORT = {"module": ..., "seed": ..., "completed": False, "checks": []}
...
# Phase 5: drain & finalize
REPORT["completed"] = True
Path("test_results_{mod}.json").write_text(...)
```

**归一化侧**：`_from_json` 返回 `completed` 三态，归一化器见到 `completed is False`
就合成一条 FAIL：

```
COCOTB_INCOMPLETE  cocotb_run_integrity  FAIL
cocotb report has completed=false: test aborted before all phases finished
(1 check(s) recorded). Treat as failure, not partial pass.
```

关键取舍：**旧格式（无 `completed` 字段）保持 PASS**，不误判。否则所有外部生成的
JSON 报告都会突然失败——那不是一个修复，是一次破坏。只有产出方显式声明
`completed=false` 才判定为失败。

## 验证

| JSON | 结果 |
|---|---|
| `completed: true` | PASS |
| `completed: false`（崩溃）| **FAIL** + `COCOTB_INCOMPLETE` |
| 无 `completed` 字段（旧格式）| PASS（向后兼容）|

- 生成的 cocotb TB 含初值 `False` 与终值 `True`，`py_compile` 通过
- 新增 8 项断言（`test_cocotb_incomplete_report_is_failure` /
  `test_generated_cocotb_tb_writes_completion`）
- regression / fusion / eda_campaign / audit 10-10 / docs_check D1–D13 / ruff /
  py3.10 全绿
