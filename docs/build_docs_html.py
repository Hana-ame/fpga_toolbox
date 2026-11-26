#!/usr/bin/env python3
"""build_docs_html.py —— 把 docs/FEATURES.md、docs/EXAMPLES.md 转成自包含 HTML

为什么要有生成器而不是手写 HTML：
  手写 HTML 与 markdown 必然漂移。这里的规则是**markdown 是唯一事实源**，
  HTML 由它派生；`--check` 模式对 CI 校验「重新生成后是否与已提交文件一致」，
  一旦有人只改了一边就会失败。

产物特性：
  * 完全自包含（无外部字体 / CDN / JS 依赖），可直接双击离线打开
  * 内嵌目录（TOC）+ 页内搜索 + 锚点
  * 深色终端风格，与 docs/eda-tutorials/*.html 的观感一致
  * 输出可复现：不写时间戳，除非显式设置 SOURCE_DATE_EPOCH

用法：
    python3 docs/build_docs_html.py            # 生成
    python3 docs/build_docs_html.py --check    # 仅校验是否同步（CI 用）
"""
from __future__ import annotations

import html
import re
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 页面元信息：标题 / 副标题 / 输出文件名
DOCS = {
    "FEATURES.md": {
        "out": "FEATURES.html",
        "title": "fpga_toolbox 功能手册",
        "deck": "13 个原子工具、统一信封、37 个诊断码 —— 面向 AI Agent 与人类工程师的 FPGA/ASIC 验证工具箱",
        "kicker": "功能手册",
    },
    "EXAMPLES.md": {
        "out": "EXAMPLES.html",
        "title": "fpga_toolbox 示例集",
        "deck": "10 个可直接运行的端到端示例，所有输出均为实际运行结果",
        "kicker": "示例集",
    },
}


# --------------------------------------------------------------------------
# 行内标记
# --------------------------------------------------------------------------
def _inline(text: str) -> str:
    """把一行 markdown 的行内标记转成 HTML。

    顺序很重要，踩过坑：
      1. 先占位保存行内代码 —— 代码内容不得被后续任何规则改写（`**` 出现在
         代码里曾被误判成粗体）；
      2. **先做 HTML 转义，再生成标签** —— 反过来会把刚生成的 `<strong>` 又
         转义成可见文本（真实 bug：页面里直接显示 `<strong>看起来通过了</strong>`）；
      3. 最后回填代码占位符。

    HTML 转义只影响 `< > &`，不会破坏 `**` / `[]()` / 反引号这些 markdown 语法，
    所以"先转义后解析"对 markdown 是安全的。
    """
    codes: list[str] = []

    def stash_code(m: re.Match[str]) -> str:
        codes.append(f"<code>{html.escape(m.group(1), quote=False)}</code>")
        return f"\x00CODE{len(codes) - 1}\x00"

    # 1) 行内代码（支持 `` 包裹含反引号的情形）
    text = re.sub(r"``([^`]+)``", stash_code, text)
    text = re.sub(r"`([^`]+)`", stash_code, text)

    # 2) 全文转义（此时还没有任何由我们生成的标签）
    text = html.escape(text, quote=False)

    # 3) 链接：只有当目标 .html 确实存在时才改写，否则保留原 .md。
    #    盲目把 .md 全换成 .html 会产生死链（README.html / changelog/INDEX.html
    #    并不存在），这些文档仍需以 markdown 形式打开。
    def link(m: re.Match[str]) -> str:
        label, href = m.group(1), m.group(2)
        if href.endswith(".md") and not href.startswith(("http://", "https://")):
            target_html = href[:-3] + ".html"
            if (HERE / target_html).is_file():
                href = target_html
        return f'<a href="{href}">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)

    # 4) 粗体 / 斜体
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)

    # 5) 回填代码
    for i, c in enumerate(codes):
        text = text.replace(f"\x00CODE{i}\x00", c)
    return text


# --------------------------------------------------------------------------
# 块级解析
# --------------------------------------------------------------------------
def _slug(text: str, used: dict[str, int]) -> str:
    """由标题生成稳定锚点 id（中文保留，去掉标点）。"""
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", s).strip().lower()
    s = re.sub(r"\s+", "-", s) or "section"
    if s in used:
        used[s] += 1
        return f"{s}-{used[s]}"
    used[s] = 0
    return s


LANG_LABEL = {
    "bash": "shell",
    "json": "json",
    "python": "python",
    "verilog": "systemverilog",
    "yaml": "yaml",
    "": "text",
}


def _render_fence(lines: list[str], start: int) -> tuple[str, int]:
    """从 start 行（应为 ```lang 围栏）渲染代码块，返回 (html, 下一行索引)。

    抽成独立函数是为了让 blockquote 内部也能复用：原先 blockquote 分支把
    围栏当普通文本，导致代码内容整段丢失并泄漏 \\x00 占位符（实测踩坑 ——
    文档里"引用块内嵌代码块"一出现，FEATURES.html 自检就报残留占位符）。
    """
    # 围栏可能带缩进（列表项内的代码块），用非捕获前导 \s* 匹配，同时把这
    # 个缩进从代码内容里剥掉，避免生成物里出现「代码块左侧悬空缩进」。
    m = re.match(r"^(\s*)```(\w*)\s*$", lines[start])
    lead = m.group(1) if m else ""
    langm = m.group(2) if m else ""
    lang = langm
    i = start + 1
    buf: list[str] = []
    close_re = re.compile(r"^\s*```\s*$")
    while i < len(lines) and not close_re.match(lines[i]):
        row = lines[i]
        # 剥掉与围栏一致的缩进前缀（缩进不足时按实际缩进剥）
        if lead and row.startswith(lead):
            row = row[len(lead):]
        buf.append(row)
        i += 1
    i += 1  # 跳过结束围栏
    label = LANG_LABEL.get(lang, lang or "text")
    code = "\n".join(buf)
    return (
        f'<div class="code-block"><div class="code-lang">{html.escape(label)}</div>'
        f'<pre><code>{html.escape(code, quote=False)}</code></pre></div>'
    ), i


def convert(md: str) -> tuple[str, list[tuple[int, str, str]]]:
    """返回 (body_html, toc)。toc 元素为 (层级, 锚点, 标题文本)。"""
    lines = md.splitlines()
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    used_slugs: dict[str, int] = {}

    i = 0
    n = len(lines)
    # 列表 / 引用 / 表格的连续状态
    while i < n:
        line = lines[i]

        # ---- 围栏代码块（允许缩进：列表项内的围栏）----
        m = re.match(r"^\s*```(\w*)\s*$", line)
        if m:
            block, i = _render_fence(lines, i)
            out.append(block)
            continue

        # ---- 水平线 ----
        if re.match(r"^---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # ---- 标题 ----
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)
        if hm:
            level = len(hm.group(1))
            raw = hm.group(2).strip()
            inner = _inline(raw)
            sid = _slug(raw, used_slugs)
            if level == 1:
                # 文档主标题已由模板 hero 渲染，正文里跳过，避免同一标题出现两次
                i += 1
                continue
            else:
                out.append(f'<h{level} id="{sid}">{inner}</h{level}>')
                toc.append((level, sid, re.sub(r"[`*]", "", raw)))
            i += 1
            continue

        # ---- 表格 ----
        if line.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in header)
            body_rows = []
            for r in rows:
                tds = "".join(f"<td>{_inline(c)}</td>" for c in r)
                body_rows.append(f"<tr>{tds}</tr>")
            out.append(
                '<div class="table-wrap"><table><thead><tr>'
                + th
                + "</tr></thead><tbody>"
                + "".join(body_rows)
                + "</tbody></table></div>"
            )
            continue

        # ---- 引用块（可能多行）----
        if re.match(r"^>\s?", line):
            # 先抽出整段引用（剥掉行首 "> "），再递归走 convert 处理内部结构 ——
            # 这样 blockquote 内可以安全嵌套围栏代码块、列表、嵌套引用，
            # 而不会把 ``` 围栏当普通文本（旧实现导致代码丢失 + 占位符泄漏）。
            buf = []
            while i < n and re.match(r"^>\s?", lines[i]):
                buf.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            inner_md = "\n".join(buf).strip("\n")
            inner_body, _ = convert(inner_md)
            out.append('<blockquote>' + inner_body + '</blockquote>')
            continue

        # ---- 列表（含嵌套一层，且支持项内嵌围栏代码块）----
        if re.match(r"^\s*([-*]|\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.", line))
            # 列表项既可以是一行文字，也可以带**缩进的延续内容**：段落、以及
            # 围栏代码块。旧实现只把匹配 `^\s*[-*]` 的行当列表项，续行与围栏
            # 一律落到外层，于是
            #     1. **标题**：
            #        ```python
            #        code
            #        ```
            # 里的围栏被当成普通文本送进 _inline，行内代码正则吞掉反引号后
            # 留下 \x00CODEn\x00 占位符 —— 生成物里出现 NUL 字节，docs 自检
            # 报「残留占位符」（缺陷 178）。
            items: list[tuple[int, str, list[str]]] = []
            base_indent = 0
            while i < n:
                m_item = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if m_item:
                    if not items:
                        base_indent = len(m_item.group(1))
                    indent = len(m_item.group(1))
                    items.append((indent, m_item.group(3), []))
                    i += 1
                    continue
                # 非列表行：只有在它属于当前列表项（缩进 > 基准，或为空行后
                # 紧跟缩进内容）时才作为续行收集。
                if not items:
                    break
                nxt = lines[i]
                if not nxt.strip():
                    # 空行：只有后面还跟着缩进内容才继续收集，否则列表结束
                    j = i + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and len(lines[j]) - len(lines[j].lstrip()) > base_indent:
                        items[-1][2].append("")
                        i += 1
                        continue
                    break
                indent = len(nxt) - len(nxt.lstrip())
                if indent <= base_indent:
                    break
                items[-1][2].append(nxt)
                i += 1
            tag = "ol" if ordered else "ul"
            parts = [f"<{tag}>"]
            depth = 0
            for indent, content, cont in items:
                if indent > base_indent and depth == 0:
                    parts.append("<ul>")
                    depth = 1
                elif indent <= base_indent and depth == 1:
                    parts.append("</ul>")
                    depth = 0
                body = _inline(content)
                if cont:
                    # 续行按块级规则渲染（围栏代码块、段落、嵌套列表）
                    inner_html, _ = convert("\n".join(cont).strip("\n"))
                    body += inner_html
                parts.append(f"<li>{body}</li>")
            if depth == 1:
                parts.append("</ul>")
            parts.append(f"</{tag}>")
            out.append("".join(parts))
            continue

        # ---- 空行 ----
        if not line.strip():
            i += 1
            continue

        # ---- 段落（连续非空行合并）----
        buf = []
        while i < n and lines[i].strip() and not re.match(
            r"^(```|#{1,6}\s|>|\||---+\s*$|\s*[-*]\s|\s*\d+\.\s)", lines[i]
        ):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>" + _inline(" ".join(buf)) + "</p>")

    return "\n".join(out), toc


# --------------------------------------------------------------------------
# 页面模板
# --------------------------------------------------------------------------
TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
:root {{
  --paper:#0E1117; --band:#151B26; --band2:#1B2431;
  --ink:#F8FAFC; --ink2:#CBD5E1; --mute:#94A3B8; --hair:#263042;
  --accent:#38BDF8; --accent2:#34D399; --warn:#FBBF24; --err:#FDA4AF;
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  font-size:15px; line-height:1.7; -webkit-font-smoothing:antialiased;
}}
code, pre {{
  font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
.layout {{ display:flex; align-items:flex-start; }}

/* 侧栏 TOC */
aside {{
  position:sticky; top:0; height:100vh; width:290px; flex:0 0 290px;
  overflow-y:auto; padding:26px 18px 40px;
  border-right:1px solid var(--hair); background:var(--band);
}}
aside .brand {{
  font-size:12px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--accent); font-weight:700; margin-bottom:6px;
}}
aside .brand-sub {{ font-size:12px; color:var(--mute); margin-bottom:20px; }}
#filter {{
  width:100%; padding:8px 10px; margin-bottom:16px;
  background:var(--paper); color:var(--ink);
  border:1px solid var(--hair); border-radius:0; font-size:13px;
}}
#filter:focus {{ outline:1px solid var(--accent); }}
aside nav a {{
  display:block; color:var(--ink2); text-decoration:none;
  font-size:13px; padding:3px 8px; border-left:2px solid transparent;
}}
aside nav a:hover {{ color:var(--accent); background:var(--band2); }}
aside nav a.lv2 {{ font-weight:600; color:var(--ink); margin-top:9px; }}
aside nav a.lv3 {{ padding-left:20px; font-size:12.5px; color:var(--mute); }}
aside nav a.lv4 {{ padding-left:32px; font-size:12px; color:var(--mute); }}

main {{ flex:1; min-width:0; padding:52px clamp(20px,4vw,64px) 100px; max-width:1080px; }}
.kicker {{
  font-size:12px; letter-spacing:.16em; text-transform:uppercase;
  color:var(--accent2); font-weight:700; margin-bottom:12px;
}}
h1 {{
  font-size:clamp(30px,3.4vw,42px); line-height:1.16; letter-spacing:-.02em;
  margin:0 0 14px; text-wrap:balance;
}}
.deck {{ font-size:17px; color:var(--ink2); max-width:74ch; margin:0 0 30px; }}
h2 {{
  font-size:26px; margin:56px 0 16px; padding-top:26px;
  border-top:1px solid var(--hair); letter-spacing:-.01em;
}}
h3 {{ font-size:19px; margin:32px 0 10px; color:var(--accent); }}
h4 {{ font-size:16px; margin:24px 0 8px; color:var(--accent2); }}
p, li {{ max-width:82ch; }}
ul, ol {{ padding-left:24px; }}
li {{ margin:4px 0; }}
a {{ color:var(--accent); }}
hr {{ border:0; border-top:1px solid var(--hair); margin:38px 0; }}

code {{
  background:var(--band2); color:#7DD3FC;
  padding:1.5px 5px; font-size:.9em; border-radius:0;
}}
.code-block {{ margin:16px 0; border:1px solid var(--hair); background:var(--band); }}
.code-lang {{
  font-size:11px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--mute); padding:6px 12px; border-bottom:1px solid var(--hair);
  background:var(--band2);
}}
.code-block pre {{ margin:0; padding:14px 16px; overflow-x:auto; }}
.code-block pre code {{ background:none; color:var(--ink2); padding:0; font-size:13px; }}

.table-wrap {{ overflow-x:auto; margin:18px 0; border:1px solid var(--hair); }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th {{
  background:var(--band2); text-align:left; padding:9px 12px;
  color:var(--accent); font-weight:600; border-bottom:1px solid var(--hair);
  white-space:nowrap;
}}
td {{ padding:8px 12px; border-bottom:1px solid var(--hair); vertical-align:top; }}
tbody tr:last-child td {{ border-bottom:0; }}
tbody tr:hover {{ background:var(--band); }}

blockquote {{
  margin:18px 0; padding:2px 0 2px 16px;
  border-left:3px solid var(--warn); color:var(--ink2);
}}
blockquote p {{ margin:8px 0; }}
blockquote ul {{ margin:8px 0; }}

.hidden {{ display:none !important; }}
@media (max-width:900px) {{
  aside {{ display:none; }}
  main {{ padding:32px 18px 70px; }}
}}
</style>
</head>
<body>
<div class="layout">
<aside>
  <div class="brand">{kicker}</div>
  <div class="brand-sub">fpga_toolbox v0.5.0</div>
  <input id="filter" type="search" placeholder="过滤目录…" autocomplete="off">
  <nav id="toc">
{toc_html}
  </nav>
</aside>
<main>
<div class="kicker">{kicker}</div>
<h1>{title}</h1>
<p class="deck">{deck}</p>
{body}
</main>
</div>
<script>
// 目录过滤：按标题文本隐藏不匹配项（纯前端，无外部依赖）
(function () {{
  var input = document.getElementById('filter');
  var links = Array.prototype.slice.call(document.querySelectorAll('#toc a'));
  input.addEventListener('input', function () {{
    var q = input.value.trim().toLowerCase();
    links.forEach(function (a) {{
      a.classList.toggle('hidden', q !== '' && a.textContent.toLowerCase().indexOf(q) === -1);
    }});
  }});
}})();
</script>
</body>
</html>
"""


def build(src_name: str, meta: dict[str, str]) -> str:
    src = HERE / src_name
    md = src.read_text(encoding="utf-8")
    body, toc = convert(md)
    toc_lines = []
    for level, sid, text in toc:
        cls = f"lv{level}"
        toc_lines.append(
            f'    <a class="{cls}" href="#{sid}">{html.escape(text)}</a>'
        )
    return TEMPLATE.format(
        title=html.escape(meta["title"]),
        kicker=html.escape(meta["kicker"]),
        deck=html.escape(meta["deck"]),
        toc_html="\n".join(toc_lines),
        body=body,
    )


def lint_output(path: Path) -> list[str]:
    """对产物做质量自检，返回问题列表（空 = 通过）。

    这些都是真实踩过的坑，不是形式检查：
      * 残留占位符 —— 行内代码回填失败
      * 标签不平衡 —— 解析器把 markdown 弄坏了
      * 死链 —— 把 .md 无脑改成 .html，产生不存在的目标
      * 锚点缺失 —— TOC 指不到标题
      * 转义标签 —— `<strong>` 被当成文本显示在页面上
    """
    txt = path.read_text(encoding="utf-8")
    problems: list[str] = []

    if "\x00" in txt:
        problems.append("残留占位符（\\x00）")

    for tag in ("table", "div", "pre", "section", "blockquote", "main", "aside"):
        o = len(re.findall(rf"<{tag}[ >]", txt))
        c = len(re.findall(rf"</{tag}>", txt))
        if o != c:
            problems.append(f"<{tag}> 不平衡 {o}/{c}")

    # 转义标签泄漏到正文
    for bad in ("&lt;strong&gt;", "&lt;code&gt;", "&lt;em&gt;", "&lt;a "):
        if bad in txt:
            problems.append(f"转义标签泄漏：{bad}")

    # 死链：只查相对链接
    for href in re.findall(r'href="([^"]+)"', txt):
        if href.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = (path.parent / urllib.parse.unquote(href)).resolve()
        if not target.exists():
            problems.append(f"死链：{href}")

    # 锚点：每个 #x 必须有 id="x"
    ids = set(re.findall(r'id="([^"]+)"', txt))
    for anchor in set(re.findall(r'href="#([^"]+)"', txt)):
        if anchor not in ids:
            problems.append(f"锚点无对应标题：#{anchor}")

    return problems


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    rc = 0
    for src_name, meta in DOCS.items():
        out_path = HERE / meta["out"]
        rendered = build(src_name, meta)
        if check_only:
            if not out_path.exists():
                print(f"  [FAIL] {meta['out']}: 不存在（运行 python3 docs/build_docs_html.py 生成）")
                rc = 1
                continue
            current = out_path.read_text(encoding="utf-8")
            if current != rendered:
                print(f"  [FAIL] {meta['out']}: 与 {src_name} 不同步（重新生成）")
                rc = 1
            else:
                print(f"  [ok] {meta['out']}: 与 {src_name} 同步")
        else:
            out_path.write_text(rendered, encoding="utf-8")
            kb = len(rendered.encode("utf-8")) / 1024
            print(f"  [ok] {meta['out']}: 已生成 ({kb:.1f} KB)")

    # 质量自检对两种模式都跑：--check 时检查的是已提交产物
    for meta in DOCS.values():
        out_path = HERE / meta["out"]
        if not out_path.exists():
            continue
        problems = lint_output(out_path)
        if problems:
            for p in problems:
                print(f"  [FAIL] {meta['out']}: {p}")
            rc = 1
        else:
            print(f"  [ok] {meta['out']}: 结构 / 链接 / 锚点自检通过")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
