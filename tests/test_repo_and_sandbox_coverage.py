"""仓库索引 / 依赖闭包 / 沙箱拷贝（含原子性）

仓库索引 / 依赖闭包 / 沙箱拷贝（含原子性）

从 tests/test_full_coverage.py 按领域拆分而来（原文件 ~5500 行、86 个用例，
定位失败需要通读全文）。拆分只搬移代码，不改断言。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench  # noqa: E402
from fpga_toolbox.registry import call_tool  # noqa: E402
from fpga_toolbox.sandbox_copier import tool_copy_sandbox  # noqa: E402
from fpga_toolbox.tb_linter import tool_lint_tb  # noqa: E402


def test_sandbox_clear_dir_marked_nested() -> None:
    print("coverage: sandbox_copier._clear_dir on marked sandbox with nested dirs & symlinks")
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        proj = td_p / "proj"
        proj.mkdir()
        (proj / "a.sv").write_text("module a; endmodule\n", encoding="utf-8")
        (proj / "b.sv").write_text("module b; endmodule\n", encoding="utf-8")
        sb = td_p / "sb"

        r1 = tool_copy_sandbox(str(proj), ["a.sv"], str(sb), clean=True)
        assert r1["ok"] and (sb / "a.sv").is_file()

        # Plant nested subdir + file + symlink inside the ALREADY-MARKED sandbox
        nested = sb / "stale_sub"
        nested.mkdir()
        (nested / "old.txt").write_text("stale", encoding="utf-8")
        (sb / "stale_link").symlink_to(nested / "old.txt")

        # Second copy with clean=True invokes _clear_dir() and wipes stale_sub + stale_link
        r2 = tool_copy_sandbox(str(proj), ["b.sv"], str(sb), clean=True)
        assert r2["ok"]
        assert not nested.exists()
        assert not (sb / "stale_link").exists()
        assert not (sb / "a.sv").exists()
        assert (sb / "b.sv").is_file()

def test_copy_sandbox_failure_leaves_no_partial_output() -> None:
    """缺陷: copy_sandbox 校验失败时留下半成品沙箱（非原子）。

    旧实现边校验边写：先 `mkdir` + 写 `.fpga_sandbox` 标记，然后逐项校验并
    立刻 `copy2`。中途任一项失败（符号链接逃逸、文件缺失、路径非法）就直接
    return err_result —— 此时前面几项**已经写进沙箱**：

        relative_files=['good.sv', 'evil.sv']   （evil.sv -> /etc/passwd）
        → ok=false
        → 但沙箱里已有 good.sv + .fpga_sandbox

    调用方看到 ok=false 不会想到沙箱里已有内容；下次运行还可能复用这些陈旧
    文件。修复为两阶段：先完成全部只读预校验，全部通过后才写盘。
    """
    print("coverage: copy_sandbox failure leaves no partial output")

    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        proj.mkdir()
        (proj / "good.sv").write_text("module good(); endmodule\n", encoding="utf-8")
        outside = Path(td) / "outside.txt"
        outside.write_text("SECRET\n", encoding="utf-8")
        try:
            (proj / "evil.sv").symlink_to(outside)
        except (OSError, NotImplementedError):
            return  # 平台不支持符号链接时跳过

        sb = Path(td) / "sb"
        r = call_tool("copy_sandbox", {
            "project_root": str(proj), "sandbox_dir": str(sb),
            "relative_files": ["good.sv", "evil.sv"]})
        assert r["ok"] is False, r
        # 关键：失败时不得创建/污染目标目录
        assert not sb.exists(),             f"sandbox left behind after failure: {sorted(p.name for p in sb.iterdir()) if sb.is_dir() else '?'}"

        # 失败也不得影响项目源目录
        assert (proj / "good.sv").is_file()

        # 预校验失败时，已存在的沙箱内容必须原封不动（不被 clean 掉）
        sb2 = Path(td) / "sb2"
        sb2.mkdir()
        (sb2 / "keep.txt").write_text("KEEP\n", encoding="utf-8")
        (sb2 / ".fpga_sandbox").write_text("marker\n", encoding="utf-8")
        r2 = call_tool("copy_sandbox", {
            "project_root": str(proj), "sandbox_dir": str(sb2),
            "relative_files": ["good.sv", "evil.sv"]})
        assert r2["ok"] is False, r2
        # 旧实现会先 _clear_dir 再写，失败后 keep.txt 已消失
        assert (sb2 / "keep.txt").is_file(), "existing sandbox content was wiped despite failure"

        # 成功路径不受影响
        r3 = call_tool("copy_sandbox", {
            "project_root": str(proj), "sandbox_dir": str(Path(td) / "sb3"),
            "relative_files": ["good.sv"]})
        assert r3["ok"], str(r3.get("error"))[:200]
        assert r3["data"]["file_count"] == 1, r3["data"]

def test_resolve_deps_path_prefixed_include() -> None:
    """Defect 102: 带路径前缀的 include 被误报 W101 并漏出 staged 清单。

    headers 按 basename 建索引（defs.svh -> inc/defs.svh），但 RTL 里的
    ``include 常带路径前缀（inc/defs.svh）。旧实现直接 hdrs[inc] 查不到 ->
    真实存在的 header 被误报 W101 not-found 并排除在 header_files / all_files
    之外，copy_sandbox 拿这份清单去复制就漏掉 header，沙箱 RTL 编译报
    「Cannot find include file」。
    """
    print("coverage: resolve_deps path-prefixed include (inc/defs.svh)")
    from fpga_toolbox.repo_indexer import tool_index_repo, tool_resolve_deps

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "inc").mkdir()
        (root / "inc" / "defs.svh").write_text("parameter W = 8;\n", encoding="utf-8")
        (root / "top.sv").write_text(
            'module top;\n`include "inc/defs.svh"\nendmodule\n', encoding="utf-8")
        idx = tool_index_repo(str(root))
        assert idx["ok"], idx.get("error")
        r = tool_resolve_deps("top", repo_index=idx["data"])
        assert r["ok"], r.get("error")
        # 真实存在的 header 必须进 staged 清单
        assert "inc/defs.svh" in r["data"]["header_files"], r["data"]["header_files"]
        assert "inc/defs.svh" in r["data"]["all_files"], r["data"]["all_files"]
        # 不应再误报 W101 not-found
        w101 = [d for d in r.get("diagnostics", []) if d.get("code") == "W101"]
        assert not w101, w101

        # 真正缺失的 header（basename 形式与带前缀形式）仍报 W101
        (root / "top2.sv").write_text(
            'module top2;\n`include "nowhere.svh"\n`include "sub/missing.svh"\nendmodule\n',
            encoding="utf-8")
        idx2 = tool_index_repo(str(root))
        r2 = tool_resolve_deps("top2", repo_index=idx2["data"])
        assert r2["ok"]
        w101_2 = sorted(d["path"] for d in r2.get("diagnostics", []) if d.get("code") == "W101")
        assert w101_2 == ["nowhere.svh", "sub/missing.svh"], w101_2
        assert r2["data"]["header_files"] == []

def test_resolve_deps_transitive_headers() -> None:
    """Defect 132: resolve_deps 必须展开 header 之间的传递 include。

    `include 是文本包含，header 内部还能再 include 别的 header。旧实现只收集
    模块**直接** include 的那一层，传递依赖全丢：top.sv -> c.svh -> b.svh ->
    a.svh 只返回 header_files=["inc/c.svh"]。调用方把 all_files 喂给
    copy_sandbox，沙箱里就缺 b.svh / a.svh，Verilator 报
    %Error: inc/c.svh:1:10: Cannot find include file: 'b.svh' —— 而整条链
    全程 ok=True（实测复现）。
    """
    print("coverage: resolve_deps transitive header closure")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inc = root / "inc"
        inc.mkdir()
        (inc / "a.svh").write_text("`define A 1\n", encoding="utf-8")
        (inc / "b.svh").write_text('`include "a.svh"\n`define B 2\n', encoding="utf-8")
        (inc / "c.svh").write_text(
            '`include "b.svh"\n`include "a.svh"\n`define C 3\n', encoding="utf-8")
        (root / "top.sv").write_text(
            '`include "c.svh"\n`include "c.svh"\nmodule top(input logic clk); endmodule\n',
            encoding="utf-8")

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "top"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        hdrs = data["header_files"]
        assert hdrs == ["inc/a.svh", "inc/b.svh", "inc/c.svh"], hdrs
        assert data["all_files"] == ["inc/a.svh", "inc/b.svh", "inc/c.svh", "top.sv"], \
            data["all_files"]
        # 传递依赖齐全时不应有任何 W101
        assert not [d for d in (r.get("diagnostics") or []) if d["code"] == "W101"], r["diagnostics"]

        # 沙箱复制必须带齐所有传递 header，并且真的能编译
        sb = root / "_sb"
        c = call_tool("copy_sandbox", {
            "project_root": str(root), "sandbox_dir": str(sb),
            "relative_files": data["all_files"]})
        assert c["ok"], str(c.get("error"))[:200]
        for rel in ("inc/a.svh", "inc/b.svh", "inc/c.svh", "top.sv"):
            assert (sb / rel).is_file(), f"missing in sandbox: {rel}"

        # 循环 include 不能死循环
        cyc = root / "_cyc"
        cyc.mkdir()
        (cyc / "x.svh").write_text('`include "y.svh"\n`define X 1\n', encoding="utf-8")
        (cyc / "y.svh").write_text('`include "x.svh"\n`define Y 2\n', encoding="utf-8")
        (cyc / "topc.sv").write_text(
            '`include "x.svh"\nmodule topc(input logic clk); endmodule\n', encoding="utf-8")
        rc = call_tool("resolve_deps", {"project_root": str(cyc), "target_module": "topc"})
        assert rc["ok"], str(rc.get("error"))[:200]
        assert rc["data"]["header_files"] == ["x.svh", "y.svh"], rc["data"]["header_files"]

        # 传递依赖里缺 header：必须报 W101，而不是静默给出不完整清单
        miss = root / "_miss"
        miss.mkdir()
        (miss / "outer.svh").write_text('`include "ghost.svh"\n`define O 1\n', encoding="utf-8")
        (miss / "topm.sv").write_text(
            '`include "outer.svh"\nmodule topm(input logic clk); endmodule\n', encoding="utf-8")
        rm = call_tool("resolve_deps", {"project_root": str(miss), "target_module": "topm"})
        assert rm["ok"], str(rm.get("error"))[:200]
        codes = [(d["code"], d["path"]) for d in (rm.get("diagnostics") or [])]
        assert ("W101", "ghost.svh") in codes, codes

def test_included_source_not_treated_as_compilation_unit() -> None:
    """Defect 133: 被 `include 的 .sv 不能被当成独立编译单元。

    ``include "defs.sv"`` 里放 helper module 是老项目常见写法。旧实现把
    defs.sv 和 top.sv 都列进 resolve_deps 的 rtl_files，调用方拿这份清单
    去编译 → module 被看到两次，Verilator 报
    ``%Error: Exiting due to 1 warning(s)``（MODDUP: Duplicate declaration of
    module）。这是默认配置下的**硬失败**（Verilator 默认 warning-as-error）。
    """
    print("coverage: included .sv source is not a compilation unit")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "defs.sv").write_text(
            "module helper_mod (input logic a, output logic b);\n"
            "  assign b = a;\n"
            "endmodule\n", encoding="utf-8")
        (root / "top.sv").write_text(
            '`include "defs.sv"\n'
            "module dupinc_top (input logic clk, output logic q);\n"
            "  helper_mod u (.a(clk), .b(q));\n"
            "endmodule\n", encoding="utf-8")

        idx = call_tool("index_repo", {"project_root": str(root)})
        assert idx["ok"], str(idx.get("error"))[:200]
        assert idx["data"]["included_files"] == ["defs.sv"], idx["data"]["included_files"]

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "dupinc_top"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        # 编译单元只有 top.sv；defs.sv 通过 include 展开拿到
        assert data["rtl_files"] == ["top.sv"], data["rtl_files"]
        # 但沙箱清单必须仍然包含 defs.sv（否则 include 找不到）
        assert data["all_files"] == ["defs.sv", "top.sv"], data["all_files"]
        assert data["dependency_closure"] == ["dupinc_top", "helper_mod"], \
            data["dependency_closure"]

        # .vh/.svh 天生是 header，不该被算进 included_files
        (root / "defs_h.svh").write_text("`define D 1\n", encoding="utf-8")
        (root / "top2.sv").write_text(
            '`include "defs_h.svh"\nmodule top2(input logic clk); endmodule\n', encoding="utf-8")
        idx2 = call_tool("index_repo", {"project_root": str(root)})
        assert idx2["data"]["included_files"] == ["defs.sv"], idx2["data"]["included_files"]

def test_rtl_files_keeps_independently_compilable_modules() -> None:
    """Defect 135: 剔除被 include 的源文件时必须保留「独立可编译」的目标。

    133 的修法「rtl_files = rtl_set - included_files」过于粗暴：当目标模块
    **本身只定义在被 include 的文件里**时（`dup_mod` 定义在 `real.sv`，而
    `real.sv` 恰好被 `wrapper.sv` include），rtl_files 会变成**空列表** ——
    调用方拿空清单编译必然失败。

    正确判据：只剔除「被闭包内其它编译单元 include 展开」的文件。
    - 目标 = dupinc_top（top.sv include defs.sv，defs.sv 定义 helper_mod）：
      defs.sv 应剔除（否则 MODDUP），rtl_files == ["top.sv"]
    - 目标 = dup_mod（只定义在 real.sv，real.sv 被 wrapper include 但
      wrapper 不在闭包内）：real.sv 必须保留，rtl_files == ["rtl/real.sv"]
    """
    print("coverage: rtl_files keeps independently compilable modules")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "defs.sv").write_text(
            "module helper_mod (input logic a, output logic b);\n  assign b = a;\nendmodule\n",
            encoding="utf-8")
        (root / "top.sv").write_text(
            '`include "defs.sv"\nmodule dupinc_top (input logic clk, output logic q);\n'
            "  helper_mod u (.a(clk), .b(q));\nendmodule\n", encoding="utf-8")

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "dupinc_top"})
        assert r["ok"], str(r.get("error"))[:200]
        assert r["data"]["rtl_files"] == ["top.sv"], r["data"]["rtl_files"]

        # 目标正是被 include 文件里定义的模块：不能把清单清空
        rh = call_tool("resolve_deps", {"project_root": str(root), "target_module": "helper_mod"})
        assert rh["ok"], str(rh.get("error"))[:200]
        assert rh["data"]["rtl_files"] == ["defs.sv"], rh["data"]["rtl_files"]

        # 子目录 + 目标在 include 目标文件中
        rtl = root / "rtl"
        rtl.mkdir()
        (rtl / "real.sv").write_text(
            "module dup_mod (input logic clk, output logic [7:0] q);\n"
            "  assign q = 8'hAA;\nendmodule\n", encoding="utf-8")
        (rtl / "wrapper.sv").write_text(
            '`include "real.sv"\nmodule wrapper (input logic clk, output logic [7:0] q);\n'
            "  dup_mod u (.clk(clk), .q(q));\nendmodule\n", encoding="utf-8")

        rw = call_tool("resolve_deps", {"project_root": str(root), "target_module": "wrapper"})
        assert rw["ok"], str(rw.get("error"))[:200]
        assert rw["data"]["rtl_files"] == ["rtl/wrapper.sv"], rw["data"]["rtl_files"]
        rm = call_tool("resolve_deps", {"project_root": str(root), "target_module": "dup_mod"})
        assert rm["ok"], str(rm.get("error"))[:200]
        assert rm["data"]["rtl_files"] == ["rtl/real.sv"], rm["data"]["rtl_files"]
        # 沙箱清单始终完整
        assert rm["data"]["all_files"] == ["rtl/real.sv"], rm["data"]["all_files"]

def test_package_imports_are_part_of_the_dependency_closure() -> None:
    """Defect 147: `import pkg::*` 依赖的 package 文件必须进 closure。

    `import cfg_pkg::*;` 与 `` `include `` 不同 —— package 通常在自己的文件里，
    必须作为独立编译单元一起编译。旧实现完全不索引 package：

        resolve_deps(top_mod) -> rtl_files=["top.sv","user.sv"]

    user.sv 有 `import cfg_pkg::*;` 但 pkg.sv 不在清单里，Verilator 报
    `%Error: user.sv:2:10: Import package not found: 'cfg_pkg'`，而工具报
    closure_is_complete=true / ok=true。仓库自带的
    examples/axi_apb_ref/axi/axi_apb_bridge.sv 单文件就复现同样错误。
    """
    print("coverage: package imports are part of the closure")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg.sv").write_text(
            "package cfg_pkg;\n  parameter int W = 8;\nendpackage\n", encoding="utf-8")
        (root / "user.sv").write_text(
            "module user_mod (input logic [7:0] d, output logic [7:0] q);\n"
            "  import cfg_pkg::*;\n"
            "  assign q = d;\nendmodule\n", encoding="utf-8")
        (root / "top.sv").write_text(
            "module top_mod (input logic [7:0] d, output logic [7:0] q);\n"
            "  user_mod u (.d(d), .q(q));\nendmodule\n", encoding="utf-8")

        idx = call_tool("index_repo", {"project_root": str(root)})
        assert idx["ok"], str(idx.get("error"))[:200]
        assert idx["data"]["packages"] == {"cfg_pkg": "pkg.sv"}, idx["data"]["packages"]
        assert idx["data"]["modules"]["user_mod"]["package_imports"] == ["cfg_pkg"], \
            idx["data"]["modules"]["user_mod"]

        r = call_tool("resolve_deps", {"project_root": str(root), "target_module": "top_mod"})
        assert r["ok"], str(r.get("error"))[:200]
        data = r["data"]
        assert data["rtl_files"] == ["pkg.sv", "top.sv", "user.sv"], data["rtl_files"]
        assert data["all_files"] == ["pkg.sv", "top.sv", "user.sv"], data["all_files"]

        # 沙箱必须包含 package 文件
        sb = root / "_sb"
        c = call_tool("copy_sandbox", {
            "project_root": str(root), "sandbox_dir": str(sb),
            "relative_files": data["all_files"]})
        assert c["ok"], str(c.get("error"))[:200]
        assert (sb / "pkg.sv").is_file(), "package file missing from sandbox"

        # 不 import package 的模块不应把 package 拖进来
        (root / "solo.sv").write_text(
            "module solo (input logic a, output logic b);\n"
            "  assign b = a;\nendmodule\n", encoding="utf-8")
        rs = call_tool("resolve_deps", {"project_root": str(root), "target_module": "solo"})
        assert rs["ok"], str(rs.get("error"))[:200]
        assert rs["data"]["rtl_files"] == ["solo.sv"], rs["data"]["rtl_files"]

def test_staged_rtl_files_reach_the_parser() -> None:
    """Defect 161: `staged_rtl_files` 过去从未传给解析器。

    参数描述承诺 "Optional additional RTL files compiled together (e.g.
    packages) for cross-file imports."，但实现里它**只**出现在返回值的
    `rtl_files` 字段中，没有传给 parse_sv_file / slang 上下文：

        grep staged_rtl_files iface_extractor.py
        → 418: 形参
        → 468: "rtl_files": staged_rtl_files or [...]

    后果：不存在的路径、目录都能通过校验；即使传了真实文件，对解析结果也是
    零影响。slang 的自动收集只认 `*_pkg.sv` / `*_if.sv` 命名，命名不规范的
    package（如 `types.sv`）只能靠这个参数补充 —— 而它没生效。
    """
    print("coverage: staged_rtl_files reach the parser")
    from fpga_toolbox.core.sv_parser import _find_slang_context

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        proj = root / "proj"
        (proj / "rtl").mkdir(parents=True)
        (proj / "inc").mkdir()
        (proj / "inc" / "defs.svh").write_text("`define W 8\n", encoding="utf-8")
        # package 名不规范，slang 自动收集（*_pkg.sv / *_if.sv）抓不到
        (proj / "types.sv").write_text(
            "package types_named_pkg;\n  typedef enum logic [1:0] { A1, A2 } mode_e;\n"
            "endpackage\n", encoding="utf-8")
        rtl = proj / "rtl" / "dut.sv"
        rtl.write_text(
            "module dut\n  import types_named_pkg::*;\n(\n"
            "  input  logic clk,\n  input  logic rst_n,\n"
            "  input  logic psel,\n  input  logic penable,\n  input  logic pwrite,\n"
            "  input  logic [7:0] paddr,\n  input  logic [31:0] pwdata,\n"
            "  input  logic [3:0] pstrb,\n"
            "  output logic pready,\n  output logic [31:0] prdata,\n"
            "  output logic pslverr\n);\n"
            "  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
            "endmodule\n", encoding="utf-8")

        # (a) staged 文件必须真正进入 slang 编译上下文
        auto_extra, _ = _find_slang_context(rtl)
        assert "types.sv" not in " ".join(auto_extra), auto_extra
        staged_extra, _ = _find_slang_context(rtl, [str(proj / "types.sv")])
        assert str((proj / "types.sv").resolve()) in staged_extra, staged_extra

        # (b) 不存在的 staged 条目必须被拒绝（旧实现静默接受）
        r_bad = call_tool("extract_interfaces", {
            "rtl_file": str(rtl), "staged_rtl_files": [str(root / "nope.sv")]})
        assert r_bad["ok"] is False, r_bad
        assert "Invalid staged_rtl_files" in str(r_bad.get("error")), str(r_bad.get("error"))[:200]

        # 目录同样拒绝
        r_dir = call_tool("extract_interfaces", {
            "rtl_file": str(rtl), "staged_rtl_files": [str(proj)]})
        assert r_dir["ok"] is False, r_dir
        assert "directory" in str(r_dir.get("error")), str(r_dir.get("error"))[:200]

        # (c) 相对路径支持三种基准：rtl 目录 / project root / cwd
        for rel in ("types.sv", "proj/types.sv"):
            rr = call_tool("extract_interfaces", {
                "rtl_file": str(rtl), "staged_rtl_files": [rel]})
            # 只有能从某个基准解析到才算合法；解不到就该明确报错（不能静默）
            if not rr["ok"]:
                assert "Invalid staged_rtl_files" in str(rr.get("error")), rr["error"]

        # project-root 相对路径：当 rtl 位于 <root>/rtl/ 时，基准之一是
        # rtl.parent.parent（= <root>），于是 "types.sv" 可直接解析到。
        # 上面已用绝对路径覆盖「合法 staged 可传」；这里补 <root> 相对路径。
        r_ok = call_tool("extract_interfaces", {
            "rtl_file": str(rtl),
            "staged_rtl_files": ["types.sv", "inc/defs.svh"]})
        assert r_ok["ok"] is True, str(r_ok.get("error"))[:250]

def test_tb_builds_for_rtl_without_timescale() -> None:
    """Defect 137: DUT 不写 `timescale 时 TB 构建必须仍然成功。

    TB 头部有 ``timescale 1ns/1ps``，但大量真实 RTL 把 timescale 交给编译
    选项、文件里不写。Verilator 5.x 对「部分模块有、部分没有」报
    TIMESCALEMOD，并在默认配置下当 error（"Exiting due to N warning(s)"）：
    一个完全合法的 DUT 直接构建失败，而 emit_testbench 返回 ok=True。

    修复方式是给 verilator 统一传 `--timescale 1ns/1ps`（与 tb_emitter 的
    COMPILE_ARGS 口径一致），而不是压制告警。
    """
    print("coverage: TB run script supplies --timescale for RTL without one")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dut = root / "apb_nots.sv"
        dut.write_text(
            "module apb_nots (\n"
            "  input  logic        clk,\n"
            "  input  logic        rst_n,\n"
            "  input  logic        psel,\n"
            "  input  logic        penable,\n"
            "  input  logic        pwrite,\n"
            "  input  logic [7:0]  paddr,\n"
            "  input  logic [31:0] pwdata,\n"
            "  input  logic [3:0]  pstrb,\n"
            "  output logic        pready,\n"
            "  output logic [31:0] prdata,\n"
            "  output logic        pslverr\n"
            ");\n"
            "  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
            "endmodule\n", encoding="utf-8")
        r = emit_protocol_testbench(str(dut), str(root / "tb"), "apb")
        assert r["ok"], str(r.get("error"))[:300]
        script = Path(r["data"]["artifacts"]["sim_script"]).read_text(encoding="utf-8")
        assert "--timescale 1ns/1ps" in script, script[:400]
        # 不能靠压制告警掩饰：真正的 verilator 调用行里不该出现抑制 TIMESCALEMOD
        vlt_lines = [ln for ln in script.splitlines()
                     if '"$VLT"' in ln and "--binary" in ln]
        assert vlt_lines, script[:400]
        assert not any("Wno-TIMESCALEMOD" in ln for ln in vlt_lines), vlt_lines

def test_duplicate_module_definitions_are_reported() -> None:
    """缺陷 195: 同一 module 在多个文件定义时静默取第一个，零诊断。

    真实成因很常见：目录里残留旧版本文件、`_bak`/`_old` 备份、从别处拷来的
    同名模块。索引按文件顺序取第一个当归属，另一个定义静默消失；但
    `resolve_deps` 会把**两个文件都**放进 `rtl_files`，一起交给编译器就是

        %Warning-MODDUP: Duplicate declaration of module: 'x'
        %Error: Exiting due to 1 warning(s)     （Verilator 默认 warning-as-error）

    修复：`index_repo` 检出重复定义并报 W058，列出所有文件。

    同时要排除「`include 展开」造成的假重名：被 include 的源文件里的模块会同时
    出现在自己文件和 includer 的解析结果里，但那不是重复定义。
    """
    print("coverage: duplicate module definitions are reported")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.sv").write_text(
            "module dupmod(input logic clk, output logic q);\n  assign q = 1'b1;\n"
            "endmodule\n", encoding="utf-8")
        (root / "b.sv").write_text(
            "module dupmod(input logic clk, output logic q);\n  assign q = 1'b0;\n"
            "endmodule\n"
            "module user(input logic clk, output logic q);\n"
            "  dupmod u(.clk(clk), .q(q));\nendmodule\n", encoding="utf-8")

        r = call_tool("index_repo", {"project_root": str(root)})
        assert r["ok"], str(r.get("error"))[:200]
        w058 = [d for d in (r.get("diagnostics") or []) if d["code"] == "W058"]
        assert len(w058) == 1, r.get("diagnostics")
        assert w058[0]["path"] == "dupmod", w058[0]
        # 两个文件都要被点名
        msg = w058[0]["message"]
        assert "a.sv" in msg and "b.sv" in msg, msg

        # include 展开的假重名不得误报
        inc_root = root / "_inc"
        inc_root.mkdir()
        (inc_root / "real.sv").write_text(
            "module helper_mod(input logic a, output logic b);\n  assign b = a;\n"
            "endmodule\n", encoding="utf-8")
        (inc_root / "top.sv").write_text(
            '`include "real.sv"\n'
            "module top_wrap(input logic clk, output logic q);\n"
            "  helper_mod u(.a(clk), .b(q));\nendmodule\n", encoding="utf-8")
        r2 = call_tool("index_repo", {"project_root": str(inc_root)})
        assert r2["ok"], str(r2.get("error"))[:200]
        assert not [d for d in (r2.get("diagnostics") or []) if d["code"] == "W058"], \
            r2.get("diagnostics")

def test_review_fixes_index_repo_and_linter_routing() -> None:
    """Defect 114: index_repo include 覆盖 + lint_tb 规则路由/前缀盲区/S4 字符串绕过。

    * index_repo：`include 让子模块出现在多个文件的解析结果里，旧实现两个都处理，
      后者用顶层文件源码算 instantiated_modules/includes，把顶层的例化与 include
      **覆盖**到子模块上（实测 datapath 被错标 includes=["datapath.v"]）。
    * lint_tb：S1 写在 scan_handshake 里（`rules=["silent_pass"]` 跳过它、
      `rules=["handshake"]` 反而报它）；`_IFACE_RE` 的 `[sm]_[ap]x[i]` 匹配不了
      s_apb/m_apb；R2/R4 写死 `axi.` 前缀，s_axi./m_axi./裸信号全部漏报；
      S4 用朴素 `//` 剥离且不剥字符串，`$display("no error(s)")` 冒充判定。
    """
    print("coverage: index_repo include ownership + lint_tb rule routing/prefix/string")
    from fpga_toolbox.repo_indexer import tool_index_repo

    # ---- index_repo：include 不得污染子模块 ----
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "rtl").mkdir()
        (root / "rtl" / "datapath.v").write_text(
            "module datapath(input logic a, output logic y);\nassign y = ~a;\nendmodule\n",
            encoding="utf-8")
        (root / "rtl" / "sub.v").write_text(
            "module sub(input logic a, output logic y);\nassign y = a;\nendmodule\n",
            encoding="utf-8")
        (root / "rtl" / "top.v").write_text(
            '`include "datapath.v"\n'
            "module top(input logic a, output logic y);\n"
            "  logic md;\n  datapath u_dp(.a(a), .y(md));\n  sub u_sub(.a(md), .y(y));\n"
            "endmodule\n", encoding="utf-8")
        r = tool_index_repo(str(root))
        assert r["ok"], r.get("error")
        mods = r["data"]["modules"]
        assert mods["datapath"]["file_path"] == "rtl/datapath.v", mods["datapath"]
        assert mods["datapath"]["instantiated_modules"] == [], mods["datapath"]
        assert mods["datapath"]["includes"] == [], mods["datapath"]
        assert mods["top"]["instantiated_modules"] == ["datapath", "sub"], mods["top"]
        assert mods["top"]["includes"] == ["datapath.v"], mods["top"]

    # ---- lint_tb：S1 必须路由到 silent_pass ----
    def _lint(src: str, rules, suffix=".sv") -> list:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / f"tb{suffix}"
            f.write_text(src, encoding="utf-8")
            res = tool_lint_tb(str(f), rules=rules)
            return sorted({i["code"] for i in res["data"]["findings"]})

    stub = "module tb;\nfunction int chk();\nreturn 0;\nendfunction\nendmodule\n"
    assert "S1" in _lint(stub, ["silent_pass"]), "S1 missed when scanning silent_pass only"
    assert "S1" not in _lint(stub, ["handshake"]), "S1 wrongly emitted by handshake scanner"

    # ---- lint_tb：R2/R4 必须覆盖 s_axi./m_axi./裸信号 ----
    for sig in ("axi", "s_axi", "m_axi"):
        r4 = _lint(f"module tb;\ninitial begin\nwhile (!{sig}.bvalid) @(posedge clk);\n"
                   f"{sig}.bready <= 1'b0;\nend\nendmodule\n", ["handshake"])
        assert "R4" in r4, (sig, r4)
        r2 = _lint(f"module tb;\nalways @(posedge clk) begin\nif ({sig}.bvalid) begin\n"
                   f"{sig}.bready <= 1'b0;\nend\nend\nendmodule\n", ["handshake"])
        assert "R2" in r2, (sig, r2)

    # ---- lint_tb：S4 不能被字符串里的 "error(" 绕过 ----
    bypass = ('module tb;\ninitial begin\n'
              '$display("no error(s)");\n$finish;\nend\nendmodule\n')
    assert "S4" in _lint(bypass, ["silent_pass"]), "string literal bypassed S4"
    judged = ('module tb;\ninitial begin\n'
              'if (1) $display("x"); else $error("bad");\n$finish;\nend\nendmodule\n')
    assert "S4" not in _lint(judged, ["silent_pass"]), "real judge wrongly flagged S4"


def main() -> int:
    test_sandbox_clear_dir_marked_nested()
    test_copy_sandbox_failure_leaves_no_partial_output()
    test_resolve_deps_path_prefixed_include()
    test_resolve_deps_transitive_headers()
    test_included_source_not_treated_as_compilation_unit()
    test_rtl_files_keeps_independently_compilable_modules()
    test_package_imports_are_part_of_the_dependency_closure()
    test_staged_rtl_files_reach_the_parser()
    test_tb_builds_for_rtl_without_timescale()
    test_duplicate_module_definitions_are_reported()
    test_review_fixes_index_repo_and_linter_routing()
    print("ALL test_repo_and_sandbox_coverage.py TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
