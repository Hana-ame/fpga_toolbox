# 161 · `staged_rtl_files` 从未真正传给解析器

## 现象

`extract_interfaces` 的参数描述承诺：

> Optional additional RTL files compiled together (e.g. packages) for
> cross-file imports.

实测这个参数**对解析结果零影响**，而且完全不做校验：

```
staged_rtl_files=["/tmp/nope.sv"]   → ok=true
staged_rtl_files=["/tmp"]           → ok=true   （目录也接受）
staged_rtl_files=["valid.sv"]       → ok=true   （但解析结果与不传完全一致）
```

## 根因

grep 该参数的全部出现位置：

```
iface_extractor.py:418    staged_rtl_files: Optional[List[str]] = None,     # 形参
iface_extractor.py:468    "rtl_files": staged_rtl_files or [m0["rtl_file"]], # 只写进返回值
```

它**从来没有**传给 `parse_sv_file` / `_find_slang_context`，只被原样回显到
输出 JSON 的 `rtl_files` 字段里，制造出「已经用它编译过了」的假象。

而 slang 的自动收集（`_find_slang_context`）只认 `*_pkg.sv` / `*_if.sv`
两种命名 —— 命名不规范的 package/interface 定义文件（如 `types.sv`）本该
靠这个参数补充，实际补不上。

## 修复

1. `_find_slang_context(rtl_path, extra_staged=None)`：把调用方指定的文件加入
   slang 的同批编译文件列表（去重、跳过目标文件自身）。
2. `_parse_with_slang(..., staged_rtl_files)`、`parse_sv_file(...,
   staged_rtl_files)` 逐层透传。
3. `extract_interfaces` 入口校验每个条目必须是**存在的文件**：
   - 不存在 → `Invalid staged_rtl_files entries: /tmp/nope.sv (not found)`
   - 是目录 → `... (/tmp) (directory)`
   - 相对路径按三种基准解析（rtl 所在目录 → rtl 父目录 → cwd），命中即用，
     避免把合法的 project-root 相对路径误判成不存在。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `staged_rtl_files=["/nope.sv"]` | `ok=true` | `(not found)` 错误 |
| `staged_rtl_files=["/tmp"]` | `ok=true` | `(directory)` 错误 |
| 真实 package 文件（`types.sv`，非 `*_pkg.sv` 命名） | 解析上下文不含它 | 出现在 `_find_slang_context` 的 extra_files |
| project-root 相对路径 | 静默接受但无效 | 正确解析并生效 |

`test_regression.py` 里那条「index → deps → sandbox → contract → tb → report」
全链路测试用的正是 project-root 相对路径（`rtl/reg_slave.sv`），修复过程中
一度被新校验误伤 —— 最终的三级路径解析把它恢复为全绿。

回归测试：`test_staged_rtl_files_reach_the_parser`。
