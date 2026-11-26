"""Tools 1 & 2: Syntax-aware RTL repo indexing and BFS transitive dependency resolution."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.sv_parser import SVModuleAST, parse_sv_file, slang_instantiation_targets, sv_tokenize
from .core.types import err_result, ok_result

RTL_EXTS = {".v", ".sv", ".vh", ".svh"}
HDR_EXTS = {".vh", ".svh"}
_INC_RE = re.compile(r'^\s*`include\s+"([^"]+)"', re.MULTILINE)
_IFACE_DEF_RE = re.compile(r"\binterface\s+(?:automatic\s+|static\s+)?([A-Za-z_]\w*)")

_NON_INST = {
    "module", "endmodule", "if", "else", "for", "while", "case", "begin", "end",
    "always", "always_ff", "always_comb", "assign", "function", "task", "generate",
    "assert", "property", "input", "output", "inout", "wire", "reg", "logic",
}


def _find_instantiations(src: str, known: Set[str], self_name: str) -> List[str]:
    toks, found, i = sv_tokenize(src), [], 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.kind == "IDENT" and t.value in known and t.value != self_name and t.value not in _NON_INST:
            j = i + 1
            if j < n and toks[j].value == "#":
                j += 1
                if j < n and toks[j].value == "(":
                    depth = 0
                    while j < n:
                        depth += 1 if toks[j].value == "(" else (-1 if toks[j].value == ")" else 0)
                        j += 1
                        if depth == 0:
                            break
            if j + 1 < n and toks[j].kind == "IDENT" and toks[j + 1].value == "(":
                if t.value not in found:
                    found.append(t.value)
        i += 1
    return found


def _module_body(name: str, src: str) -> str:
    """提取指定 module 自身的定义体源码（从 `module <name>` 到配对的 `endmodule`）。

    背景（真实踩坑）：有些仓库用 `include` 把多个子模块拼进一个翻译单元
    （如 Single_Cycle_Top.v include 了 PC/ALU/寄存器堆等全部子模块）。此时
    若把整个文件源交给例化扫描，文件里每一个模块都会继承该文件的全部例化
    （叶子模块 ALU/Register_File 被误标成例化了 PC/存储器——那些其实是
    顶层 Single_Cycle_Top 的例化）。

    用 token 的行号定位 module 声明与配对 endmodule，只返回该模块自身
    定义体的源码。找不到时回退到整个文件源（保持旧行为）。
    """
    toks = sv_tokenize(src)
    start = None
    end_line = None
    for k, t in enumerate(toks):
        if start is None:
            if t.kind == "IDENT" and t.value == "module" and k + 1 < len(toks) \
                    and toks[k + 1].kind == "IDENT" and toks[k + 1].value == name:
                start = t.line
        else:
            if t.kind == "IDENT" and t.value == "endmodule":
                end_line = t.line
                break
    if start is None:
        # 该 module 在本文件的原始文本里没有 module 声明 —— 它是通过
        # `include 从别的文件展开进来的（slang 展开后误归到本文件）。
        # 返回空串（不归属任何例化），而不是回退到整个文件源。
        return ""
    # endmodule 找不到（罕见）时取到文件末尾
    lines = src.splitlines()
    end_idx = end_line if end_line is not None else len(lines)
    return "\n".join(lines[start - 1:end_idx])


def tool_index_repo(project_root: str) -> Dict[str, Any]:
    root = Path(project_root)
    if not root.is_dir():
        return err_result("index_repo", f"Directory not found: {project_root}")

    rtl_files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in RTL_EXTS)
    sources: Dict[Path, str] = {}
    parsed: List[Tuple[Path, SVModuleAST]] = []
    for fpath in rtl_files:
        sources[fpath] = fpath.read_text(encoding="utf-8", errors="ignore")
        if fpath.suffix.lower() in {".v", ".sv"}:
            parsed.extend((fpath, m) for m in parse_sv_file(fpath))

    known = {m.name for _, m in parsed}
    # SV interface 类型名也要算「已定义」：`axi_lite_if slave;` 在 slang CST 里
    # 同样是 HierarchyInstantiation 节点，若不排除会被误报成缺失模块（靶场实测
    # 45 条 W102 里绝大多数是 apb_if / axi_lite_if 这类 interface 声明）。
    iface_defined: Set[str] = set()
    for fpath in rtl_files:
        if fpath.suffix.lower() not in {".v", ".sv", ".vh", ".svh"}:
            continue
        iface_defined.update(_IFACE_DEF_RE.findall(sources[fpath]))
    headers = {p.name: str(p.relative_to(root)) for p in rtl_files if p.suffix.lower() in {".vh", ".svh"}}
    # header 之间也会互相 `include（`include "b.svh" 里再 `include "a.svh"）。
    # 旧实现只索引「文件名 -> 路径」，没有任何 header 级的 include 关系，于是
    # resolve_deps 只能拿到模块直接 include 的头文件，**传递依赖全丢**：
    # 实测 top.sv -> c.svh -> b.svh -> a.svh 只返回 header_files=["inc/c.svh"]，
    # 调用方 copy_sandbox 照着复制，沙箱里缺 b.svh/a.svh，Verilator 直接
    # `%Error: inc/c.svh:1:10: Cannot find include file: 'b.svh'`，而工具全程
    # ok=True。这里按 header 的仓库相对路径记录它自己的 include 列表，供
    # resolve_deps 做传递闭包。
    # `.sv` 文件也可能被 `include（`include "defs.sv" 里放 helper module 是老项目
    # 常见写法）。这类文件不能同时作为**编译单元**参与构建，否则其中定义的
    # module 会被看到两次：实测 resolve_deps 把 defs.sv 和 top.sv 都列进
    # rtl_files，Verilator 报 `%Error: Exiting due to 1 warning(s)`
    # （MODDUP: Duplicate declaration of module）。旧实现完全不区分「被 include
    # 的源文件」与「独立编译单元」。
    # 这里收集所有被任何文件 `include 的相对路径，rtl_files 里剔除它们；
    # they still appear in all_files (sandbox 需要复制它们)。
    included_files: Set[str] = set()
    _all_inc_targets: List[str] = []
    for _p in rtl_files:
        _all_inc_targets.extend(_INC_RE.findall(sources[_p]))
    _by_name: Dict[str, List[str]] = {}
    for _p in rtl_files:
        _by_name.setdefault(_p.name, []).append(str(_p.relative_to(root)))
    _rel_to_path = {str(q.relative_to(root)): q for q in rtl_files}
    for _inc in _all_inc_targets:
        _bn = _inc.replace("\\", "/").rsplit("/", 1)[-1]
        _hit = _rel_to_path.get(_inc) or (_rel_to_path.get(_by_name[_bn][0]) if _bn in _by_name else None)
        if _hit is None:
            continue
        # 只有「非 header 扩展名的源文件被 include」才算异常用法；
        # .vh/.svh 天生就是 header，已经由 header_files 表达。
        if _hit.suffix.lower() not in HDR_EXTS:
            included_files.add(str(_hit.relative_to(root)))
    header_includes: Dict[str, List[str]] = {}
    for p in rtl_files:
        if p.suffix.lower() not in {".vh", ".svh"}:
            continue
        rel = str(p.relative_to(root))
        header_includes[rel] = list(dict.fromkeys(_INC_RE.findall(sources[p])))

    # 归属修正：`include 会把子模块展开进顶层文件，导致同一模块出现在多个文件
    # 的解析结果里（如 datapath 同时出现在 datapath.v 和 include 它的
    # top_proc.v）。modules 字典按名去重时「排序靠后的文件覆盖前面的」，会把
    # datapath 错标成 top_proc.v。正确归属应是**原始文本里真正声明
    # `module <name>`** 的文件（_module_body 在该文件能定位到自身定义体）。
    owner: Dict[str, Path] = {}
    # `_module_body` 每次调用都要 sv_tokenize 整份源码。旧实现是
    # `for fpath: for mname in <全部模块名>` —— 即 O(文件数 × 模块数) 次重新
    # tokenize 整个仓库；一个 200 模块 / 100 文件的项目要 tokenize 两万次。
    # 改成每个文件只 tokenize 一次、直接取出它**原始文本里声明**的模块名集合。
    defined_here: Dict[Path, Set[str]] = {}
    for fpath in rtl_files:
        if fpath.suffix.lower() not in {".v", ".sv"}:
            continue
        local: Set[str] = set()
        toks = sv_tokenize(sources[fpath])
        for k, t in enumerate(toks):
            if (t.kind == "IDENT" and t.value == "module" and k + 1 < len(toks)
                    and toks[k + 1].kind == "IDENT"):
                local.add(toks[k + 1].value)
        defined_here[fpath] = local
        for mname in local:
            owner.setdefault(mname, fpath)

    # 未知例化检测：token 启发式只能认出仓库内**已定义**的模块（value in known），
    # 例化一个仓库外的模块（厂商 IP / 加密 IP / 未克隆子模块）会被静默丢弃，
    # 依赖闭包看起来完整、沙箱静默缺文件。放宽启发式又会让 `function int foo(`
    # 之类全误报（实测 106 条噪声）。因此用 slang CST 的 HierarchyInstantiation
    # 精确拿例化目标；slang 不可用时返回 None，退回旧行为且不报未知（宁缺勿噪）。
    cst_cache: Dict[str, Optional[List[str]]] = {}
    unresolved: Dict[str, List[str]] = {}
    modules = {}
    for fp, m in parsed:
        # 只处理「本文件原始文本里真正声明了该 module」的解析结果。
        # `include 让 slang 把子模块展开进顶层文件，于是同一模块会出现两次：
        # (datapath.v, datapath) 和 (top.v, datapath)。旧实现两个都处理，后者
        # 用 top.v 的源码算 instantiated_modules / includes，把 top 的例化与
        # include 结果**覆盖**到 datapath 上（实测 datapath 被错标成
        # includes=["datapath.v"]，而它自己并没有 include 任何东西）。
        # 归属由 owner 决定；不属于本文件的解析结果直接跳过。
        if owner.get(m.name, fp) != fp:
            continue
        if fp not in cst_cache:
            cst_cache[fp] = slang_instantiation_targets(fp)
        cst_targets = cst_cache[fp]
        # _module_body 返回该 module 自身的定义体；对归属正确的文件不会是空串
        # （owner 就是用「_module_body 非空」判定的），但仍保留回退以免空串
        # 让实例化成静默丢失。
        body = _module_body(m.name, sources[fp]) or sources[fp]
        insts = _find_instantiations(body, known, m.name)
        unknown: List[str] = []
        if cst_targets is not None:
            unknown = [
                t for t in cst_targets
                if t not in known and t not in iface_defined and t not in _NON_INST
            ]
        modules[m.name] = {
            "name": m.name,
            "file_path": str(owner.get(m.name, fp).relative_to(root)),
            "port_count": len(m.ports),
            "parameters": m.parameters,
            "instantiated_modules": insts,
            "unresolved_instantiations": unknown,
            # 归属已按 owner 收敛到「真正定义该 module 的文件」，所以这里的
            # sources[fp] 就是该模块自己的文件，include 不会串味。
            "includes": list(dict.fromkeys(_INC_RE.findall(sources[fp]))),
        }
        if unknown:
            unresolved[m.name] = unknown
    diags = [
        {
            "severity": "WARNING",
            "code": "W102",
            "path": mod,
            "message": (
                f"Instantiation of module '{child}' has no definition in this repo; "
                "the dependency closure will not cover it (vendor/encrypted IP?)."
            ),
        }
        for mod, children in sorted(unresolved.items())
        for child in children
    ]
    return ok_result("index_repo", {
        "project_root": str(root.resolve()),
        "total_rtl_files": len(rtl_files),
        "headers": headers,
        "header_includes": header_includes,
        "included_files": sorted(included_files),
        "modules": modules,
        "unresolved_instantiations": unresolved,
    }, diagnostics=diags)


def _resolve_include(inc: str, hdrs: Dict[str, str]) -> Optional[str]:
    """把 ``include 字符串解析成 header 的仓库相对路径；找不到返回 None。

    背景（真实踩坑）：``headers`` 按**文件名 basename** 建索引（``defs.svh`` ->
    ``inc/defs.svh``），但 RTL 里的 ``include 常带**路径前缀**（``inc/defs.svh``、
    ``rtl/a/defs.svh``）。旧实现直接 ``hdrs[inc]``，带前缀的 include 查不到 ->
    误报 W101「Header not found」并把真实存在的 header 排除在
    ``header_files`` / ``all_files`` 之外。调用方（如 copy_sandbox）拿这份清单
    去复制，就漏掉 header，沙箱 RTL 编译报「Cannot find include file」。

    这里两级查找：先按 include 的 basename 查 headers 的 key，再退回完整
    include 字符串查 headers 的 value（覆盖 headers 恰好按带前缀相对路径键入
    的索引）。命中任一即返回仓库相对路径。
    """
    basename = inc.replace("\\", "/").rsplit("/", 1)[-1]
    if basename in hdrs:
        return hdrs[basename]
    if inc in hdrs:
        return hdrs[inc]
    return None


def _collect_header(
    inc: str,
    hdrs: Dict[str, str],
    hdr_includes: Dict[str, List[str]],
    hdr_set: set,
    missing_inc: List[str],
    seen_hdr: set,
) -> None:
    """收集一个 `include 及其**传递**依赖的 header。

    `include 是文本包含，header 内部还可以再 `include 别的 header
    （``defs.svh`` 里 include ``base.svh`` 是常见写法）。旧实现只收集模块
    直接 include 的那一层，传递依赖全部丢失：调用方（copy_sandbox）照
    ``all_files`` 复制，沙箱里缺被间接引用的 header，编译报
    ``Cannot find include file``，而工具仍返回 ok=True。

    这里做深度优先展开：每解析出一个 header 就把它加入清单，并继续展开它
    自己的 include 列表。``seen_hdr`` 按 header 的 relative path 去重，
    循环 include（a 包含 b、b 又包含 a）不会死循环。
    """
    stack = [inc]
    while stack:
        cur = stack.pop()
        resolved = _resolve_include(cur, hdrs)
        if resolved is None:
            missing_inc.append(cur)
            continue
        if resolved in seen_hdr:
            continue
        seen_hdr.add(resolved)
        hdr_set.add(resolved)
        stack.extend(hdr_includes.get(resolved) or [])


def tool_resolve_deps(
    target_module: str,
    repo_index: Optional[Dict[str, Any]] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    if repo_index is None:
        if not project_root:
            return err_result("resolve_deps", "Provide 'repo_index' or 'project_root'.")
        idx = tool_index_repo(project_root)
        if not idx["ok"]:
            return err_result("resolve_deps", idx["error"] or "Indexing failed.")
        repo_index = idx["data"]

    mods, hdrs = repo_index.get("modules", {}), repo_index.get("headers", {})
    hdr_includes = repo_index.get("header_includes") or {}
    incl_targets = set(repo_index.get("included_files") or [])
    if target_module not in mods:
        return err_result("resolve_deps", f"Module '{target_module}' not found. Available: {sorted(mods)}")

    closure, seen, edges = [], set(), []
    rtl_set, hdr_set, missing_inc = set(), set(), []
    seen_hdr: set = set()
    q = deque([target_module])
    while q:
        cur = q.popleft()
        if cur in seen:
            continue
        seen.add(cur)
        closure.append(cur)
        info = mods[cur]
        rtl_set.add(info["file_path"])
        for inc in info.get("includes", []):
            # 带路径前缀的 include（inc/defs.svh）也要按 basename 解析到 headers，
            # 否则真实存在的 header 被误报 W101 并漏出 staged 清单（缺陷 102）。
            _collect_header(inc, hdrs, hdr_includes, hdr_set, missing_inc, seen_hdr)
        for child in info.get("instantiated_modules", []):
            edges.append({"from": cur, "to": child})
            if child not in seen and child in mods:
                q.append(child)

    diags = [{"severity": "WARNING", "code": "W101", "path": inc, "message": f"Header '{inc}' not found."} for inc in sorted(set(missing_inc))]
    # 透传未知例化：闭包里某个模块例化了仓库外模块时，依赖闭包并不完整
    # （少那些模块的 rtl_files）。只在 index_repo 报 W102 不够——调用方常把
    # repo_index 直接喂给 resolve_deps 拿文件清单去 copy_sandbox，若这里不透传，
    # 它仍然会得到一份「看起来完整」的文件列表。只报闭包内模块的，避免噪声。
    unresolved_in_closure: Dict[str, List[str]] = {}
    for mname in closure:
        kids = mods.get(mname, {}).get("unresolved_instantiations") or []
        if kids:
            unresolved_in_closure[mname] = list(kids)
            for child in kids:
                diags.append({
                    "severity": "WARNING",
                    "code": "W102",
                    "path": mname,
                    "message": (
                        f"Instantiation of module '{child}' has no definition in this repo; "
                        "the dependency closure does not cover it (vendor/encrypted IP?)."
                    ),
                })
    # 语义：rtl_files = 闭包模块的归属文件，**去掉那些已经被闭包内其它
    # 编译单元 include 展开的**。
    #
    # 为什么不是简单地「去掉所有 included_files」：
    # - 目标是 dupinc_top（top.sv include defs.sv，defs.sv 定义 helper_mod，
    #   top 例化 helper_mod）：编译 top.sv 时 include 已把 helper_mod 展开，
    #   再把 defs.sv 当编译单元 → MODDUP 硬失败。所以 defs.sv 应剔除。
    # - 目标是 dup_mod（它只定义在 real.sv 里，而 real.sv 被 wrapper include）：
    #   闭包内**没有**其它编译单元会展开它（wrapper 不在闭包里），剔除会让
    #   rtl_files 变成空列表，调用方拿空清单编译必然失败。此时必须保留。
    #
    # 判据：rtl_files = 闭包归属文件 - (被 include 的文件 ∩ 被其它闭包归属
    # 文件 include 的文件)。
    _closure_files = {mods[_m]["file_path"] for _m in closure if _m in mods}
    # `include 目标既可能是 header（.vh/.svh，在 hdrs 里），也可能是被 include
    # 的源文件（`include "defs.sv"），后者不在 hdrs 里。这里用同一条
    # 「先按相对路径、再按 basename」规则，把两类都解析出来。
    _all_rel = set(incl_targets) | {_p for _p in hdrs.values()}
    _inc_by_closure: Set[str] = set()
    for _m in closure:
        _info = mods.get(_m) or {}
        _owner = _info.get("file_path")
        for _inc in _info.get("includes") or []:
            _resolved = _resolve_include(_inc, hdrs)
            if _resolved is None:
                _bn = _inc.replace("\\", "/").rsplit("/", 1)[-1]
                for _cand in _all_rel:
                    if _cand == _inc or _cand.rsplit("/", 1)[-1] == _bn:
                        _resolved = _cand
                        break
            if _resolved and _resolved != _owner:
                _inc_by_closure.add(_resolved)
    return ok_result("resolve_deps", {
        "target_module": target_module,
        "target_file": mods[target_module]["file_path"],
        "dependency_closure": closure,
        "dependency_edges": edges,
        "unresolved_instantiations": unresolved_in_closure,
        "closure_is_complete": not unresolved_in_closure,
        # 被 include 进来的「源文件型 header」（如 `include "defs.sv"）通常不能
        # 作为独立编译单元，否则重复定义 module。但**例外**：当闭包里某个模块
        # 的定义文件本身就是被 include 的那个文件时（目标是 `dup_mod`，它只
        # 定义在 `real.sv` 里，而 `real.sv` 恰好被 wrapper include），把它剔除
        # 会让 rtl_files 变成**空列表** —— 调用方拿空清单去编译必然失败。
        # 因此只剔除「不属于任何闭包模块定义文件」的 include 目标。
        "rtl_files": sorted(rtl_set - (_inc_by_closure & _closure_files)),
        "header_files": sorted(hdr_set),
        "all_files": sorted(rtl_set | hdr_set),
    }, diagnostics=diags)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    # 传了 --target-module 走依赖闭包，否则只建索引
    pick = "resolve_deps" if any(a.startswith("--target-module") or a.startswith("--target_module") for a in sys.argv[1:]) else "index_repo"
    sys.exit(run_single_tool(pick))
