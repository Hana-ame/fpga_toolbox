# Changelog — `lint_tb` S1 覆盖 SV 字面量常量 stub（防漏报静默通过 helper）

## 背景

`tb_linter.scan_handshake` 的 **S1** 规则检出「验证 helper 函数只返回常量（stub）」
—— 这种函数永远返回固定值，不含任何真实判定，是静默通过的一种形态。判定口径是
保守的：`return` 一个**常量字面量** + 函数体无 `if/else/case/for/while` 分支。

旧正则：`^return\s+[-]?\d+\s*;` —— **只认纯整数**（`return 0;` / `return -1;`）。

## 为什么是真缺陷（假阴性，静默通过漏报）

SV 里最地道的常量 stub 写法全部漏报：

```
return 1'b0;     -> 修复前漏报，修复后 S1 ✓
return '0;       -> 修复前漏报，修复后 S1 ✓
return 8'hFF;    -> 修复前漏报，修复后 S1 ✓
return 0.0;      -> 修复前漏报，修复后 S1 ✓
return 0;        -> 一直报（对照）
```

这些语义上与 `return 0;` **完全等价**——helper 永远返回固定值、不含任何真实判定。
lint 对它们说「没问题」，而实际这是一个没有判定的验证 helper（静默通过的根源）。
方向是**假阴性**，与缺陷 105 同类。

## 修复

`fpga_toolbox/tb_linter.py`：把 S1 的常量正则从纯整数扩成覆盖：

- 纯整数 `[-+]?\d+`（原有）
- 位宽字面量 `[-+]?\d*'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ_]+`（`1'b0` / `8'hFF`）
- 未位宽 `'[01xXzZ]`（`'0` / `'1`）
- 实数 `[-+]?\d+\.\d+`（`0.0`）

仍保留既有的「函数体无分支」守卫（`len(body) <= 4` 且无 `if/else/case/for/while`），
且字面量必须紧跟 `return`、后面直接是分号——含标识符/运算符的表达式不匹配。

## 不误伤（回归验证）

- `return a + 1;`（表达式）→ 不报
- `return a == 0;`（比较表达式）→ 不报
- `if (a == 0) return 1'b1; else return 1'b0;`（含分支的真实判定）→ 不报

## 测试

`tests/test_full_coverage.py::test_s1_sv_literal_constant_stub`：

- 7 个常量 stub（纯整数 0/-1/1 + 位宽 `1'b0`/`8'hFF` + 未位宽 `'0` + 实数 `0.0`）
  全部报 `S1`
- 3 个合法函数（表达式 / 比较表达式 / 含分支）不误报 `S1`

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错（ruff 0.17.6 本地非系统安装）
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
- `verify_invariants.py --fast` 5/5
