# 147 · `import pkg::*` 依赖的 package 文件不在依赖闭包里

## 现象

```
pkg.sv    package cfg_pkg; parameter int W = 8; endpackage
user.sv   module user_mod (...); import cfg_pkg::*; ...
top.sv    module top_mod (...); user_mod u (...);
```

```
resolve_deps(target_module="top_mod")
→ {"dependency_closure": ["top_mod", "user_mod"],
   "rtl_files": ["top.sv", "user.sv"],
   "all_files": ["top.sv", "user.sv"],
   "closure_is_complete": true}
```

`pkg.sv` 完全不见。拿这份清单去 Verilator 编译：

```
%Error: user.sv:2:10: Import package not found: 'cfg_pkg'
%Error: Exiting due to 1 error(s)
```

工具全程 `ok=true`。

**这不是构造出来的场景**：仓库自带的示例
`examples/axi_apb_ref/axi/axi_apb_bridge.sv` 单文件编译就是

```
%Error: axi/axi_apb_bridge.sv:63:12: Import package not found: 'axi_defs_pkg'
```

而旧的 `resolve_deps(target_module="axi_apb_bridge")` 正好只返回
`["axi/axi_apb_bridge.sv"]`。

## 根因

`index_repo` 只索引 module / interface / header / `include`，**完全没有
package 概念**。`import cfg_pkg::*` 与 `` `include `` 的语义不同：被 include 的
文件是文本展开（不能作为独立编译单元），而 package 通常定义在**自己的文件**
里，必须作为独立编译单元一起编译。

## 修复

1. `index_repo` 新增：
   - `packages`：`{package_name: 相对路径}`（用 `^\s*package\s+NAME` 匹配定义）；
   - `modules[mod].package_imports`：该模块文件 `import X::` 的 package 列表。
2. `resolve_deps`：闭包内模块 import 的 package 文件加入 `rtl_files` /
   `all_files`。package 之间也能互相 import，因此做了闭包展开（BFS 直到稳定）。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `top_mod` 闭包 | `["top.sv","user.sv"]` | `["pkg.sv","top.sv","user.sv"]` |
| 沙箱编译（复制后） | `Import package not found` | 0 error / 0 warning |
| `solo`（不 import） | `["solo.sv"]` | `["solo.sv"]`（不误拖 package） |
| 仓库真实示例 `axi_apb_bridge` | `["axi/axi_apb_bridge.sv"]` | 额外含 `axi_defs_pkg.sv` + `apb_defs_pkg.sv` |

其他真实项目无回归：`ahb_lite_slave` / `ddr3_mem_ctrl` 仍单文件；先前修复的
`top`（传递 header）、`dupinc_top`（被 include 的源文件）、`dup_mod`
（独立目标）行为不变。

回归测试：`test_package_imports_are_part_of_the_dependency_closure`。
