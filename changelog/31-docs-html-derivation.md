# Changelog — 文档 HTML 版：一个派生器 + 三个写它时踩到的真实坑

需求是「+做 html 版本」。手写两份上千行的 HTML 是错的——它必然与 markdown 漂移，
且改一处要维护两处。这里把 HTML 定位为**派生产物**：markdown 是唯一事实源，
`docs/build_docs_html.py` 负责渲染，`--check` 对 CI 校验同步。

## 缺陷 A — `<strong>` 被转义成可见文本

第一版 `_inline()` 的顺序是「先打标签，最后 `html.escape()` 兜底」：

```python
text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)   # 打标签
...
text = html.escape(text, quote=False)                            # 又把标签转义了
```

后果（浏览器实测截图）：页面正文直接显示

```
FPGA 验证里最常见的失败不是"断言失败"，而是<strong>看起来通过了</strong>：
```

`<strong>` 变成了字面文本。这不是排版瑕疵，是把 markdown 的强调语法整段报废。

修复：**先转义、后打标签**。HTML 转义只影响 `< > &`，不破坏 `**` / `[]()` /
反引号这些 markdown 语法，所以顺序可以反过来：

```python
text = escape(text)                                    # 1) 全文转义
text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)  # 2) 再打标签
```

同时把行内代码用占位符提前抽出（`\x00CODE0\x00`），处理完再回填——否则代码里的
`**` 或 `[x](y)` 会被当成 markdown 语法改写。

## 缺陷 B — 无脑把 `.md` 改成 `.html`，产生死链

链接改写规则原本是「凡是 `.md` 结尾就换成 `.html`」：

```python
if href.endswith(".md"):
    href = href[:-3] + ".html"
```

但仓库里只有 `FEATURES.html` / `EXAMPLES.html` 两份 HTML。于是产物里出现三个死链：

| 链接 | 目标是否存在 |
|---|---|
| `TEST_REPORT_AND_PLAN.html` | ✗ |
| `../README.html` | ✗ |
| `../changelog/INDEX.html` | ✗ |

点开全是 404。修复：**只有目标 `.html` 确实存在时才改写**，否则保留原 `.md`
（GitHub 上仍可正常查看）：

```python
if href.endswith(".md"):
    target_html = href[:-3] + ".html"
    if (HERE / target_html).is_file():
        href = target_html
```

修复后死链 0 个，51 个锚点全部有效。

## 缺陷 C — 页面出现两个 `<h1>`

模板 hero 已经渲染了 `<h1>{title}</h1>`，而 markdown 的第一行 `# 标题` 又被
`convert()` 转成 `<h1>`，同一标题在页面上出现两次（视觉上是重复的大标题）。

修复：正文里的 `level == 1` 直接跳过，只保留模板 hero。

## 缺陷 D — f-string 里的反斜杠：CI 才暴露的 Python 版本差异

前三个缺陷是我自己看截图/查链接发现的。**这个不是**——本地全套测试（regression /
fusion / eda / audit 11/11 / ruff / docs_check）全部通过后推送，CI 立刻红：

```
test (ubuntu-latest, py3.11)   failure
test (ubuntu-24.04, py3.10)    failure
test (ubuntu-latest, py3.13)   success     ← 本地也是这个版本
test (windows-latest, py3.13)  success
lint                           success     ← lint job 是 3.12，同样不报
```

日志：

```
File "docs/build_docs_html.py", line 218
SyntaxError: f-string expression part cannot include a backslash
```

违规代码（blockquote 内列表项）：

```python
items = [
    f"<li>{_inline(re.sub(r'^\s*[-*]\s+', '', x))}</li>"
    for x in p.splitlines() if x.strip()
]
```

**PEP 701**（Python **3.12**）才允许 f-string 的表达式部分包含反斜杠。
本地/CI-lint 是 3.12/3.13，全都正常；只有真正跑 3.10/3.11 的测试 job 才炸。

### 为什么本地没发现：语法门自己也有覆盖漏洞

CI 里那道 `Syntax gate for Python 3.10` 用的是 `ast.parse(..., feature_version=(3,10))`，
**它确实能查出这个问题**——但扫描范围只有：

```python
for root in ("fpga_toolbox", "tests"):   # ← docs/ 不在里面
```

`docs/build_docs_html.py` 从未被扫描。两处叠加：新文件放在没人检查的目录里，
用了一个新版本才允许的语法。

修复两件事：

1. 把正则移出 f-string 表达式：

   ```python
   items = []
   for x in p.splitlines():
       if not x.strip():
           continue
       content = re.sub(r"^\s*[-*]\s+", "", x)   # 在 f-string 之外求值
       items.append(f"<li>{_inline(content)}</li>")
   ```

2. 语法门扫描范围对齐到全仓库（`fpga_toolbox` / `tests` / `docs` / `examples`）：

   ```python
   for root in ("fpga_toolbox", "tests", "docs", "examples"):
   ```

### 本地可复现的回归测试

不能靠 CI 才发现这类问题。新增 `test_all_scripts_parse_under_py310`：

- 全仓库 `ast.parse(feature_version=(3,10))`，并断言扫描到的文件数 > 0
  （防止有人把目录名改错导致"零文件 = 全过"）
- 专项 AST 检查：遍历所有 `JoinedStr` / `FormattedValue`，用
  `ast.get_source_segment` 取出表达式源码，断言其中无 `\`

> 第一版专项检查用正则扫原始文本，结果**误报了自己**——本测试的 docstring 里
> 就有一段"错误写法"作为说明。改用 AST 后只命中真实 f-string 节点。

反向验证：把违规写法重新注入 `docs/build_docs_html.py`，本地立即失败：

```
  FAIL no backslash inside f-string expressions  docs/build_docs_html.py:222
FAILED (1): no backslash inside f-string expressions
```

## 产物设计

- **完全自包含**：无外部字体 / CDN / JS 依赖（`grep 'href="https\?://'` 为空），
  双击即可离线打开——与仓库「离线文档」的定位一致
- **侧栏目录 + 页内过滤**：`#filter` 输入框实时隐藏不匹配的 TOC 项
  （浏览器实测 32→1→32 / 19→1→19，清空后恢复）
- **深色终端风格**，与 `docs/eda-tutorials/*.html` 观感统一
- 代码块带语言标签（shell / json / python / systemverilog / yaml）

## 质量自检（`lint_output`）

生成器不只产出，还对产物做断言。这些都是上面踩过的坑，直接固化成检查：

| 检查 | 对应缺陷 |
|---|---|
| 残留占位符 `\x00` | 行内代码回填失败 |
| 标签不平衡 | 解析器弄坏 markdown |
| **死链** | 缺陷 B |
| **锚点无对应 id** | TOC 指不到标题 |
| **转义标签泄漏** | 缺陷 A |

## 验证

### 生成器自检

```
$ python3 docs/build_docs_html.py --check
  [ok] FEATURES.html: 与 FEATURES.md 同步
  [ok] EXAMPLES.html: 与 EXAMPLES.md 同步
  [ok] FEATURES.html: 结构 / 链接 / 锚点自检通过
  [ok] EXAMPLES.html: 结构 / 链接 / 锚点自检通过
```

漂移检测反向验证过：手动改动 HTML 一个字符后 `--check` 返回 1；把链接改成不存在的
`.html` 后同时报「不同步」与「死链：TEST_REPORT_AND_PLAN.html」。

### 浏览器实测（Playwright / Chromium）

| 项 | FEATURES | EXAMPLES |
|---|---|---|
| `<h1>` 数量 | 1 | 1 |
| TOC 项 / 锚点可解析 | 32 / 7 测全部 ok | 19 / 7 测全部 ok |
| 代码块 | 33 | 45 |
| 表格 | 10 | 5 |
| `<strong>` 实际渲染 | 26 | 32 |
| 可见文本中的转义标签 | 0 | 0 |
| markdown 残留（`**` / `|---`） | 0 | 0 |
| 过滤：全量 → 输入「变异」→ 清空 | 32→1→32 | 19→1→19 |
| 页内跳转后 `scrollY` | 3019（正常滚动） | 同 |

### 回归测试锁死

`tests/test_regression.py` 新增 `test_docs_html_markdown_sync`，16 项断言：
同步、结构自检、无转义标签、单 `<h1>`、三个死链均不存在。生成器一被改坏即失败。

### CI

新增一步（每个 test matrix job 都跑）：

```yaml
- name: Docs HTML in sync with markdown
  run: python docs/build_docs_html.py --check
```

### 完整套件

```
python3 tests/test_regression.py              ALL CHECKS PASSED
python3 tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
python3 tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
python3 -m fpga_toolbox audit_harness         11 / 11
ruff check fpga_toolbox tests                 All checks passed!
python3 examples/axi_apb_ref/axi/docs_check.py  D1–D13 全过
```

### 教训

本地套件全绿 + 推送后 CI 红，说明**本地缺了 CI 的一个维度**：这里缺的是 Python
版本。3.12 的 `ast.parse` 用 `feature_version=(3,10)` 能报出违规，但违规文件不在
扫描范围里，所以两处都要修——只修代码不改扫描范围，下次换个目录还会再犯。

修复后新增的 `test_all_scripts_parse_under_py310` 让这个维度在本地就能覆盖，
不必等 CI。

## 顺带修正

- `docs/FEATURES.md` 原写「67 个真实缺陷」，与 changelog 文件数（31 篇）不对应，
  改为不依赖计数的表述「缺陷修复记录（每篇含现象/根因/修复/验证）」
- README 文档表拆成「功能手册与示例（md + html）」与「EDA 工具资料」两节，
  并写明重新生成命令
- 两篇 markdown 的相关文档区补上各自 HTML 版入口，并标注「由本文件生成，勿直接编辑」
