# Changelog — `resolve_deps` 带路径前缀的 `include` 解析（防误报 W101 + 漏拷 header）

## 背景

`index_repo` 的 `headers` 按**文件名 basename** 建索引：`defs.svh` -> `inc/defs.svh`。
但 RTL 里的 `` `include 常带**路径前缀**（`` `include "inc/defs.svh"``、
`` `include "rtl/a/defs.svh"``），`_INC_RE` 会原样捕获成 `inc/defs.svh`。

`tool_resolve_deps` 旧实现直接 `hdrs[inc]` 查这个带前缀的字符串，**查不到** -> 把
真实存在的 header 当成缺失：

- 误报 `W101 Header 'inc/defs.svh' not found`
- 把它**排除在 `header_files` / `all_files` 之外**

## 为什么是真缺陷（不只是诊断噪声）

`resolve_deps` 的 `all_files` 是 `copy_sandbox` 复制沙箱的输入清单。header 被漏掉
后，沙箱里的 RTL 编译会报 `Cannot find include file: 'defs.svh'` —— 一个**实际存在
于仓库**的 header 因为 include 写法带了路径前缀就被排除，沙箱静默不可编译。

## 实测复现

```
inc/defs.svh  (parameter W = 8;)
top.sv        (`include "inc/defs.svh"  ...)

resolve_deps("top"):
  修复前  header_files=[]  all_files=["top.sv"]  diagnostics=[W101 'inc/defs.svh' not found]
  修复后  header_files=["inc/defs.svh"]  all_files=["inc/defs.svh","top.sv"]  diagnostics=[]
```

## 修复

`fpga_toolbox/repo_indexer.py`：

- 新增 `_resolve_include(inc, hdrs)`：先按 include 的 basename 查 `headers` 的
  key，再退回完整 include 字符串查 value（覆盖 headers 恰好按带前缀相对路径键入
  的索引）。命中返回仓库相对路径，找不到返回 `None`。
- `tool_resolve_deps` 的 header 循环改用它，`None` 才记 `W101`。

## 行为保持

- 无前缀 include（`` `include "defs.svh"``）仍按 basename 命中，行为不变
- 真正缺失的 header（`` `include "nowhere.svh"``、`` `include "sub/missing.svh"``）
  仍报 `W101` 且不进 staged 清单 —— 两种写法都实测验证

## 测试

`tests/test_full_coverage.py::test_resolve_deps_path_prefixed_include`：

- 带前缀 include 的 header 进 `header_files` / `all_files`，且无 W101
- 两个真正缺失的 header（basename + 带前缀）都报 W101，`header_files` 为空

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
- `verify_invariants.py --fast` 5/5
