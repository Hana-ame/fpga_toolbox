# 132 · `resolve_deps` 丢失 header 之间的传递 include

## 现象

```
top.sv   `include "c.svh"
inc/c.svh   `include "b.svh"   `include "a.svh"
inc/b.svh   `include "a.svh"
```

`resolve_deps(project_root=..., target_module="top")` 返回：

```json
{"header_files": ["inc/c.svh"], "all_files": ["inc/c.svh", "top.sv"]}
```

`b.svh` / `a.svh` 完全消失，且 `diagnostics` 为空、`closure_is_complete=true`。

把这份清单喂给 `copy_sandbox` 后，沙箱只有 `top.sv` + `inc/c.svh`，编译立刻失败：

```
%Error: inc/c.svh:1:10: Cannot find include file: 'b.svh'
```

而整条链上每个工具都返回 `ok=True` —— 又一处「生成成功不等于产物正确」。

## 根因

`index_repo` 的 `headers` 只记录 `文件名 -> 仓库相对路径`，**没有任何 header
级的 include 关系**。`resolve_deps` 因此只能看到模块 `includes` 列表里的
那一层（模块自己 include 的），无法知道 `c.svh` 内部还 include 了 `b.svh`。

## 修复

1. `index_repo` 新增 `header_includes`：按 header 的仓库相对路径记录它自己
   的 `include` 列表（复用同一条 `` `include "..." `` 正则）。
2. `resolve_deps` 新增 `_collect_header()`，对每个直接 include 做深度优先展开：
   解析出 header 就加入清单并继续展开它的 include。
   - `seen_hdr` 按相对路径去重 → 循环 include（a 含 b、b 含 a）不会死循环。
   - 展开途中解析不到的 header 走同一条 `missing_inc` → 仍然报 W101，不会
     因为「传递」而静默吞掉缺失。

## 验证

- `top.sv -> c.svh -> b.svh -> a.svh`：修复后 `header_files` 为三个 header
  全齐，`all_files` 完整；`copy_sandbox` 复制后 `verilator --lint-only` 通过
  （rc=0）。
- 循环 include（x↔y）：正常返回 `["x.svh","y.svh"]`，不死循环、不重复。
- 传递链中缺 `ghost.svh`：报 `W101 / ghost.svh`。
- 回归测试：`test_resolve_deps_transitive_headers`。
