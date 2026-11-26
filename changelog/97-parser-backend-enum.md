# 160 · `parser_backend` 描述列举了取值却没有 enum，非法值被静默忽略

## 现象

`extract_interfaces` 的 `parser_backend` 参数描述写得很清楚：

```
Port parser backend: auto (default), builtin, or slang (requires slang on PATH).
```

但 schema 里只有 `"type": "string"`，没有 `enum`。实测：

```
parser_backend="BOGUS"  → ok=true
parser_backend=""       → ok=true
```

两类非法输入都返回成功——工具静默回退到默认后端，调用方却以为自己做了一次
明确的后端选择。如果调用方是为了「必须用 slang 解析 interface」而传
`parser_backend="slang"`，拼错一个字母就会静默退到 builtin，解析结果变差而
无任何提示。

同仓库里 `protocol` 参数早就加了 enum（拼错协议名立即报错，缺陷 115 修过），
这里是不一致。

## 根因

schema 声明的取值集合（描述里的人工列举）没有被机器可执行的约束表达出来。

## 修复

```python
"parser_backend": {"type": "string", "enum": ["auto", "builtin", "slang"], ...}
```

复用 `call_tool` 已有的 enum 校验（错误信息会列出 `Allowed values`）。

## 附带排查

写了个一次性审计：扫描所有 `type: string`、无 `enum`、但描述里用
`or`/`、`/`,` 列举了候选值的参数。结果是 **0 处**——修完这一处后，
所有「人工列举取值」的参数都有了机器可校验的 enum。

## 验证

| `parser_backend` | 修复前 | 修复后 |
|---|---|---|
| `"BOGUS"` | `ok=true`（静默回退） | `Allowed values: ['auto','builtin','slang']` |
| `""` | `ok=true` | 同上 |
| `"slan"`（拼错） | `ok=true` | 同上 |
| `auto` / `builtin` / `slang` | 正常 | 正常 |

回归测试：`test_parser_backend_enum_is_enforced`。
