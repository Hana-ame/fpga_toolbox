"""Dependency-free regression suite for fpga_toolbox.

Run from the repo root:
    python -m tests.test_regression        (or)  python tests/test_regression.py

Covers the failure modes found during the first audit:
  * copy_sandbox wiping the project when sandbox_dir == project_root
  * copy_sandbox flattening same-basename files
  * copy_sandbox cleaning an unrelated non-empty directory
  * aresetn / s_axi_aresetn not recognised as reset -> duplicated port connection
  * normalize_sim_result silently returning NO_RESULTS on unreadable input
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpga_toolbox import call_tool  # noqa: E402

RTL = """`include "defs.svh"
module reg_slave #(parameter int ADDR_W = 12, DATA_W = 32) (
    input  logic              {clk},
    input  logic              {rst},
    input  logic [ADDR_W-1:0] s_axi_awaddr,
    input  logic [2:0]        s_axi_awprot,
    input  logic              s_axi_awvalid,
    output logic              s_axi_awready,
    input  logic [DATA_W-1:0] s_axi_wdata,
    input  logic [3:0]        s_axi_wstrb,
    input  logic              s_axi_wvalid,
    output logic              s_axi_wready,
    output logic [1:0]        s_axi_bresp,
    output logic              s_axi_bvalid,
    input  logic              s_axi_bready,
    input  logic [ADDR_W-1:0] s_axi_araddr,
    input  logic [2:0]        s_axi_arprot,
    input  logic              s_axi_arvalid,
    output logic              s_axi_arready,
    output logic [DATA_W-1:0] s_axi_rdata,
    output logic [1:0]        s_axi_rresp,
    output logic              s_axi_rvalid,
    input  logic              s_axi_rready,
    output logic              irq_o
);
    assign s_axi_awready = 1'b0;
    assign s_axi_wready  = 1'b0;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_bvalid  = 1'b0;
    assign s_axi_arready = 1'b0;
    assign s_axi_rdata   = '0;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rvalid  = 1'b0;
    assign irq_o         = 1'b0;
endmodule
"""

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


def make_project(root: Path, rst: str = "aresetn", clk: str = "aclk") -> Path:
    """Create a small AXI4-Lite project and return its root directory."""
    (root / "rtl").mkdir(parents=True, exist_ok=True)
    (root / "inc").mkdir(parents=True, exist_ok=True)
    (root / "rtl" / "reg_slave.sv").write_text(RTL.format(clk=clk, rst=rst), encoding="utf-8")
    (root / "inc" / "defs.svh").write_text("`define FOO 1\n", encoding="utf-8")
    return root


def test_sandbox_guards(tmp: Path) -> None:
    print("copy_sandbox: sandbox_dir must never be the project root")
    proj = make_project(tmp / "guard")
    before = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*") if p.is_file())
    res = call_tool("copy_sandbox", {
        "project_root": str(proj),
        "relative_files": ["rtl/reg_slave.sv"],
        "sandbox_dir": str(proj),
        "clean": True,
    })
    after = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*") if p.is_file())
    check("rejected", res["ok"] is False, res.get("error") or "")
    check("project intact", before == after, f"{before} != {after}")

    print("copy_sandbox: ancestor of the project root is rejected")
    res = call_tool("copy_sandbox", {
        "project_root": str(proj),
        "relative_files": ["rtl/reg_slave.sv"],
        "sandbox_dir": str(tmp),
        "clean": True,
    })
    check("ancestor rejected", res["ok"] is False, res.get("error") or "")
    check("ancestor kept files", (proj / "rtl" / "reg_slave.sv").is_file())


def test_sandbox_layout(tmp: Path) -> None:
    print("copy_sandbox: same basename in different dirs must not collide")
    proj = tmp / "layout"
    (proj / "rtl" / "a").mkdir(parents=True)
    (proj / "rtl" / "b").mkdir(parents=True)
    (proj / "rtl" / "a" / "common.sv").write_text("module A; endmodule\n", encoding="utf-8")
    (proj / "rtl" / "b" / "common.sv").write_text("module B; endmodule\n", encoding="utf-8")
    sb = tmp / "layout_sb"
    res = call_tool("copy_sandbox", {
        "project_root": str(proj),
        "relative_files": ["rtl/a/common.sv", "rtl/b/common.sv"],
        "sandbox_dir": str(sb),
    })
    files = sorted(p.relative_to(sb).as_posix() for p in sb.rglob("*.sv"))
    check("tool ok", res["ok"] is True, res.get("error") or "")
    check("both files staged", files == ["rtl/a/common.sv", "rtl/b/common.sv"], str(files))
    check("contents distinct",
          "module A" in (sb / "rtl/a/common.sv").read_text(encoding="utf-8")
          and "module B" in (sb / "rtl/b/common.sv").read_text(encoding="utf-8"))

    print("copy_sandbox: non-empty unmarked directory is not wiped")
    foreign = tmp / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("important\n", encoding="utf-8")
    res = call_tool("copy_sandbox", {
        "project_root": str(proj),
        "relative_files": ["rtl/a/common.sv"],
        "sandbox_dir": str(foreign),
        "clean": True,
    })
    check("refused unmarked clean", res["ok"] is False, res.get("error") or "")
    check("foreign file survived", (foreign / "keep.txt").is_file())


def test_reset_detection(tmp: Path) -> None:
    for rst_name in ("aresetn", "resetn", "rst_n", "s_axi_aresetn", "nrst", "reset"):
        print(f"extract_interfaces: reset '{rst_name}' recognised")
        proj = make_project(tmp / f"rst_{rst_name}", rst=rst_name)
        rtl = proj / "rtl" / "reg_slave.sv"
        res = call_tool("extract_interfaces", {"rtl_file": str(rtl), "module_name": "reg_slave"})
        mod = res["data"]["modules"][0]
        check(f"{rst_name} detected", (mod["reset"] or {}).get("port") == rst_name, json.dumps(mod["reset"]))
        tie_offs = [p["port"] for p in mod["out_of_scope_ports"] if p.get("tie_off")]
        check(f"{rst_name} not tied off", rst_name not in tie_offs, str(tie_offs))
        active_low = bool((mod["reset"] or {}).get("active_low"))
        expected_low = rst_name not in ("reset", "rst")
        check(f"{rst_name} polarity", active_low is expected_low, f"active_low={active_low}")

        print(f"emit_testbench: no duplicate '.{rst_name}' connection")
        draft = tmp / f"rst_{rst_name}" / "contract.yaml"
        call_tool("extract_interfaces", {
            "rtl_file": str(rtl), "module_name": "reg_slave", "emit_draft_yaml": str(draft),
        })
        out = tmp / f"rst_{rst_name}" / "tb"
        res = call_tool("emit_testbench", {
            "spec_path": str(draft), "out_dir": str(out), "rtl_file": str(rtl),
        })
        check(f"{rst_name} emitter ok", res["ok"] is True, res.get("error") or str(res.get("diagnostics")))
        if res["ok"]:
            wrap = Path(res["data"]["artifacts"]["unit_isolation_wrapper"]).read_text(encoding="utf-8")
            conns = [ln for ln in wrap.splitlines() if ln.strip().startswith(f".{rst_name}(")]
            check(f"{rst_name} connected once", len(conns) == 1, str(conns))
            py = Path(res["data"]["artifacts"]["cocotb_testbench"]).read_text(encoding="utf-8")
            m = re.search(r"reset_active_level=(\w+)", py)
            check(f"{rst_name} reset_active_level", bool(m) and m.group(1) == str(not expected_low),
                  m.group(1) if m else "missing")


def test_pure_apb_draft(tmp: Path) -> None:
    """extract_interfaces --emit-draft-yaml 对纯 APB 设备不得写空 axi4_lite 段。

    回归 33：空 axi4_lite.port_map 会被 validator 当成「存在 AXI 边界」，
    导致合法的纯 APB 契约报 17 条 E001，自家 draft 过不了自家校验。
    """
    proj = tmp / "pure_apb"
    rtl = proj / "pure_apb.sv"
    rtl.parent.mkdir(parents=True, exist_ok=True)
    rtl.write_text(
        "module pure_apb(input logic clk, input logic rst,\n"
        "  input logic psel, input logic penable, input logic pwrite,\n"
        "  input logic [7:0] paddr, input logic [31:0] pwdata,\n"
        "  output logic pready, output logic [31:0] prdata, output logic pslverr);\n"
        "  assign pready = 1'b1; assign prdata = 32'h0; assign pslverr = 1'b0;\n"
        "endmodule\n",
        encoding="utf-8",
    )
    draft = proj / "contract.yaml"
    ex = call_tool("extract_interfaces", {
        "rtl_file": str(rtl), "module_name": "pure_apb", "emit_draft_yaml": str(draft),
    })
    check("pure-apb extract ok", ex["ok"], ex.get("error") or "")
    text = draft.read_text(encoding="utf-8")
    check("pure-apb draft has apb", "apb:" in text, text[:200])
    check("pure-apb draft omits axi4_lite", "axi4_lite:" not in text,
          "draft must not emit an empty axi4_lite block for APB-only RTL")
    val = call_tool("validate_contract", {"spec_path": str(draft), "rtl_file": str(rtl)})
    errs = [d for d in (val.get("diagnostics") or []) if d.get("severity") == "ERROR"]
    check("pure-apb draft validates", val["ok"] and not errs,
          f"ok={val['ok']} errors={[d.get('code') for d in errs]}")


def test_sandbox_symlink_escape(tmp: Path) -> None:
    """copy_sandbox 不得跟随符号链接把项目根之外的文件拷进沙箱。

    回归 34：旧实现只校验相对路径字符串不含 ".."，但项目内的符号链接
    （文件或目录）会让 is_file()/shutil.copy2 跟随到项目外，把任意文件
    内容「搬」进沙箱并算进 sha256——声称隔离却泄漏。
    """
    proj = tmp / "symproj"
    outside = tmp / "symoutside"
    (proj / "rtl").mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)
    (proj / "rtl" / "ok.sv").write_text("module ok; endmodule\n", encoding="utf-8")
    (outside / "secret.txt").write_text("TOPSECRET_XYZ\n", encoding="utf-8")
    (proj / "rtl" / "leak_file.sv").symlink_to(outside / "secret.txt")
    (proj / "rtl" / "leakdir").symlink_to(outside, target_is_directory=True)

    for label, rel in (("file", "rtl/leak_file.sv"), ("dir", "rtl/leakdir/secret.txt")):
        sb = tmp / f"sb_{label}"
        r = call_tool("copy_sandbox", {
            "project_root": str(proj), "relative_files": [rel], "sandbox_dir": str(sb),
        })
        check(f"symlink {label} escape rejected", r["ok"] is False, str(r.get("error"))[:120])
        leaked = any(
            p.is_file() and "TOPSECRET_XYZ" in p.read_text(encoding="utf-8", errors="ignore")
            for p in sb.rglob("*")
        ) if sb.exists() else False
        check(f"symlink {label} no leak", not leaked, "secret content landed in sandbox")

    # 项目内部的相对符号链接解析后仍在 root 内，必须继续可用。
    inner = tmp / "syminner"
    (inner / "x").mkdir(parents=True, exist_ok=True)
    (inner / "y").mkdir(parents=True, exist_ok=True)
    (inner / "y" / "real.sv").write_text("module inner_real; endmodule\n", encoding="utf-8")
    (inner / "x" / "alias.sv").symlink_to(inner / "y" / "real.sv")
    r = call_tool("copy_sandbox", {
        "project_root": str(inner), "relative_files": ["x/alias.sv"], "sandbox_dir": str(tmp / "sb_inner"),
    })
    check("internal symlink still allowed", r["ok"] is True, str(r.get("error"))[:120])


def test_unknown_instantiations_reported(tmp: Path) -> None:
    """index_repo 必须上报「例化了但仓库里没有定义」的模块（W102）。

    回归 35：token 启发式只收 `value in known` 的名字，例化仓库外模块
    （厂商 IP / 加密 IP / 未克隆子模块）被静默丢弃，resolve_deps 闭包看起来
    完整却漏依赖、沙箱静默缺文件。放宽启发式又会让 `function int foo(`
    之类全误报（实测 106 条噪声），故用 slang CST 的显式例化节点精确判定。
    """
    proj = tmp / "unkproj"
    (proj / "rtl").mkdir(parents=True, exist_ok=True)
    (proj / "rtl" / "top.sv").write_text(
        "module top(input logic clk);\n"
        "  leaf u_leaf(.clk(clk));\n"
        "  vendor_ip u_vip(.clk(clk));\n"
        "endmodule\n", encoding="utf-8")
    (proj / "rtl" / "leaf.sv").write_text(
        "module leaf(input logic clk); endmodule\n", encoding="utf-8")

    res = call_tool("index_repo", {"project_root": str(proj)})
    check("unknown-inst index ok", res["ok"], res.get("error") or "")
    top = res["data"]["modules"]["top"]
    check("known instantiation still recorded", top["instantiated_modules"] == ["leaf"],
          str(top["instantiated_modules"]))

    # 未知例化检测依赖 slang CST（token 启发式无法区分 `vendor_ip u(` 与
    # `function int foo(`，只会产生海量误报）。CI 不装 slang，此时该特性按
    # 设计静默降级为「不报未知」，不能把环境缺失当成代码缺陷——显式跳过。
    import shutil as _shutil
    if not _shutil.which("slang"):
        print("  skip unknown-instantiation assertions (slang not installed; "
              "W102 requires the slang CST backend)")
        return

    check("unknown instantiation reported", top.get("unresolved_instantiations") == ["vendor_ip"],
          str(top.get("unresolved_instantiations")))
    w102 = [d for d in (res.get("diagnostics") or []) if d.get("code") == "W102"]
    check("W102 emitted for unknown", len(w102) == 1, str(w102))

    # 真·叶子模块不应被误报
    leaf = res["data"]["modules"]["leaf"]
    check("leaf has no unresolved", leaf.get("unresolved_instantiations") == [],
          str(leaf.get("unresolved_instantiations")))

    # 下游 resolve_deps 必须透传：否则调用方拿「看起来完整」的文件清单去
    # copy_sandbox，沙箱静默缺文件。
    dep = call_tool("resolve_deps", {"target_module": "top", "repo_index": res["data"]})
    check("resolve_deps ok", dep["ok"], dep.get("error") or "")
    check("closure flagged incomplete", dep["data"].get("closure_is_complete") is False,
          str(dep["data"].get("closure_is_complete")))
    check("resolve_deps carries unresolved",
          dep["data"].get("unresolved_instantiations") == {"top": ["vendor_ip"]},
          str(dep["data"].get("unresolved_instantiations")))
    dep_w = [d for d in (dep.get("diagnostics") or []) if d.get("code") == "W102"]
    check("resolve_deps emits W102", len(dep_w) == 1, str(dep_w))

    # 函数返回类型/interface 声明不得被误报成例化（曾产生 106 条噪声）
    proj2 = tmp / "fpproj"
    (proj2 / "rtl").mkdir(parents=True, exist_ok=True)
    (proj2 / "rtl" / "f.sv").write_text(
        "interface my_if(input logic clk); endinterface\n"
        "module f(input logic clk);\n"
        "  my_if u_if(.clk(clk));\n"
        "  function automatic int calc(input int x); return x; endfunction\n"
        "endmodule\n", encoding="utf-8")
    res2 = call_tool("index_repo", {"project_root": str(proj2)})
    w102b = [d for d in (res2.get("diagnostics") or []) if d.get("code") == "W102"]
    check("no false-positive W102 for func/interface", len(w102b) == 0,
          str([d.get("message") for d in w102b]))


def test_subprocess_encoding_robust(tmp: Path) -> None:
    """外部工具输出含非法 UTF-8 时，解析器不得因解码失败整段崩掉。

    回归 36（Windows CI 实测）：subprocess.run(text=True) 在 Windows 用
    locale 编码（cp1252）解码，slang 诊断含非 ASCII 字节时抛
    UnicodeDecodeError，随后 proc.stderr 为 None 触发 AttributeError，
    整个 AST 解析被打断。sv_parser / sim_runner 已改为显式
    encoding="utf-8", errors="replace"。
    """
    import subprocess
    import sys as _sys

    code = "import sys; sys.stdout.buffer.write(b'\\xff\\xfe bad \\x8f\\n'); sys.stdout.flush()"

    # 只断言「修复后的写法一定能拿到文本」——这是 sv_parser/sim_runner 依赖的
    # 语义。不断言「不显式编码必定抛异常」：Windows 上解码发生在 subprocess
    # 的 reader 线程里，异常不传播到主线程（CI 实测该断言在 Windows 失败），
    # 跨平台行为不一致，断言它本身就是脆的。
    proc = subprocess.run(
        [_sys.executable, "-c", code], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    check("explicit utf-8 + replace returns text", isinstance(proc.stdout, str),
          type(proc.stdout).__name__)
    check("replacement chars present", "\ufffd" in (proc.stdout or ""), repr(proc.stdout))

    # sv_parser 里两个 slang 调用点都必须带显式编码
    import inspect

    import fpga_toolbox.core.sv_parser as svp
    src_sv = inspect.getsource(svp)
    check("sv_parser _run_slang sets encoding",
          src_sv.count('encoding="utf-8", errors="replace"') >= 2,
          f"found {src_sv.count(chr(101)+chr(110)+chr(99)+chr(111)+chr(100)+chr(105)+chr(110)+chr(103))} encoding occurrences")


def test_normalizer(tmp: Path) -> None:
    print("normalize_sim_result: unrecognized input is an error, not an empty report")
    junk = tmp / "junk.json"
    junk.write_text('{"unrelated": 1}', encoding="utf-8")
    res = call_tool("normalize_sim_result", {"module_name": "m", "cocotb_results_json": str(junk)})
    check("junk rejected", res["ok"] is False, res.get("data"))

    print("normalize_sim_result: missing file is an error")
    res = call_tool("normalize_sim_result", {"module_name": "m", "cocotb_results_json": str(tmp / "nope.json")})
    check("missing rejected", res["ok"] is False)

    print("normalize_sim_result: cocotb JUnit XML is parsed")
    xml = tmp / "results.xml"
    xml.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="reg_slave">'
        '<testcase name="axil_write_read" classname="axil"/>'
        '<testcase name="axil_illegal_addr" classname="axil"><failure message="got OKAY">trace</failure></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    res = call_tool("normalize_sim_result", {"module_name": "reg_slave", "cocotb_results_json": str(xml)})
    data = res["data"]
    check("xml parsed", res["ok"] is True and data["summary"]["total"] == 2, json.dumps(data.get("summary")))
    check("xml verdict", data.get("overall_status") == "FAIL", data.get("overall_status"))


def test_full_chain(tmp: Path) -> None:
    print("full chain: index -> deps -> sandbox -> contract -> tb -> report -> visual")
    proj = make_project(tmp / "chain")
    rtl = proj / "rtl" / "reg_slave.sv"

    idx = call_tool("index_repo", {"project_root": str(proj)})
    check("index ok", idx["ok"] and idx["data"]["modules"], idx.get("error") or "")

    deps = call_tool("resolve_deps", {"target_module": "reg_slave", "repo_index": idx["data"]})
    check("deps ok", deps["ok"] and deps["data"]["dependency_closure"] == ["reg_slave"], deps.get("error") or "")

    sb = tmp / "chain_sb"
    cp = call_tool("copy_sandbox", {
        "project_root": str(proj),
        "relative_files": deps["data"]["all_files"],
        "sandbox_dir": str(sb),
    })
    check("sandbox ok", cp["ok"], cp.get("error") or "")

    draft = tmp / "chain" / "contract.yaml"
    ex = call_tool("extract_interfaces", {
        "rtl_file": str(rtl), "module_name": "reg_slave", "emit_draft_yaml": str(draft),
        "staged_rtl_files": ["rtl/reg_slave.sv", "inc/defs.svh"],
    })
    check("extract ok", ex["ok"], ex.get("error") or "")

    val = call_tool("validate_contract", {"spec_path": str(draft), "rtl_file": str(rtl)})
    check("validate ok", val["ok"], str(val.get("diagnostics")))

    tb = call_tool("emit_testbench", {"spec_path": str(draft), "out_dir": str(tmp / "chain" / "tb"), "rtl_file": str(rtl)})
    check("emit ok", tb["ok"], str(tb.get("diagnostics")))

    if tb["ok"]:
        wrap = Path(tb["data"]["artifacts"]["unit_isolation_wrapper"]).read_text(encoding="utf-8")
        dup = [ln for ln in wrap.splitlines() if ln.strip().startswith(".aresetn(")]
        check("wrapper has single aresetn", len(dup) == 1, str(dup))
        import py_compile
        py_compile.compile(tb["data"]["artifacts"]["cocotb_testbench"], doraise=True)
        check("cocotb testbench compiles", True)

    norm = call_tool("normalize_sim_result", {"module_name": "reg_slave", "cocotb_results_json": str(tmp / "results.xml")})
    check("normalize ok", norm["ok"], norm.get("error") or "")

    vis = call_tool("render_visual_report", {
        "report": norm["data"] if norm["ok"] else {"module": "reg_slave", "overall_status": "NO_RESULTS"},
        "dep_graph": deps["data"] if deps["ok"] else {"dependency_closure": []},
        "extracted_interfaces": ex["data"]["modules"][0]["interfaces"] if ex["ok"] else [],
        "out_html_path": str(tmp / "chain" / "visual.html"),
    })
    check("visual ok", vis["ok"], vis.get("error") or "")
    if vis["ok"]:
        svg = Path(vis["data"]["svg_path"])
        check("svg sidecar written", svg.is_file() and "<svg" in svg.read_text(encoding="utf-8"))
        check("html written", Path(vis["data"]["html_path"]).is_file())


def test_cli(tmp: Path) -> None:
    """The CLI surface is the part users actually touch, so drive it as a subprocess."""
    import subprocess

    root = Path(__file__).resolve().parents[1]
    proj = make_project(tmp / "cli")
    rtl = proj / "rtl" / "reg_slave.sv"

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    print("cli: list / schemas")
    res = run("-m", "fpga_toolbox", "list")
    check("python -m fpga_toolbox list", res.returncode == 0 and "index_repo" in res.stdout, res.stderr[-300:])
    res = run("-m", "fpga_toolbox", "schemas")
    check("python -m fpga_toolbox schemas", res.returncode == 0 and '"inputSchema"' in res.stdout, res.stderr[-300:])

    print("cli: native flags for a tool")
    res = run("-m", "fpga_toolbox", "index_repo", "--project-root", str(proj))
    check("native --project-root", res.returncode == 0 and '"reg_slave"' in res.stdout, res.stdout[-300:] + res.stderr[-300:])

    print("cli: single-file and module execution")
    res = run(str(root / "fpga_toolbox" / "iface_extractor.py"),
              "--rtl-file", str(rtl), "--module-name", "reg_slave")
    check("python fpga_toolbox/iface_extractor.py",
          res.returncode == 0 and '"extract_interfaces"' in res.stdout, res.stderr[-400:])
    res = run("-m", "fpga_toolbox.repo_indexer", "--project-root", str(proj))
    check("python -m fpga_toolbox.repo_indexer",
          res.returncode == 0 and '"index_repo"' in res.stdout, res.stderr[-400:])
    check("no runpy double-import warning", "RuntimeWarning" not in res.stderr, res.stderr[-300:])

    print("cli: @file.json for object parameters")
    report = tmp / "cli" / "report.json"
    report.write_text(json.dumps({
        "module": "reg_slave", "overall_status": "PASS",
        "summary": {"total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0},
        "checks": [{"rule_id": "R1", "category": "c", "status": "PASS", "detail": "d"}],
    }), encoding="utf-8")
    deps = tmp / "cli" / "deps.json"
    deps.write_text(json.dumps({"dependency_closure": ["reg_slave"]}), encoding="utf-8")
    ifaces = tmp / "cli" / "ifaces.json"
    ifaces.write_text(json.dumps([{"protocol": "axi4_lite", "port_map": {"awaddr": "s_axi_awaddr"}}]), encoding="utf-8")
    out = tmp / "cli" / "visual.html"
    res = run("-m", "fpga_toolbox", "render_visual_report",
              "--report", f"@{report}", "--dep-graph", f"@{deps}",
              "--extracted-interfaces", f"@{ifaces}", "--out-html-path", str(out))
    check("@file.json objects", res.returncode == 0 and out.is_file(), res.stdout[-300:] + res.stderr[-300:])

    # —— 缺陷 86：@file.json 非 UTF-8 → 结构化错误 + 退出码 1，不冒泡堆栈 ——
    # 实测：用户拿到别的工具导出的 GBK/latin-1 JSON，@file 读取时
    # UnicodeDecodeError 带完整 Python 堆栈直接冒泡，没有一句指向编码。
    bad_json = tmp / "cli" / "bad.json"
    bad_json.write_bytes(b'{\n  "k": "v\xe9"\n}')
    res = run("-m", "fpga_toolbox", "render_visual_report",
              "--report", f"@{bad_json}", "--dep-graph", f"@{deps}",
              "--extracted-interfaces", f"@{ifaces}", "--out-html-path", str(out))
    check("@file non-UTF-8 exits 1 (not a traceback)", res.returncode == 1,
          f"rc={res.returncode}")
    check("@file non-UTF-8 error points at encoding",
          "not valid UTF-8" in res.stdout and "re-export it as UTF-8" in res.stdout,
          res.stdout[-220:] + res.stderr[-220:])
    check("@file non-UTF-8 error is structured (no traceback)",
          '"error"' in res.stdout and "Traceback" not in res.stdout
          and "UnicodeDecodeError" not in res.stdout, res.stdout[-200:])

    print("cli: boolean flags and failure exit code")
    sb = tmp / "cli_sb"
    res = run("-m", "fpga_toolbox", "copy_sandbox", "--project-root", str(proj),
              "--relative-files", "rtl/reg_slave.sv", "--sandbox-dir", str(sb), "--no-clean")
    check("--no-clean accepted", res.returncode == 0, res.stderr[-300:])
    res = run("-m", "fpga_toolbox", "index_repo", "--project-root", str(tmp / "missing"))
    check("failure exits 1", res.returncode == 1, str(res.returncode))


def test_call_tool_enforces_required(tmp: Path) -> None:
    """call_tool 必须执行 schema 的 required 校验。

    回归 61/62：旧实现只校验「未知参数名」，不校验 required。于是
    `emit_testbench` 的 `required: ["out_dir"]` 形同虚设——不传 out_dir 时
    Path("") 解析成当前工作目录，4 个产物直接写进调用方 cwd 并返回 ok=True
    （实测在 /tmp 的空目录里凭空出现 dev_axil_wrap.sv 等）。
    """
    # 缺失 required 必须被入口拒绝（不再落到函数体里写 cwd）
    r = call_tool("index_repo", {})
    check("missing required rejected", r["ok"] is False, str(r.get("error"))[:80])
    check("error names the missing param", "project_root" in str(r.get("error")), str(r.get("error"))[:80])

    r2 = call_tool("emit_testbench", {"rtl_file": "x.sv", "protocol": "apb"})
    check("emit_testbench without out_dir rejected", r2["ok"] is False, str(r2.get("error"))[:80])
    check("out_dir named in error", "out_dir" in str(r2.get("error")), str(r2.get("error"))[:80])

    # required 为空列表的工具不受影响
    r3 = call_tool("audit_harness", {})
    check("empty-required tool still callable", r3["ok"] is True, str(r3.get("error"))[:80])

    # 未知参数名仍然单独报错（不与 required 混淆）
    r4 = call_tool("index_repo", {"project_root": ".", "bogus_param": 1})
    check("unknown param still rejected", r4["ok"] is False, str(r4.get("error"))[:80])
    check("unknown param message distinct", "Unknown parameter" in str(r4.get("error")),
          str(r4.get("error"))[:80])
def test_call_tool_enum_and_type_validation(tmp: Path) -> None:
    """call_tool 必须强制 schema 的 enum 与 type，且不破坏数字字符串兼容。

    回归 63：非法 enum 值 `protocol='BOGUS'` 与「合法但 RTL 不匹配」的
    `protocol='ddr3'` 报错文本完全相同（都是 "Protocol 'X' not found in any
    module"），调用方无法区分「参数拼错」和「RTL 没有该协议」。

    回归 64：给 string 参数传 int 时漏出 Python 内部错误
    "argument should be a str or an os.PathLike object where __fspath__ ..."，
    用户看不出是哪个参数错了。

    兼容性约束：数字类参数必须继续接受字符串（MCP 客户端常把 JSON number
    序列化成 str，且 sim_runner/mutation_runner 在函数边界故意做了 int()
    宽容转换）。
    """
    # enum：非法值必须在入口被拒，且错误信息与 RTL 层面错误可区分
    r_enum = call_tool("emit_testbench", {
        "rtl_file": str(Path(__file__).resolve().parents[1] / "examples/multi_bus_ref/apb_slave.sv"),
        "out_dir": str(tmp / "tb_enum"),
        "protocol": "BOGUS",
    })
    check("invalid enum rejected", r_enum["ok"] is False, str(r_enum.get("error"))[:80])
    check("enum error mentions allowed values", "Allowed values" in str(r_enum.get("error")),
          str(r_enum.get("error"))[:100])

    # type：错类型必须在入口被拒，且错误信息含参数名与期望类型
    r_type = call_tool("index_repo", {"project_root": 123})
    check("wrong type rejected", r_type["ok"] is False, str(r_type.get("error"))[:80])
    check("type error names param and type",
          "project_root" in str(r_type.get("error")) and "string" in str(r_type.get("error")),
          str(r_type.get("error"))[:100])

    # 数字字符串兼容：不应被类型校验挡住（下游 int() 转换负责）
    r_num = call_tool("run_sim", {"sim_script": str(tmp / "nope.sh"), "timeout_sec": "1700"})
    check("numeric string still accepted",
          "Invalid type" not in str(r_num.get("error")), str(r_num.get("error"))[:90])
    check("numeric string reaches script check",
          "not found" in str(r_num.get("error")).lower(), str(r_num.get("error"))[:90])

    # 非数字字符串仍应被拒
    r_bad_num = call_tool("run_sim", {"sim_script": str(tmp / "x.sh"), "timeout_sec": "abc"})
    check("non-numeric string rejected downstream",
          r_bad_num["ok"] is False and "integer" in str(r_bad_num.get("error")).lower(),
          str(r_bad_num.get("error"))[:90])
def test_sva_heartbeat_distinguishes_pass_from_notrun(tmp: Path) -> None:
    """SVA 日志的「全部通过」与「checker 根本没跑」必须可区分。

    回归 65：生成的 SVA checker 只在失败时 `$error("[RULE:P_xxx] ...")`，
    通过时零输出。于是 normalize_sim_result 收到「零 SVA 行」时，
    「全通过」与「bind 失败/没跑」都归一化成 NO_RESULTS + 0 checks——
    反静默通过护栏在这里失效。

    修复：checker 周期性命中 `[SVA:HBEAT]`，归一化器据此判定
    「引擎在场且无违例」= PASS；无心跳无违例仍是 NO_RESULTS。
    """
    def norm(log_text: str) -> dict:
        f = tmp / "sva.log"
        f.write_text(log_text, encoding="utf-8")
        return call_tool("normalize_sim_result", {"module_name": "m", "sva_log_path": str(f)})

    # 心跳存在、无违例 => PASS（checker 在场）
    r_pass = norm(
        "[SVA:HBEAT] rule=P_000 monitored_cycles=100 status=ACTIVE\n"
        "[SVA:HBEAT] rule=P_000 monitored_cycles=200 status=ACTIVE\n"
    )
    check("heartbeat+no-violation => PASS",
          r_pass["data"]["overall_status"] == "PASS", str(r_pass["data"]["overall_status"]))
    check("synthetic SVA_P000 check emitted",
          any(c["rule_id"] == "SVA_P000" and c["status"] == "PASS" for c in r_pass["data"]["checks"]),
          str(r_pass["data"]["checks"]))

    # 空文件（checker 没跑）=> NO_RESULTS，不能误判为 PASS
    r_empty = norm("")
    check("empty log stays NO_RESULTS",
          r_empty["data"]["overall_status"] == "NO_RESULTS", str(r_empty["data"]["overall_status"]))
    check("empty log has no SVA_P000",
          not any(c["rule_id"] == "SVA_P000" for c in r_empty["data"]["checks"]),
          str(r_empty["data"]["checks"]))

    # 心跳 + 违例 => FAIL，且不应再合成 PASS
    r_fail = norm("[SVA:HBEAT] rule=P_000 status=ACTIVE\n[RULE:P_001] AWVALID dropped\n")
    check("heartbeat+violation => FAIL",
          r_fail["data"]["overall_status"] == "FAIL", str(r_fail["data"]["overall_status"]))
    check("violation reported",
          any(c["rule_id"] == "P_001" and c["status"] == "FAIL" for c in r_fail["data"]["checks"]),
          str(r_fail["data"]["checks"]))


def test_generated_sva_checker_has_heartbeat(tmp: Path) -> None:
    """生成的 SVA checker 必须包含心跳探针，且能被 Verilator 干净编译。"""
    rtl = tmp / "sva_hb_dut.sv"
    rtl.write_text(
        "`timescale 1ns/1ps\n"
        "module sva_hb_dut(input logic aclk, input logic aresetn,\n"
        "  input logic [11:0] s_axi_awaddr, input logic [2:0] s_axi_awprot, input logic s_axi_awvalid, output logic s_axi_awready,\n"
        "  input logic [31:0] s_axi_wdata, input logic [3:0] s_axi_wstrb, input logic s_axi_wvalid, output logic s_axi_wready,\n"
        "  output logic [1:0] s_axi_bresp, output logic s_axi_bvalid, input logic s_axi_bready,\n"
        "  input logic [11:0] s_axi_araddr, input logic [2:0] s_axi_arprot, input logic s_axi_arvalid, output logic s_axi_arready,\n"
        "  output logic [31:0] s_axi_rdata, output logic [1:0] s_axi_rresp, output logic s_axi_rvalid, input logic s_axi_rready);\n"
        "  assign s_axi_awready=1; assign s_axi_wready=1; assign s_axi_bresp=0; assign s_axi_bvalid=1;\n"
        "  assign s_axi_arready=1; assign s_axi_rdata=0; assign s_axi_rresp=0; assign s_axi_rvalid=1;\n"
        "endmodule\n",
        encoding="utf-8",
    )
    draft = tmp / "sva_hb.yaml"
    ex = call_tool("extract_interfaces", {"rtl_file": str(rtl), "module_name": "sva_hb_dut", "emit_draft_yaml": str(draft)})
    check("sva-hb extract ok", ex["ok"], str(ex.get("error"))[:80])
    em = call_tool("emit_testbench", {"spec_path": str(draft), "out_dir": str(tmp / "sva_hb_tb"), "rtl_file": str(rtl)})
    check("sva-hb emit ok", em["ok"], str(em.get("error"))[:80])
    if em["ok"]:
        sva = Path(em["data"]["artifacts"]["sva_protocol_checker"]).read_text(encoding="utf-8")
        check("checker emits [SVA:HBEAT]", "[SVA:HBEAT]" in sva, "heartbeat missing")
        check("heartbeat counter reset (no PROCASSINIT)",
              "sva_hb_cnt <= 0" in sva and "int unsigned sva_hb_cnt = 0" not in sva,
              "counter init style")
def test_cocotb_incomplete_report_is_failure(tmp: Path) -> None:
    """cocotb 报告 completed=false 时必须判 FAIL，不能当「部分通过」。

    回归 66：cocotb 平台每记录一条 check 就落盘一次，却没有任何「已跑完」信号。
    若测试中途崩溃（异常/超时/await 卡死），留下的 JSON 只含前面几条 PASS，
    旧实现把这份**残缺**结果判成 PASS——与 SVA 的零输出问题同类。
    """
    def norm(payload: dict, name: str) -> dict:
        f = tmp / name
        f.write_text(json.dumps(payload), encoding="utf-8")
        return call_tool("normalize_sim_result", {"module_name": "m", "cocotb_results_json": str(f)})

    base_checks = [{"rule_id": "REG_001", "category": "reset_value", "status": "PASS", "detail": "ok"}]

    # 跑完：completed=true -> PASS
    r_ok = norm({"module": "m", "seed": 1, "completed": True, "checks": base_checks}, "ok.json")
    check("completed=true => PASS", r_ok["data"]["overall_status"] == "PASS",
          str(r_ok["data"]["overall_status"]))

    # 崩溃：completed=false -> FAIL + COCOTB_INCOMPLETE
    r_bad = norm({"module": "m", "seed": 1, "completed": False, "checks": base_checks}, "bad.json")
    check("completed=false => FAIL", r_bad["data"]["overall_status"] == "FAIL",
          str(r_bad["data"]["overall_status"]))
    check("COCOTB_INCOMPLETE emitted",
          any(c["rule_id"] == "COCOTB_INCOMPLETE" and c["status"] == "FAIL"
              for c in r_bad["data"]["checks"]),
          str(r_bad["data"]["checks"]))

    # 旧格式（无 completed 字段）保持向后兼容：不误判为失败
    r_legacy = norm({"module": "m", "seed": 1, "checks": base_checks}, "legacy.json")
    check("legacy JSON without completed still PASS",
          r_legacy["data"]["overall_status"] == "PASS",
          str(r_legacy["data"]["overall_status"]))


def test_generated_cocotb_tb_writes_completion(tmp: Path) -> None:
    """生成的 cocotb TB 必须写 completed 标记（初值 False、Phase 5 置 True）。"""
    rtl = tmp / "cocotb_c_dut.sv"
    rtl.write_text(
        "`timescale 1ns/1ps\n"
        "module cocotb_c_dut(input logic aclk, input logic aresetn,\n"
        "  input logic [11:0] s_axi_awaddr, input logic [2:0] s_axi_awprot, input logic s_axi_awvalid, output logic s_axi_awready,\n"
        "  input logic [31:0] s_axi_wdata, input logic [3:0] s_axi_wstrb, input logic s_axi_wvalid, output logic s_axi_wready,\n"
        "  output logic [1:0] s_axi_bresp, output logic s_axi_bvalid, input logic s_axi_bready,\n"
        "  input logic [11:0] s_axi_araddr, input logic [2:0] s_axi_arprot, input logic s_axi_arvalid, output logic s_axi_arready,\n"
        "  output logic [31:0] s_axi_rdata, output logic [1:0] s_axi_rresp, output logic s_axi_rvalid, input logic s_axi_rready);\n"
        "  assign s_axi_awready=1; assign s_axi_wready=1; assign s_axi_bresp=0; assign s_axi_bvalid=1;\n"
        "  assign s_axi_arready=1; assign s_axi_rdata=0; assign s_axi_rresp=0; assign s_axi_rvalid=1;\n"
        "endmodule\n",
        encoding="utf-8",
    )
    draft = tmp / "cocotb_c.yaml"
    ex = call_tool("extract_interfaces", {"rtl_file": str(rtl), "module_name": "cocotb_c_dut", "emit_draft_yaml": str(draft)})
    check("cocotb-c extract ok", ex["ok"], str(ex.get("error"))[:80])
    em = call_tool("emit_testbench", {"spec_path": str(draft), "out_dir": str(tmp / "cocotb_c_tb"), "rtl_file": str(rtl)})
    check("cocotb-c emit ok", em["ok"], str(em.get("error"))[:80])
    if em["ok"]:
        py = Path(em["data"]["artifacts"]["cocotb_testbench"]).read_text(encoding="utf-8")
        check("completion starts False", '"completed": False' in py, "init missing")
        check("completion set True at end", 'REPORT["completed"] = True' in py, "finalize missing")
def test_verilator_missing_summary_is_failure(tmp: Path) -> None:
    """Verilator 套件缺 PASS/FAIL 汇总行时必须判 FAIL。

    回归 67：生成的 TB 必定在 $finish 前打印一条 `PASS:`/`FAIL:` 汇总。
    旧实现只看已有的 `[CHECK]` 行，于是「CHECK 之后崩溃」与「完整跑完」
    结果完全相同（都 PASS）。

    另需保证不误伤：汇总行首词不一定是套件名（真实 sim.sh 打印
    `PASS: apb all 8 checks passed`，而某些 TB 打印 `PASS: all 20 checks
    passed`），因此用**计数法**（启动 N 个套件需 ≥N 条汇总）而非逐名匹配。
    """
    def norm(log_text: str, name: str) -> dict:
        f = tmp / name
        f.write_text(log_text, encoding="utf-8")
        return call_tool("normalize_sim_result", {"module_name": "m", "verilator_log_path": str(f)})

    # 完整 2 套件 => PASS
    r_ok = norm(
        "== sim: apb (top=t) ==\nPASS: apb passed\n"
        "== sim: axi (top=t) ==\nPASS: axi passed\n", "vl_ok.log")
    check("complete 2 suites => PASS", r_ok["data"]["overall_status"] == "PASS",
          str(r_ok["data"]["overall_status"]))

    # 第二个套件崩（无汇总）=> FAIL
    r_partial = norm(
        "== sim: apb (top=t) ==\nPASS: apb passed\n"
        "== sim: axi (top=t) ==\n[CHECK] id=A1 | status=PASS\n", "vl_partial.log")
    check("second suite aborted => FAIL", r_partial["data"]["overall_status"] == "FAIL",
          str(r_partial["data"]["overall_status"]))
    check("VERILATOR_NO_SUMMARY emitted",
          any(c["rule_id"] == "VERILATOR_NO_SUMMARY" and c["status"] == "FAIL"
              for c in r_partial["data"]["checks"]),
          str(r_partial["data"]["checks"]))

    # 完全没有汇总行 => FAIL
    r_none = norm("== sim: apb (top=t) ==\n[CHECK] id=A1 | status=PASS\n", "vl_none.log")
    check("no summary at all => FAIL", r_none["data"]["overall_status"] == "FAIL",
          str(r_none["data"]["overall_status"]))

    # 汇总行首词不是套件名：不得误判
    r_word = norm(
        "== sim: counter (top=t) ==\nPASS: all 20 checks passed\n"
        "== sim: axi (top=t) ==\nPASS: all 35 checks passed\n", "vl_word.log")
    check("summary not named after suite still PASS",
          r_word["data"]["overall_status"] == "PASS", str(r_word["data"]["overall_status"]))


def test_sv_silent_pass_detected(tmp: Path) -> None:
    """SV 测试台「只 $display 不判定」必须被 lint_tb 判出（S4）。

    回归 68：`scan_silent_pass` 只被喂 `.sh`/`.py`，SV 文件根本不过这个扫描器。
    于是 README/FEATURES 反复宣传的头号症状——测试台打印几句 `$display`
    就 `$finish`、全程没有任何判定——`lint_tb` 返回 ok=True、零发现。
    这类 TB 的仿真必然「通过」，因为它从未表达过失败。

    同时保证不误报：带 assert / $error / $fatal / 自研 check 任务的正常 TB
    必须保持干净。
    """
    silent = tmp / "tb_silent.sv"
    silent.write_text(
        "module tb_silent;\n"
        "  initial begin\n"
        "    #100;\n"
        "    $display(\"TEST DONE\");\n"
        "    $finish;\n"
        "  end\n"
        "endmodule\n",
        encoding="utf-8",
    )
    r = call_tool("lint_tb", {"target_path": str(silent), "rules": ["silent_pass"]})
    codes = {d["code"] for d in r["diagnostics"]}
    check("silent-pass SV TB fails lint_tb", not r["ok"], str(r["ok"]))
    check("S4 emitted for $display-without-judgement", "S4" in codes, str(sorted(codes)))

    # 反向：真实判定形态都不得误报
    judged = {
        "assert_tb.sv": "module t; initial begin assert (d==8'h12) else $error(\"bad\"); $display(\"PASS\"); end endmodule\n",
        "if_error_tb.sv": "module t; initial begin if (d !== 8'h12) $error(\"mismatch\"); $display(\"PASS\"); end endmodule\n",
        "fatal_tb.sv": "module t; initial begin if (d !== 8'h12) $fatal(1, \"mismatch\"); $display(\"PASS\"); end endmodule\n",
        "check_task_tb.sv": (
            "module t; initial begin check_eq(d, 8'h12); $display(\"PASS\"); end\n"
            "  task check_eq; input [7:0] a, b; begin if (a !== b) $error(\"x\"); end endtask\n"
            "endmodule\n"
        ),
    }
    for name, src in judged.items():
        f = tmp / name
        f.write_text(src, encoding="utf-8")
        rj = call_tool("lint_tb", {"target_path": str(f), "rules": ["silent_pass"]})
        check(f"judged TB not flagged ({name})", rj["ok"],
              f"{name}: {sorted({d['code'] for d in rj['diagnostics']})}")


def test_waveform_and_log_capture(tmp: Path) -> None:
    """波形与日志抓取必须「有产物才算数」。

    回归 73：工具箱原先完全没有波形/日志持久化 —— run_sim 只在返回值里放
    4000/2000 字符的 tail，完整日志被丢弃，波形更没有。

    这里锁死写功能时踩到的 4 个真实缺陷：
      A. 生成的 TB 里 $value$plusargs 目标变量未声明 → Verilator 直接
         "Can't find definition of variable: 'wave_path'" 编译失败。
      B. require_waveform=true + waveform=false：要求「必须有波形」却不开
         --trace，旧实现直接放行返回 ok=True。
      C. 旧波形污染：上一次运行留下的 .vcd 被当成本次证据，于是「这次其实
         没抓到波形」也能通过 require_waveform。
      D. FST 缺 lz4 开发头文件时静默产出「后缀 .fst、内容是 VCD」的假文件；
         现在必须显式报 E025。

    环境依赖说明：`run_sim` 在**做任何事之前**就会检查 Verilator 版本，
    没有 verilator 的环境（如裸 python:3.10 容器）会先返回 E020。
    因此：
      * 缺陷 B（矛盾组合）不依赖 Verilator —— 校验发生在版本检查之前；
      * 需要真正跑一次脚本的断言（C / 正向 / 长日志）必须先探测 verilator，
        不可用时**显式跳过并打印原因**，而不是伪装成通过或直接失败。
    """
    import os
    import stat as _stat

    base = tmp / "wlog"
    base.mkdir(parents=True, exist_ok=True)

    def mk_script(name: str, body: str) -> Path:
        p = base / name
        p.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
        os.chmod(p, os.stat(p).st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)
        return p

    def verilator_usable() -> bool:
        """run_sim 要求 Verilator >= 5.040。没有就跳过需要真跑仿真的断言。"""
        try:
            from fpga_toolbox.eda_env import probe_eda_env
            return bool(probe_eda_env()["verilator"]["meets_min_version"])
        except Exception:  # noqa: BLE001 - 探测失败按不可用处理
            return False

    _HAVE_VLT = verilator_usable()
    _skip_reason = "" if _HAVE_VLT else "no Verilator >= 5.040 in this environment"

    def skip(name: str) -> None:
        print(f"  skip {name}  ({_skip_reason})")

    # —— B: 矛盾组合必须报错（不依赖 Verilator：校验位于版本检查之前）——
    plain = mk_script("run_plain.sh", 'echo "PASS: fake"; exit 0\n')
    r_b = call_tool("run_sim", {
        "sim_script": str(plain), "suite": "apb",
        "waveform": False, "require_waveform": True,
    })
    check("require_waveform without waveform rejected", not r_b["ok"], str(r_b["ok"]))
    check("E024 for contradictory waveform flags",
          "E024" in {d["code"] for d in r_b.get("diagnostics", [])},
          str([d["code"] for d in r_b.get("diagnostics", [])]))

    # —— C: 旧波形不算数（需要真跑脚本 → 依赖 Verilator）——
    if not _HAVE_VLT:
        skip("stale waveform does not satisfy require_waveform")
        skip("E024 when no fresh waveform produced")
    else:
        # 先放一个"上次留下的"波形文件，再跑一个不产生波形的脚本
        stale = base / "wave_stale.vcd"
        stale.write_text("$version stale $end\n", encoding="utf-8")
        r_c = call_tool("run_sim", {
            "sim_script": str(plain), "suite": "apb",
            "waveform": True, "require_waveform": True,
        })
        check("stale waveform does not satisfy require_waveform", not r_c["ok"], str(r_c["ok"]))
        check("E024 when no fresh waveform produced",
              "E024" in {d["code"] for d in r_c.get("diagnostics", [])},
              str([d["code"] for d in r_c.get("diagnostics", [])]))

    # —— 正向：脚本产生波形 + 完整日志（依赖 Verilator）——
    if not _HAVE_VLT:
        skip("fresh waveform satisfies require_waveform")
        skip("full log persisted to disk")
    maker = mk_script("run_wave.sh", (
        'echo "== sim: apb (top=t) =="\n'
        f'printf \'$version fake $end\\n\' > "{base}/wave_ok.vcd"\n'
        'echo "PASS: apb all 8 checks passed"\n'
    ))
    logd = base / "logs"
    r_ok = call_tool("run_sim", {
        "sim_script": str(maker), "suite": "apb", "log_dir": str(logd),
        "waveform": True, "require_waveform": True,
    }) if _HAVE_VLT else {"ok": False, "error": _skip_reason, "data": {}}
    if _HAVE_VLT:
        check("fresh waveform satisfies require_waveform", r_ok["ok"], str(r_ok.get("error")))
    if r_ok["ok"]:
        kinds = [w["kind"] for w in r_ok["data"]["waveform"]["files"]]
        check("VCD detected", "VCD" in kinds, str(kinds))
        log_info = r_ok["data"]["log"]
        check("full log persisted to disk", bool(log_info and Path(log_info["path"]).is_file()),
              str(log_info))
        if log_info:
            content = Path(log_info["path"]).read_text(encoding="utf-8")
            check("log file keeps first line (not just tail)",
                  "== sim: apb" in content, content[:60])
            check("log file keeps last line", "PASS: apb" in content, content[-60:])

    # —— 缺陷 88：run_sim 传 Makefile → 明确指引用 make，而非 code 127 ——
    # 契约路径（emit_testbench --spec-path）产出的是 Makefile，不是 shell 脚本。
    # 旧实现直接 `bash Makefile` → 127 "command not found"，用户不知道该用 make。
    # 这条不依赖 Verilator：Makefile 类型检测在版本检查之前。
    mkf = base / "Makefile"
    mkf.write_text("# 契约路径 cocotb Makefile\nTOPLEVEL_LANG = verilog\n", encoding="utf-8")
    r_mk = call_tool("run_sim", {"sim_script": str(mkf), "suite": "x"})
    check("run_sim rejects a Makefile with actionable hint", not r_mk["ok"],
          str(r_mk.get("error")))
    check("Makefile error suggests using make",
          "run it with make" in str(r_mk.get("error")), str(r_mk.get("error"))[:120])
    check("Makefile error mentions VERILATOR_TRACE for waveform",
          "VERILATOR_TRACE=1" in str(r_mk.get("error")))

    # —— 长日志：返回值里是 tail，但磁盘上必须是全文 ——
    long_script = mk_script("run_long.sh",
                            'for i in $(seq 1 400); do echo "[CHECK] id=L$i padding"; done\n'
                            'echo "PASS: apb all 8 checks passed"\n')
    logd2 = base / "logs2"
    r_long = call_tool("run_sim", {"sim_script": str(long_script), "suite": "apb",
                                   "log_dir": str(logd2)}) if _HAVE_VLT else {
        "ok": False, "error": _skip_reason, "data": {}}
    if _HAVE_VLT:
        check("long run ok", r_long["ok"], str(r_long.get("error")))
    else:
        skip("long run ok")
    if r_long["ok"]:
        li = r_long["data"]["log"]
        full = Path(li["path"]).read_text(encoding="utf-8")
        check("log file holds the FULL output (beyond 4000-char tail)",
              len(full) > len(r_long["data"]["stdout_tail"]),
              f"full={len(full)} tail={len(r_long['data']['stdout_tail'])}")
        check("log marks truncation in response", li["truncated_in_response"] is True, str(li))

    # —— 非法格式：enum 校验（E025 在真 verilator 环境下才触发，这里只测枚举）——
    r_bad = call_tool("run_sim", {"sim_script": str(plain), "suite": "apb",
                                  "waveform": True, "waveform_format": "bogus"})
    check("invalid waveform_format rejected", not r_bad["ok"], str(r_bad["ok"]))


def test_waveform_dump_injected_into_tb(tmp: Path) -> None:
    """生成的 TB 必须带 $dumpfile/$dumpvars，且声明 plusargs 目标变量。

    回归 73-A：第一版只注入了 $value$plusargs("WAVE_FILE=%s", wave_path)，
    却没有声明 `string wave_path;`，Verilator 直接报
    "Can't find definition of variable: 'wave_path'" 拒绝编译。
    这个问题只在**真正编译一次**时才暴露，静态看代码看不出来。
    """
    out = tmp / "wvdump"
    r = call_tool("emit_testbench", {
        "rtl_file": str(Path(__file__).resolve().parents[1]
                        / "examples" / "multi_bus_ref" / "apb_slave.sv"),
        "out_dir": str(out), "protocol": "apb",
    })
    check("emit tb for waveform test", r["ok"], str(r.get("error")))
    if not r["ok"]:
        return
    tb = Path(r["data"]["artifacts"]["sv_testbench"])
    src = tb.read_text(encoding="utf-8")
    check("TB has $dumpfile", "$dumpfile" in src)
    check("TB has $dumpvars", "$dumpvars" in src)
    check("TB declares wave_path variable", "string wave_path" in src)
    check("TB gates dump behind +WAVE plusarg", '$test$plusargs("WAVE")' in src)
    # 运行脚本必须能响应 WAVE=1 注入 --trace
    sim = Path(r["data"]["artifacts"]["sim_script"]).read_text(encoding="utf-8")
    check("sim script honours WAVE=1", 'WAVE="${WAVE:-0}"' in sim)
    check("sim script adds --trace", "--trace" in sim)
    check("sim script honours WAVE_FORMAT", "WAVE_FORMAT" in sim)
    check("sim script passes +WAVE plusarg", "+WAVE" in sim)
    # 缺陷 D：构建缓存必须记录**格式**，不能只记"开没开波形"。
    # 否则从 vcd 切到 fst 时会复用 VCD 二进制，产出后缀 .fst、内容是 VCD 的假文件。
    check("sim script records wave mode (format, not just on/off)",
          ".wave_mode" in sim and "want_tag" in sim, sim[:200])
    # 回归 78：`local want_tag` 曾写在 `if/else` 的 else 分支里，而脚本用
    # `set -u`。全新目录**首次**构建走 then 分支（binary 不存在）时
    # want_tag 从未定义，之后 `printf '%s' "$want_tag"` 报 unbound variable、
    # rc=1 —— 首次构建 100% 失败。第二次运行 binary 已存在、走 else 分支
    # 反而正常，把缺陷完全掩盖。
    # 用 bash 语法树（nameref 检查）+ 静态定位来锁死：want_tag 的声明必须
    # 出现在 run_one 函数体内、且不在 if/then 或 if/else 的分支里。
    m = re.search(r"^run_one\(\)\s*\{(.*?)^\}", sim, re.M | re.S)
    check("run_one function block found", bool(m), "run_one not matched")
    if m:
        body = m.group(1)
        lines = body.splitlines()
        # 找到 declare 行（含 `local want_tag`）与它所在的 if/then/else 深度
        dec_idx = next((i for i, ln in enumerate(lines)
                        if re.search(r"^\s*local\s+want_tag=", ln)), None)
        check("want_tag declared inside run_one", dec_idx is not None)
        if dec_idx is not None:
            # 统计声明行之前未闭合的 if 数量：>0 说明声明在某个 if 的分支内
            depth = 0
            for ln in lines[:dec_idx]:
                s = ln.strip()
                if re.match(r"^if\b", s):
                    depth += 1
                elif s == "fi" or s.startswith("fi;"):
                    depth -= 1
            check("want_tag declared at run_one top scope (not inside if/else)",
                  depth == 0, f"depth={depth}")
            # 声明之后同一函数体内必须用到 want_tag
            used = any("want_tag" in ln and not re.match(r"^\s*local\s+want_tag=", ln.strip())
                       for ln in lines[dec_idx + 1:])
            check("want_tag actually used after declaration", used)


def test_contract_path_waveform_and_w014(tmp: Path) -> None:
    """契约路径（文档示例 1 用的那条）也必须有波形支持，且不能静默失效。

    回归 79：`emit_testbench --spec-path` 走 `tb_emitter.py`（不是
    protocol_tb_emitter.py），原先**完全没有**波形支持；同时
    `run_sim --waveform` 在脚本不响应 WAVE=1 时静默返回空 files。

    锁死写它时踩到的三个真实缺陷：
      A. 兜底文件名 `{mod}.vcd` 以裸标识符输出 → Verilator 把 `.vcd` 当层次
         引用，报 `Can't find definition of 'vcd'`，**契约路径 TB 根本编译不过**。
      B. 第一版用编译期 `` `ifdef WAVE_DUMP ``，但全仓库没有任何调用方 define
         它 → 死宏，等于永远不注入波形（静默失效）。
      C. `run_sim(waveform=True)` 在脚本不响应 WAVE=1 时返回 ok=True + 空 files，
         "波形开了只是没人看" → 现在给 W014 警告。
    """
    from fpga_toolbox import call_tool

    contract = tmp / "contract.yaml"
    contract.write_text(
        "boundary:\n"
        "  module_name: reg_slave\n"
        "  rtl_files: [dut.sv]\n"
        "  parameters: {}\n"
        "  clock: {port: aclk, freq_mhz: 100.0}\n"
        "  reset: {port: aresetn, active_low: true, sync: true}\n"
        "  out_of_scope_ports: []\n"
        "  axi4_lite:\n"
        "    addr_width: 12\n"
        "    data_width: 32\n"
        "    role: slave\n"
        "    port_map:\n"
        "      awaddr: s_axi_awaddr\n"
        "      awprot: s_axi_awprot\n"
        "      awvalid: s_axi_awvalid\n"
        "      awready: s_axi_awready\n"
        "      wdata: s_axi_wdata\n"
        "      wstrb: s_axi_wstrb\n"
        "      wvalid: s_axi_wvalid\n"
        "      wready: s_axi_wready\n"
        "      bresp: s_axi_bresp\n"
        "      bvalid: s_axi_bvalid\n"
        "      bready: s_axi_bready\n"
        "      araddr: s_axi_araddr\n"
        "      arprot: s_axi_arprot\n"
        "      arvalid: s_axi_arvalid\n"
        "      arready: s_axi_arready\n"
        "      rdata: s_axi_rdata\n"
        "      rresp: s_axi_rresp\n"
        "      rvalid: s_axi_rvalid\n"
        "      rready: s_axi_rready\n"
        "test_plan:\n"
        "  protocol_rules:\n"
        "    check_valid_stability: true\n"
        "    check_reset_outputs: true\n"
        "    max_resp_cycles: 16\n"
        "    allow_unaligned_addr: false\n"
        "  register_rules:\n"
        "  - {rule_id: REG_001, name: CTRL_REG, offset: 0, reset_value: 0,\n"
        "     access: RW, write_mask: 0xffffffff, read_mask: 0xffffffff}\n"
        "  illegal_addr_rules: []\n"
        "  stress_profile: {random_backpressure: true, iterations: 8, seed: 1}\n",
        encoding="utf-8",
    )
    (tmp / "dut.sv").write_text(
        "`timescale 1ns/1ps\n"
        "module reg_slave(input logic aclk, input logic aresetn,\n"
        "  input logic [11:0] s_axi_awaddr, input logic [2:0] s_axi_awprot,\n"
        "  input logic s_axi_awvalid, output logic s_axi_awready,\n"
        "  input logic [31:0] s_axi_wdata, input logic [3:0] s_axi_wstrb,\n"
        "  input logic s_axi_wvalid, output logic s_axi_wready,\n"
        "  output logic [1:0] s_axi_bresp, output logic s_axi_bvalid,\n"
        "  input logic s_axi_bready,\n"
        "  input logic [11:0] s_axi_araddr, input logic [2:0] s_axi_arprot,\n"
        "  input logic s_axi_arvalid, output logic s_axi_arready,\n"
        "  output logic [31:0] s_axi_rdata, output logic [1:0] s_axi_rresp,\n"
        "  output logic s_axi_rvalid, input logic s_axi_rready);\n"
        "  logic [31:0] rw_reg;\n"
        "  always_ff @(posedge aclk) begin\n"
        "    if (!aresetn) rw_reg <= '0;\n"
        "    else if (s_axi_awvalid && s_axi_wvalid) rw_reg <= s_axi_wdata;\n"
        "  end\n"
        "  assign s_axi_awready = 1'b1; assign s_axi_wready = 1'b1;\n"
        "  assign s_axi_bresp = 2'b00; assign s_axi_bvalid = s_axi_awvalid && s_axi_wvalid;\n"
        "  assign s_axi_arready = 1'b1; assign s_axi_rdata = rw_reg;\n"
        "  assign s_axi_rresp = 2'b00; assign s_axi_rvalid = s_axi_arvalid;\n"
        "endmodule\n",
        encoding="utf-8",
    )

    r = call_tool("emit_testbench", {
        "spec_path": str(contract), "out_dir": str(tmp / "tb"),
        "rtl_file": str(tmp / "dut.sv"),
    })
    check("emit contract-path tb", r["ok"], str(r.get("error")))
    if not r["ok"]:
        return

    wrap = Path(r["data"]["artifacts"]["unit_isolation_wrapper"])
    src = wrap.read_text(encoding="utf-8")

    # —— 缺陷 A：兜底文件名必须是**带引号的字符串字面量** ——
    # 裸 `reg_slave.vcd` 会被 Verilator 当层次引用报错，契约 TB 编译不过。
    dumpfiles = re.findall(r'\$dumpfile\(([^)]+)\)', src)
    check("contract wrapper has $dumpfile", bool(dumpfiles))
    for arg in dumpfiles:
        arg = arg.strip()
        if not arg.startswith('"'):
            # wave_path 来自 $value$plusargs，是变量，不是字面量 —— 跳过
            continue
        check(f"dumpfile literal arg is a quoted string: {arg}",
              re.fullmatch(r'"[^"]+"', arg) is not None, arg)

    # —— 缺陷 B：必须用运行期 plusarg，不能用死宏 ——
    check("contract wrapper gates dump on +WAVE plusarg",
          '$test$plusargs("WAVE")' in src)
    check("contract wrapper has no dead `ifdef WAVE_DUMP", "`ifdef WAVE_DUMP" not in src)
    check("contract wrapper has $dumpvars", "$dumpvars" in src)
    # `$value$plusargs` 的目标变量必须声明（否则 Verilator 报未定义直接拒绝编译）
    check("contract wrapper declares wave_path variable", "string wave_path;" in src)

    # —— 缺陷 80：契约路径必须有运行入口（Makefile）——
    # 旧实现只产出 4 个源文件，用户拿到后得自己手写 cocotb Makefile，且 cocotb
    # 2.x 有 4 个反直觉的值。实测踩坑（Verilator 5.054 + cocotb 2.1.0）：
    #   * TOPLEVEL_LANG 写成 "verilator" 会静默跳过仿真，仍报 TESTS=1 / rc=0
    #   * 默认 simulator 是 icarus，必须显式 SIM=verilator
    #   * 波形要用 VERILATOR_TRACE=1（不是 VERILATOR_EXTRA_ARGS=--trace）
    #   * PLUSARGS=+WAVE 必须显式传，否则 --trace 编了也拿不到波形
    mk = Path(r["data"]["artifacts"]["makefile"])
    check("contract path emits a Makefile", mk.is_file(), str(mk))
    if mk.is_file():
        mtext = mk.read_text(encoding="utf-8")
        check("Makefile sets TOPLEVEL_LANG=verilog (not verilator)",
              "TOPLEVEL_LANG = verilog" in mtext, mtext[:120])
        check("Makefile defaults SIM to verilator", "SIM ?= verilator" in mtext)
        check("Makefile mentions VERILATOR_TRACE for waveform",
              "VERILATOR_TRACE" in mtext)
        # cocotb 2.x 已弃用 PLUSARGS，正确变量是 COCOTB_PLUSARGS（实测有效）
        check("Makefile passes +WAVE plusarg by default (COCOTB_PLUSARGS)",
              "COCOTB_PLUSARGS = +WAVE" in mtext)
        check("Makefile names the cocotb test module",
              f"MODULE = test_{r['data']['module']}_axil" in mtext)
        check("Makefile includes cocotb Makefile.sim",
              "cocotb-config --makefiles)/Makefile.sim" in mtext)
        # RTL 已复制进 out_dir，产物自包含（cocotb 在 out_dir 里找不到外部 RTL）
        check("RTL copied into out_dir so Makefile is self-contained",
              (mk.parent / "dut.sv").is_file())
        # 缺陷 83：非 UTF-8 RTL（工业界 GBK/Shift-JIS/latin-1 注释常见）必须容错，
        # 而不是抛未捕获的 UnicodeDecodeError 冒泡成 ok=false + 一句原始异常。
        # 且复制必须按字节（write_bytes），保留原始编码。
        # 用本测试自己写的那份 inline dut（不要用 src —— 那是 wrapper 的内容）
        nonutf = mk.parent / "dut_nonutf8.sv"
        dut_bytes = (mk.parent / "dut.sv").read_bytes()
        nonutf.write_bytes(
            dut_bytes.replace(
                b"module reg_slave(",
                b"// commentaire latin-1: \xe9\xe8 pr\xe9c\xe9dent\nmodule reg_slave(",
                1,
            )
        )
        r2 = call_tool("emit_testbench", {
            "spec_path": str(contract), "out_dir": str(tmp / "tb_nonutf8"),
            "rtl_file": str(nonutf),
        })
        check("non-UTF-8 RTL still emits ok (tolerant decode)", r2["ok"],
              str(r2.get("error")))
        if r2["ok"]:
            copied = Path(r2["data"]["artifacts"]["unit_isolation_wrapper"]).parent / nonutf.name
            check("non-UTF-8 RTL copied byte-for-byte",
                  copied.is_file() and copied.read_bytes() == nonutf.read_bytes())
            # 不应把编码问题误报成"端口缺失"（实测旧版报 19 个 E011）
            e011 = [d for d in r2.get("diagnostics", []) if d.get("code") == "E011"]
            check("no spurious E011 port-missing from encoding issue", not e011,
                  str([d.get("message") for d in e011][:2]))

    # —— 缺陷 87：RTL 文件名与生成产物同名 → fail-fast，不覆盖产物 ——
    # 实测：--rtl-file .../reg_slave_axil_wrap.sv 时，复制进 out_dir 会覆盖刚
    # 生成的 wrapper（dumpvars 2→0，wrapper 内容丢失）却返回 ok=True。
    # 旧实现检测放在写产物之后，只拦住 RTL 复制却拦不住"产物已写入"。
    # RTL **内容**必须合法匹配契约（否则先在 E011 失败，到不了冲突检测），
    # 只是**文件名**与产物冲突。直接复用上面写好的合法 dut.sv 内容。
    coll_dir = tmp / "tb_coll"
    coll_dir.mkdir(parents=True, exist_ok=True)
    coll_rtl = coll_dir / "reg_slave_axil_wrap.sv"
    coll_rtl.write_bytes((tmp / "tb" / "dut.sv").read_bytes())
    r_coll = call_tool("emit_testbench", {
        "spec_path": str(contract), "out_dir": str(tmp / "tb_coll"),
        "rtl_file": str(coll_rtl),
    })
    check("name collision fails fast", not r_coll["ok"], str(r_coll.get("error")))
    check("collision error names the offending artifact",
          "same name as a generated artifact" in str(r_coll.get("error"))
          and "reg_slave_axil_wrap.sv" in str(r_coll.get("error")),
          str(r_coll.get("error"))[:120])
    # 关键：fail-fast 发生在写**任何产物**之前。tb_coll 目录和 RTL 是测试自己
    # 预先放的，但工具不应写入 wrapper/Makefile（否则 wrapper 会被 RTL 覆盖）。
    check("Makefile not written on collision",
          not (tmp / "tb_coll" / "Makefile").exists(),
          "Makefile was written despite collision")
    check("cocotb testbench not written on collision",
          not (tmp / "tb_coll" / "test_reg_slave_axil.py").exists(),
          "cocotb tb was written despite collision")
    # wrap.sv 仍是测试预放的 RTL 内容（未被工具的 wrapper 覆盖）
    check("pre-existing RTL not overwritten by generated wrapper",
          b"module reg_slave(" in (tmp / "tb_coll" / "reg_slave_axil_wrap.sv").read_bytes())

    # —— 缺陷 84：非 UTF-8 契约 YAML 同样会崩（用户手写 YAML 注释带本地编码）——
    raw_contract = Path(contract).read_bytes()
    nonutf_contract = tmp / "contract_nonutf8.yaml"
    nonutf_contract.write_bytes(raw_contract.replace(
        b"boundary:", b"# commentaire latin-1: \xe9\xe8\nboundary:", 1))
    r3 = call_tool("emit_testbench", {
        "spec_path": str(nonutf_contract), "out_dir": str(tmp / "tb_nonutf8yaml"),
        "rtl_file": str(tmp / "dut.sv"),
    })
    check("non-UTF-8 contract YAML still emits ok (tolerant decode)", r3["ok"],
          str(r3.get("error")))
    if r3["ok"]:
        check("non-UTF-8 YAML produces same module", r3["data"]["module"] == "reg_slave",
              str(r3["data"].get("module")))

    # —— 缺陷 C：run_sim waveform=True 但无波形 → W014 警告（ok 仍为 True）——
    # 需要真跑一次脚本，而 run_sim 在跑之前会检查 Verilator >= 5.040（E020），
    # 所以无 verilator 的环境（裸容器）必须跳过，否则把"环境缺失"当"功能失效"。
    def _vlt_usable() -> bool:
        try:
            from fpga_toolbox.eda_env import probe_eda_env
            return bool(probe_eda_env()["verilator"]["meets_min_version"])
        except Exception:  # noqa: BLE001
            return False

    if not _vlt_usable():
        print("  skip run_sim waveform W014  (no Verilator >= 5.040 in this environment)")
        return

    fake = tmp / "run_plain.sh"
    fake.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho \"PASS: fake\"\n",
                    encoding="utf-8")
    import os
    import stat as _stat
    os.chmod(fake, os.stat(fake).st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)
    r2 = call_tool("run_sim", {
        "sim_script": str(fake), "suite": "x", "waveform": True,
    })
    check("run_sim with waveform=True still ok", r2["ok"], str(r2.get("error")))
    warns = r2.get("data", {}).get("waveform_warnings", [])
    check("W014 emitted when no waveform produced despite waveform=True",
          any(w.get("code") == "W014" for w in warns), str(warns))
    # 诊断里也要可见（不只埋在 data 里）
    all_codes = {d.get("code") for d in r2.get("diagnostics", [])}
    check("W014 surfaced in diagnostics", "W014" in all_codes, str(sorted(all_codes)))



def test_docs_html_markdown_sync(tmp: Path) -> None:
    """HTML 文档必须与 markdown 同步，且产物结构/链接/锚点自检通过。

    回归 69：`docs/*.html` 由 `docs/build_docs_html.py` 从 markdown 派生。
    markdown 是唯一事实源——两边同时维护必然漂移。这里直接调用生成器的
    `--check` 逻辑（重新渲染并与已提交文件逐字节比对），并复用其质量自检。

    另锁死三个写这个生成器时真实踩到的坑：
      * `<strong>` 被 html.escape 转义成可见文本（先转义后打标签才对）
      * 把 `.md` 无脑改成 `.html` 造成死链（只有目标存在才改写）
      * 页面里出现两个 `<h1>`（hero 与正文标题重复）
    """
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    gen_path = root / "docs" / "build_docs_html.py"
    if not gen_path.is_file():
        check("docs HTML generator exists", False, str(gen_path))
        return

    spec = importlib.util.spec_from_file_location("build_docs_html", gen_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    for src_name, meta in mod.DOCS.items():
        out_path = mod.HERE / meta["out"]
        check(f"{meta['out']} exists", out_path.is_file(), str(out_path))
        if not out_path.is_file():
            continue

        rendered = mod.build(src_name, meta)
        current = out_path.read_text(encoding="utf-8")
        check(f"{meta['out']} in sync with {src_name}", current == rendered,
              "重新运行 python3 docs/build_docs_html.py")

        problems = mod.lint_output(out_path)
        check(f"{meta['out']} passes structural lint", not problems, str(problems[:3]))

        # 转义标签泄漏（真实 bug：页面直接显示 <strong>...</strong>）
        check(f"{meta['out']} has no escaped tags",
              "&lt;strong&gt;" not in current and "&lt;code&gt;" not in current)

        # 回归 81：blockquote 内嵌围栏代码块曾被当普通文本，整段代码丢失并
        # 泄漏 \x00 占位符（lint_output 会抓到）。这里直测生成器的核心转换。
        probe_md = "> 引用行\n>\n> ```makefile\n> TOPLEVEL_LANG = verilog\n> ```\n>\n> - 项目 `X`\n"
        body, _ = mod.convert(probe_md)
        check("blockquote preserves fenced code block",
              "TOPLEVEL_LANG" in body and '<div class="code-block">' in body, body[:120])
        check("blockquote emits no placeholder leak", "\x00" not in body)

        # 只允许一个 h1（hero），正文 h1 应被跳过
        check(f"{meta['out']} has exactly one h1", current.count("<h1") == 1,
              str(current.count("<h1")))

        # 相对 .md 链接只在目标 .html 存在时才被改写
        for href in ("TEST_REPORT_AND_PLAN.html", "../README.html", "../changelog/INDEX.html"):
            check(f"{meta['out']} has no dead link {href}", f'href="{href}"' not in current)


def test_all_scripts_parse_under_py310() -> None:
    """全仓库脚本必须能在 Python 3.10 下解析（含 docs/ 与 examples/）。

    回归 72：`docs/build_docs_html.py` 把反斜杠写进了 f-string 的表达式部分：

        f"<li>{_inline(re.sub(r'^\\s*[-*]\\s+', '', x))}</li>"

    PEP 701 从 **Python 3.12** 起才允许这种写法。本地开发机是 3.12（更宽松），
    所以本地怎么跑都正常；CI 的 py3.10 / py3.11 job 直接 SyntaxError。

    更根本的问题是语法门只扫了 `fpga_toolbox/` 和 `tests/`，`docs/`、`examples/`
    下的脚本从未被检查——用户同样会在低版本 Python 上执行它们。这里把扫描范围
    对齐到 CI，并断言「未被覆盖的目录为空」。
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    scanned = 0
    bad: list[str] = []
    for sub in ("fpga_toolbox", "tests", "docs", "examples"):
        d = root / sub
        if not d.is_dir():
            continue
        for py in d.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            scanned += 1
            try:
                ast.parse(py.read_text(encoding="utf-8"), feature_version=(3, 10))
            except SyntaxError as exc:
                bad.append(f"{py.relative_to(root)}:{exc.lineno}: {exc.msg}")

    check("py3.10 syntax gate covers docs/ + examples/", scanned > 0, str(scanned))
    check("all repo scripts parse under Python 3.10", not bad, "; ".join(bad[:3]))

    # 专项：f-string 表达式内不得出现反斜杠（3.10/3.11 不允许）。
    # 必须走 AST + get_source_segment，不能用正则扫原始文本——本函数的 docstring
    # 里就有一段"错误写法"的示例代码，正则会把它误判成真实违规。
    offenders: list[str] = []
    for sub in ("fpga_toolbox", "tests", "docs", "examples"):
        d = root / sub
        if not d.is_dir():
            continue
        for py in d.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            src = py.read_text(encoding="utf-8")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.JoinedStr):
                    continue
                for v in node.values:
                    if not isinstance(v, ast.FormattedValue):
                        continue
                    seg = ast.get_source_segment(src, v.value) or ""
                    if "\\" in seg:
                        offenders.append(f"{py.relative_to(root)}:{v.lineno}")
    check("no backslash inside f-string expressions", not offenders,
          ", ".join(sorted(set(offenders))[:3]))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fpga_tb_") as td:
        tmp = Path(td)
        test_sandbox_guards(tmp)
        test_sandbox_layout(tmp)
        test_reset_detection(tmp)
        test_pure_apb_draft(tmp)
        test_sandbox_symlink_escape(tmp)
        test_unknown_instantiations_reported(tmp)
        test_subprocess_encoding_robust(tmp)
        test_normalizer(tmp)
        test_full_chain(tmp)
        test_cli(tmp)
        test_call_tool_enforces_required(tmp)
        test_call_tool_enum_and_type_validation(tmp)
        test_sva_heartbeat_distinguishes_pass_from_notrun(tmp)
        test_generated_sva_checker_has_heartbeat(tmp)
        test_cocotb_incomplete_report_is_failure(tmp)
        test_generated_cocotb_tb_writes_completion(tmp)
        test_verilator_missing_summary_is_failure(tmp)
        test_sv_silent_pass_detected(tmp)
        test_docs_html_markdown_sync(tmp)
        test_all_scripts_parse_under_py310()
        test_waveform_and_log_capture(tmp)
        test_waveform_dump_injected_into_tb(tmp)
        test_contract_path_waveform_and_w014(tmp)
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + ", ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
