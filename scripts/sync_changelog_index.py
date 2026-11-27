#!/usr/bin/env python3
"""changelog 索引一致性校验 + 顶层 CHANGELOG.md 发布摘要生成。

背景（真实踩坑）：
  * `changelog/` 曾同时存在 `10-pure-apb-draft.md` 与
    `10-synopsys-vcs-dc-pt-campaign-integration.md` —— 两个分支各自独立编号到
    10，合入后撞号。靠人工发现，CI 毫无感知。
  * `changelog/INDEX.md` 靠手工维护，新增条目忘了登记就静默漂移；`01`–`03`
    还长期是反引号而不是链接（点不开）。
  * 顶层 `CHANGELOG.md` 的「发布摘要」同样手工维护，与 `changelog/` 实际内容
    脱节。

本脚本把这三件事变成可自动校验/生成的事实：

  python3 scripts/sync_changelog_index.py --check   # 校验（CI / 门禁用）
  python3 scripts/sync_changelog_index.py --write   # 重新生成摘要段

校验项：
  1. 编号唯一（转发文件豁免：一级标题以「已迁移」开头）
  2. 每个 changelog/*.md 都在 INDEX.md 里有链接
  3. INDEX.md 里没有指向不存在文件的死链
  4. CHANGELOG.md 的自动摘要段与 changelog/ 实际内容一致
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
CHDIR = ROOT / "changelog"
INDEX = CHDIR / "INDEX.md"
TOPLEVEL = ROOT / "CHANGELOG.md"

#: CHANGELOG.md 里自动生成区段的标记（人工内容在标记之外，不受影响）
BEGIN_MARK = "<!-- BEGIN AUTO-GENERATED CHANGELOG SUMMARY -->"
END_MARK = "<!-- END AUTO-GENERATED CHANGELOG SUMMARY -->"

_NUM_RE = re.compile(r"^(\d+)-")
_LINK_RE = re.compile(r"\]\((\d+-[^)]+\.md)\)")


def _is_forwarding_stub(path: Path) -> bool:
    """转发文件（历史重号迁移后留下的兼容桩）不参与编号唯一性检查。"""
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return False
    return "已迁移" in first


def scan() -> Tuple[List[Path], dict, List[str]]:
    """返回 (全部 changelog 文件, 编号 -> 文件列表, 问题列表)。"""
    files = sorted(p for p in CHDIR.glob("*.md") if p.name != "INDEX.md")
    by_num: dict = {}
    problems: List[str] = []
    for p in files:
        m = _NUM_RE.match(p.name)
        if not m:
            problems.append(f"文件名缺少数字前缀: {p.name}")
            continue
        if _is_forwarding_stub(p):
            continue
        by_num.setdefault(m.group(1), []).append(p)
    for num, ps in sorted(by_num.items()):
        if len(ps) > 1:
            problems.append(
                "编号重复 %s: %s" % (num, ", ".join(x.name for x in ps)))
    return files, by_num, problems


def check_index(files: List[Path]) -> List[str]:
    problems: List[str] = []
    if not INDEX.is_file():
        return ["changelog/INDEX.md 不存在"]
    idx = INDEX.read_text(encoding="utf-8")
    linked = set(_LINK_RE.findall(idx))
    names = {p.name for p in files}
    for missing in sorted(names - linked):
        problems.append(f"未登记进 INDEX.md: {missing}")
    for dead in sorted(linked - names):
        problems.append(f"INDEX.md 死链: {dead}")
    return problems


def _title_of(path: Path) -> str:
    """取一级标题作为摘要条目文案（去掉前导编号与分隔符）。"""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                t = line[2:].strip()
                t = re.sub(r"^\d+\s*[·:：—-]\s*", "", t)
                return t
    except OSError:
        pass
    return path.stem


def build_summary(by_num: dict, limit: int = 12) -> str:
    """生成最近 N 条变更的摘要段（按编号倒序）。"""
    items = []
    for num in sorted(by_num, key=lambda x: int(x), reverse=True)[:limit]:
        for p in by_num[num]:
            items.append(f"- **{num}** · [{_title_of(p)}](changelog/{p.name})")
    return (
        f"{BEGIN_MARK}\n"
        f"<!-- 本段由 scripts/sync_changelog_index.py 自动生成，请勿手工编辑。\n"
        f"     完整清单见 changelog/INDEX.md；运行 `python3 scripts/sync_changelog_index.py --write` 重新生成。 -->\n"
        f"\n### 最近 {limit} 条变更\n\n"
        + "\n".join(items)
        + f"\n\n{END_MARK}"
    )


def check_toplevel(by_num: dict) -> List[str]:
    if not TOPLEVEL.is_file():
        return ["CHANGELOG.md 不存在"]
    txt = TOPLEVEL.read_text(encoding="utf-8")
    if BEGIN_MARK not in txt or END_MARK not in txt:
        return ["CHANGELOG.md 缺少自动摘要标记段（见 scripts/sync_changelog_index.py）"]
    start = txt.index(BEGIN_MARK)
    end = txt.index(END_MARK) + len(END_MARK)
    current = txt[start:end]
    expected = build_summary(by_num)
    if current.strip() != expected.strip():
        return ["CHANGELOG.md 自动摘要段已过期（运行 --write 重新生成）"]
    return []


def write_toplevel(by_num: dict) -> bool:
    txt = TOPLEVEL.read_text(encoding="utf-8")
    expected = build_summary(by_num)
    if BEGIN_MARK in txt and END_MARK in txt:
        start = txt.index(BEGIN_MARK)
        end = txt.index(END_MARK) + len(END_MARK)
        new = txt[:start] + expected + txt[end:]
    else:
        new = txt.rstrip("\n") + "\n\n" + expected + "\n"
    if new == txt:
        return False
    TOPLEVEL.write_text(new, encoding="utf-8")
    return True


def main(argv: List[str]) -> int:
    write = "--write" in argv
    files, by_num, problems = scan()
    problems += check_index(files)
    if write:
        changed = write_toplevel(by_num)
        print(f"{'已更新' if changed else '无需更新'} CHANGELOG.md 自动摘要段")
        # 写完再校验一次摘要是否一致
        problems = scan()[2] + check_index(files) + check_toplevel(by_num)
    else:
        problems += check_toplevel(by_num)

    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
        return 1
    print(f"  [ok] changelog: {len(files)} 个文件、编号唯一、INDEX 完整、CHANGELOG 摘要同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
