"""SV 解析器（builtin/slang 双后端、位宽、typedef、localparam）

SV 解析器（builtin/slang 双后端、位宽、typedef、localparam）

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

from _testkit import _available_backends  # noqa: E402

from fpga_toolbox.core.sv_parser import parse_sv_file  # noqa: E402
from fpga_toolbox.core.types import BitFieldIR  # noqa: E402
from fpga_toolbox.registry import call_tool  # noqa: E402


def test_sv_parser_non_ansi_and_bitfield_width() -> None:
    print("coverage: sv_parser._parse_non_ansi & types.BitFieldIR.width")
    legacy_sv = """
module legacy_non_ansi (clk, rst_n, a_in, b_out);
  parameter W = 8;
  localparam H = W - 1;
  input clk;
  input rst_n;
  input wire [H:0] a_in;
  output reg [W-1:0] b_out;
  always @(posedge clk) if (!rst_n) b_out <= 0; else b_out <= a_in;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "legacy_non_ansi.sv"
        lp.write_text(legacy_sv, encoding="utf-8")
        mods = parse_sv_file(lp, backend="builtin")
    assert len(mods) == 1
    m = mods[0]
    assert m.name == "legacy_non_ansi"
    pmap = {p.name: p for p in m.ports}
    assert set(pmap.keys()) == {"clk", "rst_n", "a_in", "b_out"}
    assert pmap["a_in"].direction == "input" and pmap["a_in"].width == 8
    assert pmap["b_out"].direction == "output" and pmap["b_out"].width == 8

    bf = BitFieldIR(name="CTRL", msb=7, lsb=4, access="RW", reset_value=0x5)
    assert bf.width == 4
    bf1 = BitFieldIR(name="EN", msb=0, lsb=0, access="RW")
    assert bf1.width == 1

def test_s1_sv_literal_constant_stub() -> None:
    r"""Defect 107: S1 只认纯整数 return 常量，漏掉 SV 地道常量 stub。

    旧正则 `^return [-]?\d+;` 只匹配 return 0 / -1。SV 里最地道的常量 stub
    写法 return 1'b0 / '0 / 8'hFF / 0.0 语义上同样是「helper 永远返回固定值、
    不含任何真实判定」，却全部漏报。扩成覆盖纯整数 / 位宽字面量 / 未位宽
    '0/'1 / 实数，同时保持「含标识符/运算符的表达式」不误报。
    """
    print("coverage: S1 catches SV literal constant stubs (1'b0 / '0 / 8'hFF / 0.0)")
    # S1 属 silent_pass 组（缺陷 113 从 scan_handshake 迁到 scan_sv_silent_pass）：
    # 用 scan_handshake 测它等于在测一个"规则路由错了"的实现。
    from fpga_toolbox.tb_linter import scan_sv_silent_pass

    def _scan(fn_body: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            tb = Path(td) / "tb.sv"
            tb.write_text("module tb;\n" + fn_body + "\nendmodule\n", encoding="utf-8")
            return [i["code"] for i in scan_sv_silent_pass(tb)]

    stubs = [
        ("function int chk();\nreturn 0;\nendfunction"),
        ("function int chk();\nreturn -1;\nendfunction"),
        ("function int chk();\nreturn 1;\nendfunction"),
        ("function bit chk();\nreturn 1'b0;\nendfunction"),      # 修复前漏报
        ("function logic chk();\nreturn '0;\nendfunction"),      # 修复前漏报
        ("function [7:0] chk();\nreturn 8'hFF;\nendfunction"),  # 修复前漏报
        ("function real chk();\nreturn 0.0;\nendfunction"),     # 修复前漏报
    ]
    for body in stubs:
        codes = _scan(body)
        assert "S1" in codes, (body, codes)

    # 不误伤：含标识符/运算符的表达式、含分支的函数，不是常量 stub
    legit = [
        ("function int chk(input int a);\nreturn a + 1;\nendfunction"),
        ("function bit chk(input [7:0] a);\nreturn a == 0;\nendfunction"),
        ("function bit chk(input [7:0] a);\nif (a == 0) return 1'b1;"
         "\nelse return 1'b0;\nendfunction"),
    ]
    for body in legit:
        codes = _scan(body)
        assert "S1" not in codes, (body, codes)

def test_sv_parser_tokenizer_and_header_fixes() -> None:
    """Defect 130: builtin SV 解析器的 5 处缺陷（分词器 / header / 多维位宽）。

    1. 分词器的 `IDENT` 是 `[A-Za-z_][A-Za-z0-9_$]*`，**不允许 `$` 开头**，
       也不把 `$` 放进 `SYM` —— `$clog2(DEPTH)` 被静默丢成 `clog2(DEPTH)`，
       端口宽度退化成 1（slang 正确给 4）。builtin 是 slang 不可用时的后端，
       于是整体位宽推断全错。
    2. `<<` / `>>` 被拆成两个单字符 token，`_parse_params` 用
       `" ".join(t.value ...)` 重新拼成 `< <`，`eval_int_expr` 解析失败 →
       参数丢失（`parameter int W = 1 << 3` 得到 params={}）、端口宽度退化为 1。
    3. `module automatic foo (...)` —— 自动机标是生命周期修饰符，旧实现直接取
       下一个 IDENT 当模块名，于是模块被解析成名字 `automatic`、端口全空。
    4. `module foo import pkg::*; #(...) (...)` —— header 里的 import 声明让
       旧实现跳出，**参数与端口全部丢失**。
    5. 多维 packed 数组 `logic [1:0][7:0]` 总位宽是各维**乘积**（16）：builtin
       只解析第一维，slang 的 `re.search` 也只取第一维 → 分别得到 8 / 2。
    """
    print("coverage: SV tokenizer ($clog2, shifts), module header, multi-dim widths")
    from fpga_toolbox.core.sv_parser import parse_sv_file, sv_tokenize

    # 1) $ 前缀 token
    assert [t.value for t in sv_tokenize("$clog2(8)")][0] == "$clog2", \
        [t.value for t in sv_tokenize("$clog2(8)")]
    # 2) 多字符运算符必须是一个 token
    for expr, want in (("1 << 3", ["1", "<<", "3"]),
                       ("8 >> 2", ["8", ">>", "2"]),
                       ("a <= b", ["a", "<=", "b"]),
                       ("x == y", ["x", "==", "y"])):
        assert [t.value for t in sv_tokenize(expr)] == want, expr

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # $clog2 端口宽度
        f1 = root / "clog2.sv"
        f1.write_text(
            "module clog2_dut #(parameter int DEPTH = 16) (\n"
            "  input  logic clk,\n"
            "  output logic [$clog2(DEPTH)-1:0] addr\n);\n"
            "  assign addr = '0;\nendmodule\n", encoding="utf-8")
        m = parse_sv_file(f1, backend="builtin")[0]
        addr = next(p for p in m.ports if p.name == "addr")
        assert addr.width == 4, (addr.width_expr, addr.width)

        # 移位参数
        f2 = root / "shift.sv"
        f2.write_text(
            "module sp_dut #(parameter int W = 1 << 3) (\n"
            "  input  logic [W-1:0] d,\n  output logic [W-1:0] q\n);\n"
            "  assign q = d;\nendmodule\n", encoding="utf-8")
        m2 = parse_sv_file(f2, backend="builtin")[0]
        assert m2.parameters.get("W") == 8, m2.parameters
        assert all(p.width == 8 for p in m2.ports), [(p.name, p.width) for p in m2.ports]

        # module automatic
        f3 = root / "auto.sv"
        f3.write_text(
            "module automatic auto_dut (\n  input logic clk,\n"
            "  output logic q\n);\n  assign q = clk;\nendmodule\n", encoding="utf-8")
        m3 = parse_sv_file(f3, backend="builtin")[0]
        assert m3.name == "auto_dut", m3.name
        assert {p.name for p in m3.ports} == {"clk", "q"}, [p.name for p in m3.ports]

        # module ... import pkg::*; #(...) (...)
        f4 = root / "imp.sv"
        f4.write_text(
            "module imp_dut import mypkg::*; #(parameter int W = 8) (\n"
            "  input  logic [W-1:0] d,\n  output logic [W-1:0] q\n);\n"
            "  assign q = d;\nendmodule\n", encoding="utf-8")
        m4 = parse_sv_file(f4, backend="builtin")[0]
        assert m4.name == "imp_dut", m4.name
        assert m4.parameters.get("W") == 8, m4.parameters
        assert {p.name for p in m4.ports} == {"d", "q"}, [p.name for p in m4.ports]

        # 多维 packed：两个后端都要给乘积
        f5 = root / "multi.sv"
        f5.write_text(
            "module multi_dut (\n  input  logic [1:0][7:0] d2d,\n"
            "  output logic [3:0][3:0] d2d2,\n  output logic [7:0] flat\n);\n"
            "endmodule\n", encoding="utf-8")
        for backend in _available_backends():
            mods = parse_sv_file(f5, backend=backend)
            if not mods:
                continue
            got = {p.name: p.width for p in mods[0].ports}
            assert got.get("d2d") == 16, (backend, got)
            assert got.get("d2d2") == 16, (backend, got)
            assert got.get("flat") == 8, (backend, got)

def test_typedef_port_width_placeholders_are_legal() -> None:
    """Defect 138/139: typedef/interface 端口位宽未解析时不能生成非法或致败代码。

    纯文本解析拿不到 typedef（enum/struct）与 interface 端口的位宽，实测都是
    width=0。旧实现：

    - 138: `out_of_scope_ports` 的 tie_off 用 f"{width}'b0" → 产出非法字面量
      `0'b0`（零位宽）；tb_emitter 直接写进 `.port(0'b0)`，生成物必然编译失败。
      修法：位宽未知时用 SV 的 unsized `'0`，并附 warning 说明位宽未解析。
    - 139: 输出端口的观察变量按 `if o.width > 1` 三态声明，width=0 落到 1 位，
      而 DUT 端口实际可能多位（enum 常见 2/3 位）→ WIDTHTRUNC，Verilator 默认
      warning-as-error 直接构建失败。修法：该端口用**显式空连接** `.port()`
      （实测零告警），并通过 W054 诊断告知调用方未观察该端口。
    """
    print("coverage: unresolved typedef port widths use legal placeholders")
    from fpga_toolbox.registry import call_tool

    rtl = (
        "package tdpkg;\n"
        "  typedef enum logic [2:0] { T0, T1, T2 } dbg_e;\n"
        "endpackage\n"
        "module axil_typed\n"
        "  import tdpkg::*;\n"
        "(\n"
        "  input  logic        ACLK,\n"
        "  input  logic        ARESETn,\n"
        "  input  logic [7:0]  AWADDR,\n"
        "  input  logic        AWVALID,\n"
        "  output logic        AWREADY,\n"
        "  input  logic [31:0] WDATA,\n"
        "  input  logic [3:0]  WSTRB,\n"
        "  input  logic        WVALID,\n"
        "  output logic        WREADY,\n"
        "  output logic [1:0]  BRESP,\n"
        "  output logic        BVALID,\n"
        "  input  logic        BREADY,\n"
        "  input  logic [7:0]  ARADDR,\n"
        "  input  logic        ARVALID,\n"
        "  output logic        ARREADY,\n"
        "  output logic [31:0] RDATA,\n"
        "  output logic [1:0]  RRESP,\n"
        "  output logic        RVALID,\n"
        "  input  logic        RREADY,\n"
        "  input  dbg_e        dbg_mode_i,\n"
        "  output dbg_e        dbg_state_o\n"
        ");\n"
        "  assign AWREADY = 1'b1; assign WREADY = 1'b1; assign BVALID = 1'b0;\n"
        "  assign BRESP = 2'b00; assign ARREADY = 1'b1; assign RVALID = 1'b0;\n"
        "  assign RDATA = 32'h0; assign RRESP = 2'b00; assign dbg_state_o = T0;\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "axil_typed.sv"
        f.write_text(rtl, encoding="utf-8")

        # 138: 输入侧 tie_off 不能是 0'b0（显式覆盖 builtin 降级后端与默认后端）
        ex_builtin = call_tool("extract_interfaces", {"rtl_file": str(f), "parser_backend": "builtin"})
        assert ex_builtin["ok"], str(ex_builtin.get("error"))[:200]
        oos_b = {o["port"]: o for o in ex_builtin["data"]["modules"][0]["out_of_scope_ports"]}
        assert oos_b["dbg_mode_i"]["width"] == 0, oos_b["dbg_mode_i"]
        assert oos_b["dbg_mode_i"]["tie_off"] == "'0", oos_b["dbg_mode_i"]

        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        mod = ex["data"]["modules"][0]
        oos = {o["port"]: o for o in mod["out_of_scope_ports"]}
        assert "dbg_mode_i" in oos, sorted(oos)
        assert oos["dbg_mode_i"]["tie_off"] != "0'b0", oos["dbg_mode_i"]
        assert oos["dbg_mode_i"]["tie_off"] == "'0", oos["dbg_mode_i"]
        assert "warning" in oos["dbg_mode_i"], oos["dbg_mode_i"]

        # 139: 观察端口用显式空连接，且报 W054
        draft = root / "draft.yaml"
        dr = call_tool("extract_interfaces",
                       {"rtl_file": str(f), "emit_draft_yaml": str(draft)})
        assert dr["ok"], str(dr.get("error"))[:200]
        em = call_tool("emit_testbench", {
            "spec_path": str(draft), "rtl_file": str(f), "out_dir": str(root / "out")})
        assert em["ok"], str(em.get("error"))[:300]
        assert em["data"]["unconnected_observation_ports"] == ["dbg_state_o"], \
            em["data"].get("unconnected_observation_ports")
        codes = [(d["code"], d["path"]) for d in (em.get("diagnostics") or [])]
        assert ("W054", "wrapper[dbg_state_o]") in codes, codes
        wrap = Path(em["data"]["artifacts"]["unit_isolation_wrapper"]).read_text(
            encoding="utf-8")
        assert ".dbg_state_o()" in wrap, wrap
        assert "0'b0" not in wrap, "illegal zero-width literal in wrapper"
        # 输入侧 tie-off 必须是合法的 unsized '0
        assert ".dbg_mode_i('0)" in wrap, wrap

def test_unexpanded_sv_interface_ports_are_reported() -> None:
    """Defect 140: interface 打包的总线端口不能被报成「DUT 缺端口」。

    DUT 顶层只有 `apb_if.slave apb` 一个端口时，各平铺信号（psel/pready/...）
    都在 interface 内部。旧实现给 extract_interfaces 返回
    `protocol="apb"` + `port_map={}` + `missing_signals=[]`，**零诊断** ——
    调用方以为抽到了可用接口；下游 emit_testbench 则报
    「DUT is missing port(s) psel->psel, ...」，把「端口是 interface 类型」
    误述成「DUT 漏了端口」，排查方向完全错。
    """
    print("coverage: unexpanded SV interface ports are reported honestly")
    from fpga_toolbox.registry import call_tool

    src = (
        "interface apb_if (input logic clk, input logic rst_n);\n"
        "  logic psel, penable, pwrite, pready;\n"
        "  logic [7:0] paddr;\n"
        "  logic [31:0] pwdata, prdata;\n"
        "  logic pslverr;\n"
        "  modport slave (input psel, penable, pwrite, paddr, pwdata,\n"
        "                 output pready, prdata, pslverr);\n"
        "endinterface\n"
        "module iface_dut (\n"
        "  input logic clk,\n"
        "  input logic rst_n,\n"
        "  apb_if.slave apb\n"
        ");\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "iface_dut.sv"
        f.write_text(src, encoding="utf-8")
        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        mod = ex["data"]["modules"][0]
        # 如实标记：接口未展开
        assert mod["interfaces_expandable"] is False, mod.get("interfaces_expandable")
        unexp = mod["unexpanded_sv_interfaces"]
        assert len(unexp) == 1, unexp
        assert unexp[0]["port"] == "apb", unexp[0]
        assert unexp[0]["interface_type"] == "apb_if", unexp[0]
        assert unexp[0]["modport"] == "slave", unexp[0]

        # 下游错误信息必须指向 interface，而不是「DUT 缺端口」
        from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench
        r = emit_protocol_testbench(str(f), str(Path(td) / "tb"), "apb")
        assert r["ok"] is False
        err = str(r.get("error"))
        assert "SV interface port" in err, err[:300]
        assert "apb_if" in err, err[:300]
        # 不再把根因说成「DUT 缺少端口」
        assert "DUT is missing port(s)" not in err, err[:300]
        assert (r.get("data") or {}).get("sv_interface", {}).get("interface_type") == "apb_if"

        # 扁平端口的 DUT 必须仍然可解析且 expandable=True
        flat = Path(td) / "flat.sv"
        flat.write_text(
            "module flat_dut (\n"
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
        exf = call_tool("extract_interfaces", {"rtl_file": str(flat)})
        mf = exf["data"]["modules"][0]
        assert mf["interfaces_expandable"] is True, mf.get("interfaces_expandable")
        assert mf["unexpanded_sv_interfaces"] == [], mf["unexpanded_sv_interfaces"]

def test_builtin_parser_unknown_typedef_width() -> None:
    """P0: builtin 解析器对 typedef 端口静默赋位宽 1。

    纯文本解析无法得知用户自定义类型（typedef enum/struct、`pkg::type`）的
    位宽。旧实现在类型前缀循环里遇到这类标识符直接 `break`，cur_w 保持默认 1：

      - 一个 3 位 enum 端口被报成 1 位 → 下游 TB 按 1 位连线，Verilator 报
        WIDTHTRUNC（默认 warning-as-error，构建失败）；
      - Defect 138/139 的防护只在 width==0 时生效，谎报成 1 直接绕过；
      - slang 后端对同一端口给 0，两个后端行为不一致。

    修复后 builtin 与 slang 对 typedef 端口一致给 0（= 未解析），并把
    `pkg::type` 也归入未知；内建类型（int/byte/longint）与可求值维度不受影响。
    """
    print("coverage: builtin parser unknown typedef width")
    from fpga_toolbox.core.sv_parser import parse_sv_file

    src = (
        "module t (\n"
        "  input  logic        clk,\n"
        "  input  logic        rst_n,\n"
        "  input  dbg_e        dbg_mode_i,\n"
        "  input  my_pkg::cfg_t cfg_i,\n"
        "  output dbg_e        dbg_state_o,\n"
        "  input  logic [7:0]  data_i,\n"
        "  input  int          cnt_i,\n"
        "  input  logic [1:0][7:0] multi_i\n"
        ");\nendmodule\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(src, encoding="utf-8")
        mods = parse_sv_file(f, backend="builtin")
        assert mods, "builtin parser returned no modules"
        got = {p.name: p.width for p in mods[0].ports}
        # 所有端口都必须被解析出来（不能因类型未知而丢端口）
        assert set(got) == {"clk", "rst_n", "dbg_mode_i", "cfg_i", "dbg_state_o",
                            "data_i", "cnt_i", "multi_i"}, sorted(got)
        # typedef / package-qualified 类型 -> 位宽未知 = 0
        assert got["dbg_mode_i"] == 0, got
        assert got["cfg_i"] == 0, got
        assert got["dbg_state_o"] == 0, got
        # 内建类型与可求值维度仍然正确
        assert got["data_i"] == 8, got
        assert got["cnt_i"] == 32, got
        assert got["multi_i"] == 16, got

        # 与 slang 后端一致（若有 slang；零 EDA 环境下跳过）
        if "slang" in _available_backends():
            sm = parse_sv_file(f, backend="slang")
            if sm:
                sgot = {p.name: p.width for p in sm[0].ports}
                for name in ("dbg_mode_i", "cfg_i", "dbg_state_o"):
                    assert sgot.get(name) == got[name] == 0, (name, sgot, got)

    # 端到端：Extract 时必须给 width=0 + tie_off='0（而不是 1 位 / 1'b0）
    from fpga_toolbox.registry import call_tool
    apb_src = (
        "package tdpkg;\n  typedef enum logic [2:0] { T0, T1, T2 } dbg_e;\n"
        "endpackage\n"
        "module typedef_apb\n  import tdpkg::*;\n(\n"
        "  input  logic        clk,\n  input  logic rst_n,\n"
        "  input  logic        psel,\n  input  logic penable,\n"
        "  input  logic        pwrite,\n  input  logic [7:0] paddr,\n"
        "  input  logic [31:0] pwdata,\n  input  logic [3:0] pstrb,\n"
        "  input  dbg_e        dbg_mode_i,\n"
        "  output logic        pready,\n  output logic [31:0] prdata,\n"
        "  output logic        pslverr,\n  output dbg_e dbg_state_o\n);\n"
        "  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
        "  assign dbg_state_o = T0;\nendmodule\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(apb_src, encoding="utf-8")
        r = call_tool("extract_interfaces", {"rtl_file": str(f),
                                             "parser_backend": "builtin"})
        assert r["ok"], str(r.get("error"))[:200]
        mod = r["data"]["modules"][0]
        oos = {o["port"]: o for o in mod["out_of_scope_ports"]}
        assert "dbg_mode_i" in oos, sorted(oos)
        # 关键：不能是 1'b0（那会把 3 位 enum 截断成 1 位）
        assert oos["dbg_mode_i"]["tie_off"] == "'0", oos["dbg_mode_i"]
        assert oos["dbg_mode_i"]["width"] == 0, oos["dbg_mode_i"]
        assert "dbg_state_o" in oos, sorted(oos)
        assert oos["dbg_state_o"].get("observe_only") is True, oos["dbg_state_o"]

def test_localparam_ternary_and_body_declared_params() -> None:
    """P0: 端口位宽表达式引用的 localparam 在模块体内，旧解析器求值不到。

    真实 RTL 常见写法：

        module m (
          input logic [DW-1:0] data_i,
          output logic [W-1:0] data_o
        );
          localparam W = 8;
          localparam DW = (W > 4) ? 16 : 8;

    两个问题叠加：

    1. `eval_int_expr` 不支持 SV 三元 `cond ? a : b`（Python ast 直接
       SyntaxError），也不支持比较运算 —— DW 永远求不出来；
    2. localparam 声明在端口列表**之后**，解析端口时参数表还是空的，于是
       `[DW-1:0]` 求值失败：位宽退化成 1，`data_i` 甚至整条端口丢失。

    修复后：模块体 localparam 在解析端口**之前**预扫描；端口列表内联的
    localparam 按 `;` 切分单独求值；三元/比较/位运算进 eval_int_expr。
    """
    print("coverage: localparam ternary and body-declared params")
    from fpga_toolbox.core.sv_parser import eval_int_expr, parse_sv_file

    # 三元与比较（含位运算）
    assert eval_int_expr("SEL ? 64 : 32", {"SEL": 1}) == 64
    assert eval_int_expr("SEL ? 64 : 32", {"SEL": 0}) == 32
    assert eval_int_expr("( W > 4 ) ? 16 : 8", {"W": 8}) == 16
    assert eval_int_expr("( W > 4 ) ? 16 : 8", {"W": 2}) == 8
    assert eval_int_expr("W > 4", {"W": 8}) == 1
    assert eval_int_expr("W >= 8", {"W": 8}) == 1
    assert eval_int_expr("W == 8", {"W": 8}) == 1
    assert eval_int_expr("8 & 3", {}) == 0
    assert eval_int_expr("8 | 3", {}) == 11
    assert eval_int_expr("~0 & 7", {}) == 7
    assert eval_int_expr("$clog2(16)", {}) == 4

    # 模块体内声明 localparam，端口列表在前面引用
    src_body = (
        "module bodyp (\n"
        "  input  logic        clk,\n"
        "  input  logic [DW-1:0] data_i,\n"
        "  output logic [W-1:0]  data_o\n"
        ");\n"
        "  localparam W = 8;\n"
        "  localparam DW = (W > 4) ? 16 : 8;\n"
        "  assign data_o = data_i[7:0];\n"
        "endmodule\n")
    # 端口列表内部内联 localparam
    src_inline = (
        "module inlinep (\n"
        "  input  logic clk,\n"
        "  localparam W = 8;\n"
        "  localparam DW = W * 2;\n"
        "  input  logic [DW-1:0] data_i,\n"
        "  output logic [W-1:0]  data_o\n"
        ");\n"
        "  assign data_o = data_i[7:0];\n"
        "endmodule\n")
    with tempfile.TemporaryDirectory() as td:
        for name, src, want_w, want_dw in (
                ("body.sv", src_body, 8, 16),
                ("inline.sv", src_inline, 8, 16)):
            f = Path(td) / name
            f.write_text(src, encoding="utf-8")
            mods = parse_sv_file(f, backend="builtin")
            assert mods, name
            mod = mods[0]
            got = {p.name: p.width for p in mod.ports}
            # 两个端口都不能丢
            assert "data_i" in got and "data_o" in got, (name, sorted(got))
            assert got["data_o"] == want_w, (name, got)
            assert got["data_i"] == want_dw, (name, got)

def test_parser_backends_agree_on_widths() -> None:
    """缺陷 185: slang 后端对「模块体声明的 parameter」引用的端口位宽退化为 0。

    ```systemverilog
    module m (
      input  logic [W-1:0] p_i,
      output logic [D-1:0] p_o
    );
      parameter W = 12;
      localparam D = 24;
    endmodule
    ```

    builtin → p_i=12 / p_o=24（token 解析能求值）
    slang   → p_i=0  / p_o=0 （AST 里该端口的 `type` 是 `<error>`）

    slang 的 `members` 里实际**能**读到 `Parameter name=W value=12`，只是端口
    类型求值失败 → type 变 `<error>` → 位宽退化成 0。参数声明在模块头 `#()`
    里时两个后端都正确，只有「模块体参数 + 端口位宽引用」这条路径不一致。

    修复：`_enrich_from_builtin` 在 slang 给 width<=0 时用 builtin 的位宽补齐
    （builtin 也是 0 则保持 0 = 未知，不用 0 覆盖 0）。
    """
    print("coverage: parser backends agree on widths")
    from fpga_toolbox.core.sv_parser import parse_sv_file

    body_param = (
        "module fz_body (\n"
        "  input  logic [W-1:0] p_i,\n"
        "  output logic [D-1:0] p_o\n"
        ");\n"
        "  parameter W = 12;\n"
        "  localparam D = 24;\n"
        "endmodule\n")
    hdr_param = (
        "module fz_hdr #(\n"
        "  parameter W = 12,\n"
        "  parameter D = 24\n"
        ") (\n"
        "  input  logic [W-1:0] p_i,\n"
        "  output logic [D-1:0] p_o\n"
        ");\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        # 模块体参数：两个后端都必须给出 12 / 24
        f1 = Path(td) / "body.sv"
        f1.write_text(body_param, encoding="utf-8")
        for backend in _available_backends():
            mods = parse_sv_file(f1, backend=backend)
            assert mods, backend
            got = {p.name: p.width for p in mods[0].ports}
            assert got.get("p_i") == 12, (backend, got)
            assert got.get("p_o") == 24, (backend, got)

        # 模块头参数：两个后端本来就一致，确认没被补齐逻辑破坏
        f2 = Path(td) / "hdr.sv"
        f2.write_text(hdr_param, encoding="utf-8")
        for backend in _available_backends():
            mods = parse_sv_file(f2, backend=backend)
            assert mods, backend
            got = {p.name: p.width for p in mods[0].ports}
            assert got.get("p_i") == 12, (backend, got)
            assert got.get("p_o") == 24, (backend, got)

        # 一致性总检：多形态声明在可用后端之间不得有差异
        if set(_available_backends()) >= {"builtin", "slang"}:
            forms = {
                "logic": "input  logic [7:0] a_i, output logic [15:0] a_o",
                "signed": "input  logic signed [5:0] s_i",
                "atoms": "input int i32, input byte i8, input shortint i16, input longint i64",
                "multidim": "input logic [1:0][7:0] m16, output logic [3:0][3:0] m16o",
                "reverse": "input logic [0:7] rev",
                "inout": "inout wire [15:0] dq",
                "names": "input logic [7:0] a, b, c, output logic [7:0] d, e",
                "implicit": "input [7:0] plain_i, output [15:0] plain_o",
            }
            for tag, ports in forms.items():
                f = Path(td) / f"f_{tag}.sv"
                f.write_text(f"module f_{tag} (\n  {ports}\n);\nendmodule\n",
                             encoding="utf-8")
                b = {p.name: (p.direction, p.width)
                     for p in parse_sv_file(f, backend="builtin")[0].ports}
                sl = {p.name: (p.direction, p.width)
                      for p in parse_sv_file(f, backend="slang")[0].ports}
                diff = [(k, b.get(k), sl.get(k)) for k in set(b) | set(sl)
                        if b.get(k) != sl.get(k)]
                assert not diff, (tag, diff)

def test_unpacked_array_ports_use_packed_width_only() -> None:
    r"""缺陷 186: slang 后端把 unpacked 维度也算进端口位宽。

    ```systemverilog
    input logic [7:0] a [0:3]     // packed 8 位，unpacked 4 个元素
    input logic       g [2]        // 无 packed 维度 → 标量 1 位
    ```

    | 端口 | builtin | slang（旧） |
    |---|---|---|
    | `a` | 8 ✓ | **32**（8×4）|
    | `b` `[3:0] b [0:1]` | 4 ✓ | **8** |
    | `g` `g [2]` | 1 ✓ | **2** |

    slang 的 type 字段其实用 `$` 前缀明确标记 unpacked 维度
    （`logic[7:0]$[0:3]`），但旧实现用 `re.findall(r"\[(\d+):(\d+)\]")`
    把两者都匹配到并相乘。端口位宽只应算 packed 部分 —— 否则下游 TB 按
    32 位连线接 8 位端口，Verilator 报 WIDTHTRUNC/WIDTHEXPAND（默认
    warning-as-error）。
    """
    print("coverage: unpacked array ports use packed width only")
    from fpga_toolbox.core.sv_parser import parse_sv_file

    src = (
        "module fz_unp (\n"
        "  input  logic [7:0]     a [0:3],\n"
        "  output logic [15:0]    b [0:1],\n"
        "  input  logic           g [2],\n"
        "  input  logic [3:0][7:0] packed_only,\n"
        "  input  logic [7:0]     mixed [0:1],\n"
        "  output logic [1:0][1:0][1:0] triple\n"
        ");\nendmodule\n")
    want = {"a": 8, "b": 16, "g": 1, "packed_only": 32, "mixed": 8, "triple": 8}
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "unp.sv"
        f.write_text(src, encoding="utf-8")
        for backend in _available_backends():
            mods = parse_sv_file(f, backend=backend)
            assert mods, backend
            got = {p.name: p.width for p in mods[0].ports}
            for name, exp in want.items():
                assert got.get(name) == exp, (backend, name, got)

def test_parser_backend_enum_is_enforced() -> None:
    """Defect 160: parser_backend 描述列了 auto/builtin/slang，却没有 enum。

    旧 schema 只有 `type: string`，于是 `parser_backend="BOGUS"` 与 `""` 都
    返回 ok=true —— 工具静默回退到默认后端，调用方以为进行了后端选择。
    这与 protocol 参数早已加 enum 的做法不一致（拼错的协议名会立即报错）。
    """
    print("coverage: parser_backend enum enforced")
    from fpga_toolbox.registry import call_tool, list_tool_schemas

    schema = next(s for s in list_tool_schemas() if s["name"] == "extract_interfaces")
    spec = schema["inputSchema"]["properties"]["parser_backend"]
    assert spec.get("enum") == ["auto", "builtin", "slang"], spec

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rtl = root / "dut.sv"
        rtl.write_text(
            "module dut (\n"
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
            ");\n  assign pready = 1'b1; assign prdata = pwdata; assign pslverr = 1'b0;\n"
            "endmodule\n", encoding="utf-8")

        for bad in ("BOGUS", "", "slan", "builtin2"):
            r = call_tool("extract_interfaces", {"rtl_file": str(rtl),
                                                 "parser_backend": bad})
            assert r["ok"] is False, (bad, r)
            err = str(r.get("error"))
            assert "Allowed values" in err, (bad, err[:200])

        for good in ("auto", "builtin", "slang"):
            r = call_tool("extract_interfaces", {"rtl_file": str(rtl),
                                                 "parser_backend": good})
            # slang 可能因环境缺失而失败，但错误不能是「非法枚举值」
            err = str(r.get("error") or "")
            assert "Allowed values" not in err, (good, err[:200])

def test_review_fixes_registry_parser_and_types() -> None:
    """Defect 115: call_tool 的 None/enum/bool 处理与 sv_parser 的整数类型宽度。

    * `{"protocol": None}`（MCP 客户端把省略的可选字段序列化成 null）被 enum
      校验拒成"非法枚举值"，而类型校验明确写了「显式 None 交给函数处理」。
    * `{"out_dir": None}` 同时通过类型校验（None 跳过）和 required 校验（key 存在），
      把 None 漏进函数体，报出与参数无关的下游错误。
    * `"number"` 类型接受 bool（isinstance(True, (int, float)) 为真）。
    * call_tool 原地改写调用方 dict（boolean 字符串强转）。
    * sv_parser：byte/shortint/int/longint 被当成 1 位；`$clog2(1)` 返回 1（应为 0）。
    """
    print("coverage: call_tool None/enum/bool + sv_parser int-type widths and $clog2")
    from fpga_toolbox.core.sv_parser import eval_int_expr, parse_sv_file

    # ---- call_tool ----
    r = call_tool("emit_testbench", {"rtl_file": "x.sv", "out_dir": "/tmp/o", "protocol": None})
    assert "Invalid value None" not in str(r.get("error")), str(r["error"])[:160]

    r = call_tool("emit_testbench", {"rtl_file": "x.sv", "out_dir": None, "protocol": "apb"})
    assert not r["ok"] and "out_dir" in r["error"] and "required" in r["error"], r.get("error")

    r = call_tool("run_mutation", {"workdir": "/tmp", "mutation_spec": "/tmp/x",
                                   "test_cmd": ["true"], "min_score": True})
    assert not r["ok"] and "boolean" in r["error"], r.get("error")

    args = {"rtl_file": "x.sv", "out_dir": "/tmp/o", "protocol": "apb"}
    snapshot = dict(args)
    call_tool("emit_testbench", args)
    assert args == snapshot, f"call_tool mutated caller dict: {args}"

    # ---- sv_parser 整数类型宽度 ----
    src = ("module intty(\n  input logic clk,\n  output int o1,\n  output byte o2,\n"
           "  output shortint o3,\n  output longint o4\n);\nendmodule\n")
    want = {"o1": 32, "o2": 8, "o3": 16, "o4": 64}
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(src, encoding="utf-8")
        for backend in _available_backends():
            mods = parse_sv_file(f, backend=backend)
            if not mods:
                continue
            got = {p.name: p.width for p in mods[0].ports}
            for k, v in want.items():
                assert got.get(k) == v, (backend, k, got)

    # ---- $clog2 边界（IEEE 1800：clog2(0)=clog2(1)=0）----
    for n, exp_v in ((0, 0), (1, 0), (2, 1), (3, 2), (4, 2), (8, 3), (9, 4), (16, 4), (17, 5)):
        assert eval_int_expr(f"$clog2({n})", {}) == exp_v, (n, eval_int_expr(f"$clog2({n})", {}))

def test_power_on_reset_names_detected() -> None:
    """Defect 112: `por_n` 这类 power-on-reset 端口名不被识别为复位。

    `por_n` 是工业界非常标准的 POR 端口名。仓库自带的
    `examples/multi_bus_ref/omni_hub/soc_omni_bus_hub.sv`（6 协议集成 DUT）就用
    `if (!por_n)`，且它是**唯一**复位 —— 但旧 `_RST_RE` 只认
    `rst/reset/nreset/nrst` 家族，`por` 完全不在其中。

    后果不是"少一条 warning"：`emit_testbench` 的每条协议模板都硬连复位脚，
    检测不到复位就直接拒绝生成 TB，于是一个 6 协议 DUT 的**所有协议路径**
    （pcie/ddr4/axi4/apb...）全部被挡掉，报错还是误导性的
    "no reset port detected in the RTL. Add a reset port
    (rst/rst_n/aresetn/hresetn/...)" —— 用户明明写了 `por_n`。

    修复：`_RST_RE` 增加 por / poreset / power_on_reset / pon 家族（含 `n` 前缀
    与 `_n`/`n`/`b` 后缀），`_reset_active_low` 同步覆盖（否则 `por_n` 会被判成
    高有效，生成 TB 的复位时序反相）。同时必须**不**误伤 `port` / `ponder` /
    `por_ready` / `por_cnt` 这类以 por 开头的普通信号（锚定 + 尾部可选后缀）。
    """
    print("coverage: power-on-reset names (por_n / npor / poresetn) are recognised")
    from fpga_toolbox.iface_extractor import _is_reset, _reset_active_low

    # 必须识别为复位，且极性正确
    must_detect = {
        "por_n": True, "por": False, "npor": True, "npor_n": True,
        "poresetn": True, "nporeset": True, "pon_n": True, "npon": True,
        "power_on_reset_n": True, "power_on_reset": False, "por_rst_n": True,
        # 既有家族不能回归
        "rst_n": True, "rst": False, "reset": False, "resetn": True,
        "s_axi_aresetn": True, "hresetn": True, "presetn": True,
        "arst_n": True, "nrst": True, "nreset": True, "resetb": True,
    }
    for name, want_low in must_detect.items():
        assert _is_reset(name), f"{name} must be detected as a reset"
        assert _reset_active_low(name) is want_low, (
            f"{name}: active_low={_reset_active_low(name)} want={want_low}")

    # 不能误伤：以 por 开头但不是复位、或带 _done/_ready 后缀的派生信号
    must_not = [
        "port", "ponder", "por_ready", "por_cnt", "por_init_guard",
        "por_cke_mrs_zqcl", "power_on_reset_done", "reset_done",
        "clk", "supports", "report",
    ]
    for name in must_not:
        assert not _is_reset(name), f"{name} must NOT be detected as a reset"

    # 端到端：6 协议集成 DUT 的复位必须被识别出来（修复前 reset=None，
    # 导致 emit_testbench 对每种协议都拒绝生成）
    omni = ROOT / "examples" / "multi_bus_ref" / "omni_hub" / "soc_omni_bus_hub.sv"
    assert omni.is_file(), omni
    ex = call_tool("extract_interfaces", {"rtl_file": str(omni)})
    assert ex["ok"], str(ex.get("error"))[:200]
    mod = ex["data"]["modules"][0]
    assert mod["reset"] is not None, "omni hub reset not detected (por_n missed)"
    assert mod["reset"]["port"] == "por_n", mod["reset"]
    assert mod["reset"]["active_low"] is True, mod["reset"]

    # 且现在能真的生成 PCIe TB（修复前被 "no reset port detected" 挡掉）
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        r = call_tool("emit_testbench", {
            "rtl_file": str(omni),
            "out_dir": str(Path(td) / "out"), "protocol": "pcie",
        })
        assert r["ok"], f"emit still blocked on por_n: {str(r.get('error'))[:200]}"


def main() -> int:
    test_sv_parser_non_ansi_and_bitfield_width()
    test_s1_sv_literal_constant_stub()
    test_sv_parser_tokenizer_and_header_fixes()
    test_typedef_port_width_placeholders_are_legal()
    test_unexpanded_sv_interface_ports_are_reported()
    test_builtin_parser_unknown_typedef_width()
    test_localparam_ternary_and_body_declared_params()
    test_parser_backends_agree_on_widths()
    test_unpacked_array_ports_use_packed_width_only()
    test_parser_backend_enum_is_enforced()
    test_review_fixes_registry_parser_and_types()
    test_power_on_reset_names_detected()
    print("ALL test_sv_parser_coverage.py TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
