# 133 · 被 `include 的 `.sv` 被当成独立编译单元，导致 module 重复定义

## 现象

老项目常见写法：``include "defs.sv"`` 里放 helper module。

```
defs.sv   module helper_mod ...
top.sv    `include "defs.sv"   module dupinc_top ... helper_mod u (...)
```

`resolve_deps(project_root=..., target_module="dupinc_top")` 返回

```json
{"rtl_files": ["defs.sv", "top.sv"], "all_files": ["defs.sv", "top.sv"]}
```

调用方把 `rtl_files` 当编译单元列表喂给 Verilator：

```
%Error: Exiting due to 1 warning(s)
（MODDUP: defs.sv:1:8: Duplicate declaration of module: 'helper_mod'）
```

Verilator 默认 warning-as-error，这是**硬失败**。工具链侧 `ok=True`。

## 根因

`index_repo` 只按扩展名区分 RTL（`.v/.sv/.vh/.svh`）与 header（`.vh/.svh`），
完全没有「这个源文件是被别人 `include 进去的」这一信息，于是 `.sv`
形式的 include 目标被同时算作 header 与编译单元。

## 修复

1. `index_repo` 新增 `included_files`：扫描所有文件的 `` `include "..." ``，
   按相对路径（回退 basename）解析到实际文件；**只记录非 header 扩展名**
   （`.vh/.svh` 天生属于 `header_files`，不需要再标注）。
2. `resolve_deps` 用 `rtl_files = sorted(rtl_set - incl_targets)` 剔除这些
   文件，`all_files` 保持不变 —— 沙箱仍需复制它们，否则 include 找不到。

## 验证

- `defs.sv`（被 include）+ `top.sv`：`rtl_files == ["top.sv"]`、
  `all_files == ["defs.sv", "top.sv"]`、`included_files == ["defs.sv"]`。
  监听：`dependency_closure` 仍完整覆盖 `helper_mod`。
- 真实项目无回归：`examples/multi_bus_ref` 各 DUT 的 `rtl_files` 仍为单文件、
  `included_files` 为空；头文件型 include 不算进 `included_files`。
- 编译验证：`verilator --lint-only -I. top.sv` 零 error 零 warning（修复前
  把两个文件都当源文件会 MODDUP）。
- 回归测试：`test_included_source_not_treated_as_compilation_unit`。
