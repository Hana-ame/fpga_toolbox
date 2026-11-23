# Changelog — `call_tool` 不校验 enum 与 type

上一轮给 `call_tool` 补了 `required` 校验。顺着「schema 声明了但没人执行」这条线继续查，
发现 `enum` 与 `type` 两类约束同样是**只写在 schema 里、入口从不执行**。

## 缺陷 A — 非法 enum 值报错指向错误的方向

`emit_testbench.protocol` 的 schema 有明确枚举：

```python
"enum": ["auto", "apb", "axi4", "ahb_lite", "ddr3", "ddr4", "hbm", "pcie"]
```

传 `protocol="BOGUS"` 时，错误来自函数内部：

```
Protocol 'BOGUS' not found in any module of apb_slave.sv
(modules: {'apb_slave': ['apb']})
```

而传一个**合法但 RTL 不支持**的值（`protocol="ddr3"`）报错文本**一模一样**：

```
Protocol 'ddr3' not found in any module of apb_slave.sv
```

调用方无法区分「我把协议名拼错了」和「这个 RTL 确实没有该协议」——前者改参数、
后者换文件，处置方式完全不同。

修复：`call_tool` 执行 enum 校验，非法值报
`Invalid value 'BOGUS' for parameter 'protocol' ... Allowed values: [...]`。

## 缺陷 B — 类型错误漏出 Python 内部实现细节

给 `string` 参数传 int：

```
index_repo(project_root=123)
-> argument should be a str or an os.PathLike object where __fspath__
   returns a str, not 'int'
```

这是 CPython 的 `os.fspath` 错误原文。用户既看不出**是哪个参数**错了，也看不出
**期望是什么类型**。

修复：`call_tool` 按 schema type 校验，报
`Invalid type for parameter 'project_root' of tool 'index_repo': expected string, got int.`

## 一个必须保留的兼容性（我的加严一度破坏了它）

第一版类型校验一律拒绝「integer 参数收到 str」，结果打挂了既有设计。

`sim_runner.py` 有明确注释说明这是**故意的**：

```python
# MCP / CLI 调用方常把数字参数序列化成字符串（JSON number → str），
# schema 虽声明 integer，但 call_tool 不做类型强制。若直接把 str 传给
# subprocess.run(timeout=...)，其内部 `timeout - (now - start)` 会报
# `unsupported operand type(s) for +: 'float' and 'str'`（实测踩过）。
```

`sim_runner` / `mutation_runner` 都在函数边界做了 `int(timeout_sec)` 宽容转换。
入口若一律拒绝会把这个已存在的兼容逻辑废掉——实测 `run_sim(timeout_sec="1700")`
由「可用」变成「Invalid type」。

修复：**数字类参数放行字符串**（下游自己解析并给出可读错误），非数字类型严格。
现在的行为：

| 调用 | 结果 |
|---|---|
| `run_sim(timeout_sec="1700")` | 放行 → 下游 `int()` → 走到脚本检查 |
| `run_sim(timeout_sec="abc")` | 放行 → 下游报 `timeout_sec must be an integer` |
| `index_repo(project_root=123)` | 入口拒绝：`expected string, got int` |
| `copy_sandbox(clean="false")` | 入口拒绝：`expected boolean, got str` |

## 验证

新增 `test_call_tool_enum_and_type_validation`（7 项断言，含「enum 错误与 RTL
错误可区分」「数字字符串仍被接受」两条关键约束）。regression 全部通过；
audit_harness 10/10、fusion、eda_campaign、docs_check D1–D13、ruff、py3.10 全绿。
