# 157 / 158 · `copy_sandbox` 的参数描述与真实行为/默认值不符

两处都在 `copy_sandbox` 的 MCP schema 描述里，直接影响调用方（尤其是 AI agent）
对破坏性参数的理解。

## 157 · `force` 描述把语义写反了

```
旧: "Allow cleaning a directory that carries the sandbox marker."
```

读起来像「`force` 用来清理**带标记**的目录」。真实行为完全相反：

```python
if not marked and not force:
    return err_result(... "Pass force=True if you really intend to wipe it.")
_clear_dir(dst_root)
```

- **有**标记 → 本来就允许清理，不需要 `force`
- **没有**标记 → 只有 `force=True` 才会清空，而那很可能是用户自己的目录

按旧描述理解，调用方会以为 `force` 与「已标记沙箱」有关，从而在没有任何
保护提示的情况下清空用户文件。

修复：

```
新: "DESTRUCTIVE: allow cleaning a non-empty directory that has NO sandbox
     marker (i.e. possibly the user's own files). Only pass when you truly
     intend to wipe that directory."
```

## 158 · `clean` 描述说 `default false`，实际是 `True`

```
旧: "Clean an existing marked sandbox directory before copying (default false)."
签名: def tool_copy_sandbox(..., clean: bool = True, ...)
```

实测：已标记沙箱里放一个 `stale.sv`，**不传** `clean` 调用一次，`stale.sv`
被删除。用户按描述会以为「默认不清理」，实际每次调用都会清空沙箱。

修复：

```
新: "Clean an existing MARKED sandbox directory before copying (default TRUE:
     omitting this parameter wipes stale files from a previously created marked
     sandbox). Unmarked non-empty directories are always refused unless
     force=true."
```

## 附带：加了通用一致性审计

新增回归测试对所有 13 个工具的 schema 描述做扫描：凡描述里写了
`default true/false` 或 `default <数字>` 的参数，默认值必须与函数签名一致。
当前 0 处不匹配（`clean` 修好后）。

这类「文档说 A、代码做 B」的偏差此前只在 `sandbox_copier` 模块 docstring
（"unless the caller explicitly passes force=True"，是对的）与
`docs/FEATURES.md`（也对）里正确记录，唯独最贴近调用方的 MCP schema 描述
是错的。

回归测试：`test_tool_schema_descriptions_match_signature_defaults`。
