# 135 · 剔除被 include 的源文件时把「独立可编译的目标」也剔掉了

## 现象

缺陷 133 的修法是 `rtl_files = rtl_set - included_files`。这个判据过于粗暴：

```
rtl/real.sv     module dup_mod (...)          -- 定义了 dup_mod
rtl/wrapper.sv  `include "real.sv"
                module wrapper (...) dup_mod u (...)
```

`resolve_deps(target_module="dup_mod")` 的闭包只有 `dup_mod` 一个模块，它的归属
文件正是被 include 的 `rtl/real.sv`：

```json
{"dependency_closure": ["dup_mod"], "rtl_files": [], "all_files": ["rtl/real.sv"]}
```

`rtl_files` 变成**空列表** —— 调用方把这份清单当编译单元列表，必然编译不出
`dup_mod`。这是 133 修复引入的回归（133 的测试只覆盖了「目标是 includer」
的方向，没覆盖「目标是被 include 方」）。

## 根因

`included_files` 只知道「这个文件被别人 include 了」，不知道**当前闭包里有没有
其它编译单元会展开它**。语义应该是：

> `rtl_files` = 闭包模块的归属文件，去掉那些**已经被闭包内其它编译单元
> include 展开**的。

- 目标 `dupinc_top`（`top.sv` include `defs.sv`，`defs.sv` 定义 `helper_mod`）：
  `defs.sv` 会被 `top.sv` 展开 → 剔除，`rtl_files == ["top.sv"]`（否则 MODDUP）。
- 目标 `dup_mod`（只定义在 `real.sv`，`real.sv` 被 `wrapper.sv` include，但
  `wrapper` **不在**闭包里）：闭包内没有东西会展开它 → 保留，
  `rtl_files == ["rtl/real.sv"]`。

## 修复

`resolve_deps` 改为按闭包计算：

```python
_inc_by_closure = { 被闭包内模块 include 的文件 }
"rtl_files": sorted(rtl_set - (_inc_by_closure & _closure_files))
```

`include` 目标既可能是 header（在 `hdrs` 里）也可能是源文件（不在 `hdrs` 里），
解析时两者都试：先 `_resolve_include`（header），再按相对路径 / basename 在
`included_files ∪ headers.values()` 中匹配。

## 验证

| 目标 | 修复前 `rtl_files` | 修复后 |
|---|---|---|
| `dupinc_top` | `["top.sv"]` | `["top.sv"]` ✅ |
| `helper_mod` | `["defs.sv"]` | `["defs.sv"]` ✅ |
| `wrapper` | `["rtl/wrapper.sv"]` | `["rtl/wrapper.sv"]` ✅ |
| `dup_mod` | `[]` ❌ | `["rtl/real.sv"]` ✅ |

真实项目无回归：`examples/multi_bus_ref` 四个 DUT 与 `axi_apb_ref` 的
`rtl_files` 均保持单文件。

回归测试：`test_rtl_files_keeps_independently_compilable_modules`。
