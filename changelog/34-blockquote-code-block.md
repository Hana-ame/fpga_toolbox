# Changelog — blockquote 内的代码块整段丢失并泄漏占位符

按"首次/边界/异构环境"排查时，给 `docs/FEATURES.md` 补写"怎么跑路径 A 的
cocotb TB"，把一段 ```makefile 围栏代码写进了 `>` 引用块里。重新生成 HTML
后，生成器自己的 `lint_output` 立刻报：

```
[FAIL] FEATURES.html: 残留占位符（\x00）
```

## 现象

那段代码在 HTML 里变成：

```html
<p><code>\x00CODE0\x00</code></p>
```

`TOPLEVEL_LANG = verilog` 等**整段代码内容消失**，只留一个未回填的占位符。

## 根因

`convert()` 的 blockquote 分支把引用块内部当成"普通段落"，只做逐行
`<br>` / 列表处理，**完全不认 ``` 围栏**：

```python
if re.match(r"^>\s?", line):
    ...
    for p in paras:
        if all(列表行 or 空):        # 代码行不匹配 → 走 else
            ...
        else:
            html_parts.append("<p>" + _inline(p.replace("\n", " ")) + "</p>")
```

于是围栏行被并进同一段落文本，`_inline()` 的行内代码占位符机制把整块
包进一个 `\x00CODE0\x00`，而回填表里并没有这个占位符的正文 → 永远泄漏。

## 修复

把围栏解析抽成 `_render_fence(lines, start)`，顶层与 blockquote 都复用；
blockquote 改为**递归调用 `convert()`** 处理内部结构（它本身已能处理
列表 / 粗体 / 代码 / 表格 / 嵌套引用，剥完 `>` 后若仍是引用会继续剥，
天然收敛）。

```python
if re.match(r"^>\s?", line):
    buf = []
    while i < n and re.match(r"^>\s?", lines[i]):
        buf.append(re.sub(r"^>\s?", "", lines[i]))
        i += 1
    inner_body, _ = convert("\n".join(buf).strip("\n"))
    out.append('<blockquote>' + inner_body + '</blockquote>')
```

## 验证

反向验证：把 blockquote 改回旧的"平铺成一段"实现 →
`blockquote preserves fenced code block` 立即 FAIL（代码变成一行平铺文本），
且 `in sync` 也 FAIL（渲染结果与已提交 HTML 不一致）。4 项断言同时报警。

## 附带产出

排查这个缺陷时顺带确认了**契约路径 cocotb TB 确实能跑通**（Verilator
5.054 + cocotb 2.1.0，`TESTS=1 PASS=1`，SVA 心跳正常）。过程中踩到
cocotb 2.x 的 4 个反直觉点，已写进 `docs/FEATURES.md` 路径 A 说明：

- `TOPLEVEL_LANG = verilog`（不是 `verilator`，否则静默跳过仿真）
- 默认 simulator 是 icarus，必须显式 `SIM=verilator`
- `cocotb-config` 在 cocotb 2.x 属于 `cocotb_tools`
- 需 `VERILATOR_BIN_DIR` 指向 Verilator 5.036+（系统 5.020 会被 `$(error)` 拦）
