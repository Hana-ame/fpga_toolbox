# Changelog — `call_tool` 不校验 required，产物被静默写进 cwd

前六轮都在审 `eda_campaign` 与单个工具。本轮做**跨工具一致性扫描**：把 13 个工具的
`inputSchema` 与函数签名逐项比对。参数名、类型、默认值全部一致，但 `required`
这一项撞出了两个串联的缺陷。

## 缺陷 A — `out_dir` 为空时产物写进当前工作目录

`emit_testbench` 的两条路径都直接 `Path(out_dir)` 后 `mkdir`：

```python
od = Path(out_dir)          # out_dir="" -> Path(".")  -> cwd
od.mkdir(parents=True, exist_ok=True)
```

实测在空目录 `/tmp/dircwd` 里调用 `emit_testbench(spec_path=...)`（不传 out_dir）：

```
ok=True
当前目录新增文件: ['dev_axil_bind.sv', 'dev_axil_sva_checker.sv',
                   'dev_axil_wrap.sv', 'test_dev_axil.py']
```

**4 个产物凭空出现在调用方的工作目录里，工具还报成功。** 多协议路径
（`--protocol apb` 等）同样如此。

修复：两条路径都在 `Path(out_dir)` 之前显式拒绝空值，给出可读错误。

## 缺陷 B（根因）— `call_tool` 从不校验 `required`

顺着缺陷 A 追下去发现：`emit_testbench` 的 schema 明明写着
`"required": ["out_dir"]`，但 `call_tool` 只校验**未知参数名**：

```python
unknown = [k for k in params if k not in schema_props]
if unknown: return err_result(...)
try: return fn(**params)      # required 从未被检查
```

于是 schema 里的 `required` 对 MCP/`call_tool` 调用方**完全无效**——必填约束
只写在文档里。这也是为什么缺陷 A 能发生：函数签名给了 `out_dir=""` 默认值兜底，
而入口又没人执行 required。

修复：`call_tool` 增加 required 校验，缺失即返回
`Missing required parameter(s) [...] for tool '...'`，与「未知参数名」错误区分开。

扫描确认只有 `emit_testbench.out_dir` 属于「schema 必填 + 签名有默认值」这一
可绕过组合；其余 12 个工具的必填项在签名里无默认值，Python 本身会拦截。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 契约路径缺 `out_dir` | `ok=True`，4 个文件写进 cwd | `ok=False`，**cwd 无文件** |
| 多协议路径缺 `out_dir` | 同上 | `ok=False`，**cwd 无文件** |
| `index_repo` 缺 `project_root` | 报错信息不统一 | `Missing required parameter(s)` |
| `audit_harness`（required=[]） | ok | ok（不受影响）|

新增 `test_call_tool_enforces_required`（6 项断言），regression 总检查项 99 项全绿。
audit_harness 10/10、fusion、docs_check D1–D13、ruff、py3.10 全绿。
