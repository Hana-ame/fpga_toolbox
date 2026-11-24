# Changelog — validator 非数字 offset 报 E005/E008 诊断而非 `int()` ValueError 冒泡

## 背景

契约 spec（boundary contract YAML/JSON）由用户 / AI 手写，`register_rules[].offset`
和 `illegal_addr_rules[].offset` 可能被写成**非数字**值：字符串 `"abc"`、浮点 `4.5`、
非法 hex `"0xZZ"`。旧实现 `_to_int` 里 `int(str(val), 0)` 直接抛 `ValueError` 冒泡。

## 为什么是真缺陷

1. **裸堆栈无定位**：用户只看到 `invalid literal for int() with base 0: 'abc'`，
   完全没指向是 `register_rules[0].offset` 写错了，要去翻堆栈猜。
2. **未落进诊断码体系**：同一个字段的其他非法输入（越界 `-1`）走的是 **E005**
   诊断路径；非数字输入却给裸堆栈 —— 同一字段的非法输入，一条给结构化诊断、
   一条给崩溃，不一致。
3. **两个规则类型都中招**：`register_rules` 与 `illegal_addr_rules` 都用 `_to_int`
   解析 offset，都崩。

## 实测复现

```
offset="abc":
  修复前  CRASH ValueError: invalid literal for int() with base 0: 'abc'
          at _to_int -> int(str(val), 0)
  修复后  diagnostics=[E005 Offset must be an integer, got 'abc'.,
                       E008 Offset must be an integer, got 'abc.']
```

## 修复

`fpga_toolbox/core/validator.py`：

- 新增 `_to_int_or_none(val)`：解析不出返回 `None`（**不抛**）；`bool`/`int`
  原样返回（注意 `isinstance(True, int)` 为真，需先处理 bool）；其余
  `int(str(val), 0)` 包 `try/except (ValueError, TypeError)`。
- `register_rules` offset 解析不出 → **E005** `Offset must be an integer, got
  {val!r}.` + `continue`（跳过对齐 / 重复判定，避免多个非法 offset 都落成 `-1`
  而级联出**假 E004 重复**）。
- `illegal_addr_rules` offset 解析不出 → **E008** 同构消息 + `continue`。
- 合法 hex 字符串 offset（`"0x4"`）仍正常解析，**不误报** E005。

## 测试

`tests/test_full_coverage.py::test_validator_bad_offset_no_crash`：

- 三类非法 offset（`"abc"` / `4.5` / `"0xZZ"`）→ 都产出 E005 + E008，消息含
  `must be an integer`，**不崩**
- 合法 hex offset（`"0x4"`）→ 不误报 E005

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
- `verify_invariants.py --fast` 5/5
- CI 6/6 success（run `45470982220`，headSha `a9c209e`）
