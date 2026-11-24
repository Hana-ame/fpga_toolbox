# Changelog — boolean 参数不接受字符串（与数字参数待遇不一致）

## 背景

排查 `run_sim` 新增参数的类型边界时发现不一致：`timeout_sec="1700"`（字符串数字）
被接受（changelog 64/26 的兼容设计），但 `waveform="true"`（字符串布尔）被
`Invalid type` 拒绝。

## 缺陷 96 — boolean 参数不接受字符串

实测：

```
$ run_sim --waveform "true"
Invalid type for parameter 'waveform' of tool 'run_sim':
expected boolean, got str.
```

而同一调用方的 `timeout_sec="300"`（数字字符串）是接受的。MCP/LLM 客户端常把
bool 序列化成字符串（LLM 生成的工具调用尤甚），这条不一致会让这类调用方在
`waveform` / `require_waveform` / `clean` / `force` / `strict` 等所有 boolean
参数上全部失败。

## 修复

`call_tool` 的类型校验给 boolean 加字符串兼容，与数字类参数的既有宽容设计对齐：

```python
if ptype == "boolean" and isinstance(val, str):
    _low = val.strip().lower()
    if _low in ("true", "1", "yes"):  params[pname] = True;  continue
    if _low in ("false", "0", "no"):  params[pname] = False; continue
    return err_result(... Expected a boolean or one of
                       'true'/'false'/'1'/'0'/'yes'/'no'.)
```

**关键约束**：只接受**明确的布尔字面量**（大小写不敏感），其他字符串仍明确拒绝
（`Invalid value for boolean parameter ...`）——**绝不静默转成 False**，那会
静默关掉 waveform 等功能。

## 验证

| 输入 | 结果 |
|---|---|
| `true` / `TRUE` / `1` / `yes` | 接受 → True |
| `false` / `False` / `0` / `no` | 接受 → False |
| `yes please` / `enabled` / `""` | 明确拒绝（`Invalid value for boolean ...`）|

覆盖所有 boolean 参数（`waveform` / `require_waveform` / `clean` / `force` /
`strict` / `require_slang`）。

## 反向验证

移除 boolean 兼容分支 → 3 项"非布尔字符串仍拒绝"断言 FAIL
（`yes please` / `enabled` / `""` 泄漏）。

## 回归测试

`test_call_tool_enum_and_type_validation` 内新增 11 项断言（8 个合法布尔字符串
+ 3 个非法字符串仍拒绝）。

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 11 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
tests/test_full_coverage.py           ALL FULL-COVERAGE TESTS PASSED
audit_harness                         13 / 13
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```
