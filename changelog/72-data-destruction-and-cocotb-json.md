# Changelog — 三处会毁用户数据 / 必崩的缺陷

三个问题都会让工具**返回 `ok=True` 的同时毁掉用户的东西**，优先级最高。

## 1. 生成的 cocotb 测试用 `repr()` 当 JSON —— 有寄存器就必崩

上一轮为修「契约描述含三引号导致生成的 Python 源码 SyntaxError」，我把

```python
regs = json.dumps([asdict(r) for r in tp.register_rules])   # 合法 JSON
```

改成了

```python
regs = repr([asdict(r) for r in tp.register_rules])         # Python repr
```

但生成出来的代码是 `REGS = json.loads({regs!r})`，于是实际渲染成：

```python
REGS = json.loads("[{'rule_id': 'R1', 'name': 'CTRL', ...}]")
```

单引号不是合法 JSON：

```
JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 3
```

**只要 spec 里有任何一个寄存器或非法地址规则，生成的 cocotb 测试一导入就崩**，
而 `emit_testbench` 报 `ok=True`。这是我上一轮引入的回归 —— 修 A 引入 B。

正确做法是两个约束都要满足：

```python
regs = repr(json.dumps(...))    # json.dumps 保证 JSON 合法性；repr 保证安全嵌入
REGS = json.loads({regs})       # 模板里不再套 {regs!r}
```

验证：三种描述形态（含 `"""`、含结尾反斜杠、普通）生成的源码都
① 是合法 Python（`ast.parse` 通过）、② `exec` 那行后 `json.loads` 成功、
③ 内容精确保留。

## 2. `emit_testbench` 会永久删除用户的 `inc/` 目录

```python
_inc_dst = od / "inc"
if _inc_dst.exists():
    shutil.rmtree(_inc_dst)        # 无条件删除
for _cand in (...):
    shutil.copytree(_cand, _inc_dst)
```

当 `out_dir` 恰好是 RTL 所在目录时，`od / "inc"` **就是用户自己的源码
`inc/` 目录**。实测 `--out-dir <rtl目录> --rtl-file <rtl>`：

```
ok=True
inc/ 目录：GONE        <- 用户的 inc/defs.svh 被永久删除
```

修复：先判断「目标与任一候选源是同一路径」，是则**完全跳过**（本来就在原地，
既不需要复制，更不能先删）；否则才 rmtree + copytree。

验证：`out_dir == rtl 目录` 时 `inc/defs.svh` 内容原样保留；`out_dir` 为独立
目录时仍正常把 `inc/` 复制过去（不误伤原功能）。

## 3. `emit_protocol_testbench` 会静默覆盖用户的 RTL 源文件

产物名是固定的 `tb_<mod>_<proto>.sv` / `run_<mod>_<proto>.sh`。如果用户把上次
生成的 TB 改名后当作 RTL 传回来、且 `out_dir` 是同一目录，`write_text` 直接覆盖：

```
ok=True
用户 RTL 内容 -> 被生成的 TB 覆盖（内容消失）
```

修复：写任何产物之前，核对「产物路径 == 输入 RTL 路径」，命中则拒绝并说明
（"generated artifact 'tb_...' would overwrite the input RTL file"）。

验证：同名场景现在 `ok=False` 且 RTL 字节完全不变；非冲突场景 7 个协议全部
正常生成。

## 验证

- 4 个测试套件全过（新增
  `test_no_user_data_destruction_and_valid_cocotb_json`，三条断言分别覆盖上述
  三处：JSON 合法性 + `ast.parse`、`inc/` 存活 + 独立目录仍复制、同名拒绝 + RTL
  字节不变）
- ruff 0 错 / `verify_invariants --full` **6 层全过** / docs HTML 同步
- 7 个协议参考 DUT 全部仍能正常 emit
