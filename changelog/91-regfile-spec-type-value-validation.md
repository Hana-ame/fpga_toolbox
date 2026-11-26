# 150 / 151 / 152 · 手写 regfile spec 的类型错误会崩栈、数值错误被静默吞掉

三处都在 `_normalize_regfile_spec_v2`（把 `apb-regfile-spec/v2` 归一成统一 IR）。

## 150 · `registers` 写成 mapping → 裸 `AttributeError`

```json
{"$schema": "apb-regfile-spec/v2", "registers": {"CTRL": {"offset": "0x0"}}}
```

归一化代码 `for idx, r in enumerate(data.get("registers", []))` 迭代到的是**键
字符串**，随后 `r.get(...)` 抛：

```
'str' object has no attribute 'get'
```

未捕获异常漏出，`ok=false`，但错误信息与真正原因（registers 该是数组）毫无关联。

修复：先校验 `registers` 是 list，再逐项校验元素是 dict，给出带下标的明确信息：

```
apb-regfile-spec: 'registers' must be a list of register objects, got dict.
apb-regfile-spec: registers[0] must be an object, got str.
```

## 151 / 152 · 非法 `offset` / `reset` 被静默回退

```
{"registers": [{"name": "R", "offset": "zzz", "reset": "zz", "attr": "RW"}]}
→ ok=true, diagnostics=[], register_count=1
```

根本原因是 `_to_int(val, default)` 的**宽容设计**（缺陷 103 为 addr_width 之类
引入的）被用在了必须能解析的字段上：`offset` 悄悄变成 `idx*4`、`reset` 悄悄变成
`0`。寄存器位置和复位值都是仿真判据，静默改值是**假通过**。

修复：这两个字段改用 `_to_int_or_none`，解析失败时收集起来，经
`__spec_warnings__` 通道带回 `build_and_validate_ir`，转成 `E005` ERROR：

```
E005  BitfieldSpec: invalid offset value(s) R='zzz' — expected an integer or
      0x-prefixed hex; fell back to index*4.
E005  BitfieldSpec: invalid reset value(s) R='zz' — expected an integer or
      0x-prefixed hex; fell back to 0.
```

`addr_width` / `data_width` / `wait_cycles` 等**有明确默认值**的字段保持宽容
（沿用缺陷 103 的设计，避免 `"abc"` 变裸 ValueError）。

## 验证

| spec | 修复前 | 修复后 |
|---|---|---|
| `registers` 是 mapping | `AttributeError: 'str' object has no attribute 'get'` | `must be a list ... got dict` |
| `registers` 元素是字符串 | 同上 | `registers[0] must be an object, got str` |
| `offset: "zzz"` | `ok=true`，零诊断 | `ok=false` + `E005` |
| `reset: "zz"` | `ok=true`，零诊断 | `ok=false` + `E005` |
| 合法 spec | `ok=true, register_count=2` | 不变 |

自审计 `audit_harness` 仍 19/19；四套测试全绿。

回归测试：`test_regfile_spec_type_and_value_validation`。
