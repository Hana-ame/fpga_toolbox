"""接口识别与 canonical 绑定层（分类器、方言前缀、split_port_map）

接口识别与 canonical 绑定层（分类器、方言前缀、split_port_map）

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

from fpga_toolbox.core.sv_parser import parse_sv_file  # noqa: E402
from fpga_toolbox.iface_extractor import tool_extract_interfaces  # noqa: E402
from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench  # noqa: E402
from fpga_toolbox.registry import call_tool  # noqa: E402


def test_all_11_protocol_classifiers_and_clean_pt_registry() -> None:
    print("coverage: all 11 protocol classifiers + clean_pt_netlist via call_tool")
    ref = ROOT / "examples" / "multi_bus_ref"
    omni = ref / "omni_hub" / "soc_omni_bus_hub.sv"
    r_omni = tool_extract_interfaces(str(omni))
    assert r_omni["ok"]
    protos = {i["protocol"] for i in r_omni["data"]["modules"][0]["interfaces"]}
    assert {"spi", "apb", "axi4", "ethernet", "pcie", "ddr4"}.issubset(protos), f"Missing protos: {protos}"

    for fname, exp in (
        ("ahb_lite_slave.sv", "ahb_lite"),
        ("ddr3_mem_ctrl.sv", "ddr3"),
        ("hbm_pc_ctrl.sv", "hbm"),
    ):
        r = tool_extract_interfaces(str(ref / fname))
        assert r["ok"] and any(i["protocol"] == exp for i in r["data"]["modules"][0]["interfaces"])

    # clean_pt_netlist via registry call_tool
    with tempfile.TemporaryDirectory() as td:
        in_v = Path(td) / "net.v"
        out_v = Path(td) / "net_clean.v"
        in_v.write_text("module m(output q);\n  RDBUS u0(.o_d(q));\nendmodule\n", encoding="utf-8")
        r_pt = call_tool("clean_pt_netlist", {"in_verilog": str(in_v), "out_verilog": str(out_v)})
        assert r_pt["ok"] and r_pt["data"]["removed_instances"] == 1
        assert "assign q = 1'b0;" in out_v.read_text(encoding="utf-8")

def test_iface_direction_prefix_and_ahb_ready_split() -> None:
    """Defect 131: 方向前缀拆组 + AHB HREADY/HREADYOUT 键冲突。

    1. `_norm_prefix` 原本 `re.sub(r"^(?:i_|o_|in_|out_)", ...)` —— 要求方向词
       后**必须跟下划线**。但各 `_*_PATS` 的模式是 `(?:^|_)sig$`，`m.start()`
       落在下划线**之后**，传进来的前缀是裸 `"i"` / `"o"`，正则匹配不到 →
       APB 被拆成 "i" 组与 "o" 组两组，识别出的接口只有一半信号：
       实测 i_psel/i_penable/... + o_pready/o_prdata/o_pslverr 的 APB 只认出
       group="i" 的 6 个信号，3 个输出口落进 out_of_scope_ports。
    2. `_AHB_PATS` 的 `hready` 模式是 `h_?ready(?:out|in)?$`，同时匹配
       HREADY 与 HREADYOUT，后遍历者覆盖前者。标准 AHB-Lite 从机同时暴露
       这两个端口（HREADY 输入、HREADYOUT 输出），实测
       `port_map["hready"]="HREADYOUT"` 而真正的输入 `HREADY` 落进
       out_of_scope_ports 并被 tie_off 到 `1'b0` —— TB 端永远等不到 ready，
       仿真永久挂起。
    """
    print("coverage: direction-prefix grouping + AHB HREADY/HREADYOUT split")
    from fpga_toolbox.iface_extractor import _norm_prefix

    # 1a) 裸方向词与带下划线形态都必须归一成 default
    for raw in ("i", "o", "i_", "o_", "in", "out", "in_", "out_"):
        assert _norm_prefix(raw) == "default", (raw, _norm_prefix(raw))
    # 1b) 非方向前缀不能被误伤
    for raw in ("s_axi", "m_axi", "ch0", "s_apb"):
        assert _norm_prefix(raw) == raw, (raw, _norm_prefix(raw))

    # 1c) 端到端：完整的 i_/o_ 前缀 APB 必须被认出全部 9 个信号
    apb = """module dirpref_dut (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        i_psel,
  input  logic        i_penable,
  input  logic        i_pwrite,
  input  logic [7:0]  i_paddr,
  input  logic [31:0] i_pwdata,
  input  logic [3:0]  i_pstrb,
  output logic        o_pready,
  output logic [31:0] o_prdata,
  output logic        o_pslverr
);
  assign o_pready = 1'b1; assign o_prdata = i_pwdata; assign o_pslverr = 1'b0;
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(apb, encoding="utf-8")
        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        mod = ex["data"]["modules"][0]
        ifaces = mod["interfaces"]
        assert len(ifaces) == 1, [(i["protocol"], i["group_prefix"]) for i in ifaces]
        assert ifaces[0]["protocol"] == "apb", ifaces[0]["protocol"]
        assert len(ifaces[0]["port_map"]) == 9, sorted(ifaces[0]["port_map"])
        assert mod["out_of_scope_ports"] == [], mod["out_of_scope_ports"]

        # 2) AHB 同时有 HREADY + HREADYOUT：两者必须各归各位
        ahb = """module ahb_both_dut (
  input  logic        HCLK,
  input  logic        HRESETn,
  input  logic        HSEL,
  input  logic [15:0] HADDR,
  input  logic [1:0]  HTRANS,
  input  logic        HWRITE,
  input  logic [2:0]  HSIZE,
  input  logic [31:0] HWDATA,
  input  logic        HREADY,
  output logic        HREADYOUT,
  output logic [31:0] HRDATA,
  output logic        HRESP
);
  assign HREADYOUT = HREADY;
  assign HRDATA = HWDATA;
  assign HRESP = 1'b0;
endmodule
"""
        f2 = Path(td) / "ahb.sv"
        f2.write_text(ahb, encoding="utf-8")
        ex2 = call_tool("extract_interfaces", {"rtl_file": str(f2)})
        assert ex2["ok"], str(ex2.get("error"))[:200]
        m2 = ex2["data"]["modules"][0]
        pm = next(i for i in m2["interfaces"] if i["protocol"] == "ahb_lite")["port_map"]
        assert pm.get("hready") == "HREADY", pm
        assert pm.get("hreadyout") == "HREADYOUT", pm
        # 真正的输入 HREADY 绝不能被 tie 到 1'b0
        assert all(o["port"] != "HREADY" for o in m2["out_of_scope_ports"]), \
            m2["out_of_scope_ports"]

        # 端到端：标准 AHB-Lite 从机现在能正常生成 TB（修复前误报方向不符）
        r = call_tool("emit_testbench", {
            "rtl_file": str(f2), "out_dir": str(Path(td) / "ahb_tb"),
            "protocol": "ahb_lite"})
        assert r["ok"], str(r.get("error"))[:300]
        tb = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        assert ".HREADYOUT(HREADYOUT)" in tb, "template must connect the slave's HREADYOUT"

def test_secondary_clock_names_are_recognised() -> None:
    """Defect 164: 第二时钟 `clk_extra` / `clk2` / `clk_i` 等名称识别不到。

    `_CLK_RE` 旧式样 `^(?:[a-z0-9]+_)*(?:[ahps]?clk|clock)$` 要求时钟词出现在
    **末尾**。于是多时钟设计里极常见的 `clk_extra`、`clk2`、`clk_i`、`clk_in`
    全部识别不到：它们落进 out_of_scope_ports 被 tie 到 `1'b0`，而
    `_is_clock()` 本该给这类端口加
    "clock/reset-like port left out of scope; verify tie-off is intended"
    警告 —— 识别不到连警告都没有。多时钟设计把第二个时钟 tie 到 0 是灾难性的。
    """
    print("coverage: secondary clock names are recognised")
    from fpga_toolbox.iface_extractor import _is_clock
    from fpga_toolbox.registry import call_tool

    clocks = ("clk", "clk_extra", "clk2", "clk_i", "clk_in", "clk_aux",
              "aclk", "hclk", "pclk", "sys_clk", "CLK_EXTRA", "clk_p", "clk_n")
    for n in clocks:
        assert _is_clock(n), f"clock name not recognised: {n}"
    # 名称里含 clk 但**不是**时钟的不得误判
    for n in ("clken", "clk_en", "clock_en", "clk_gate_en", "clear", "data", "psel"):
        assert not _is_clock(n), f"false positive clock name: {n}"

    # 端到端：第二个时钟必须落进 out_of_scope 并带 warning
    rtl = (
        "module twoclk (\n"
        "  input  logic clk,\n"
        "  input  logic clk_extra,\n"
        "  input  logic rst_n,\n"
        "  input  logic psel, input logic penable, input logic pwrite,\n"
        "  input  logic [7:0] paddr, input logic [31:0] pwdata, input logic [3:0] pstrb,\n"
        "  output logic pready, output logic [31:0] prdata, output logic pslverr\n"
        ");\n"
        "  assign pready = 1\'b1; assign prdata = pwdata; assign pslverr = 1\'b0;\n"
        "endmodule\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "twoclk.sv"
        f.write_text(rtl, encoding="utf-8")
        rr = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert rr["ok"], str(rr.get("error"))[:200]
        mod = rr["data"]["modules"][0]
        oos = {o["port"]: o for o in mod["out_of_scope_ports"]}
        assert "clk_extra" in oos, sorted(oos)
        assert "warning" in oos["clk_extra"], oos["clk_extra"]
        assert "clock/reset-like" in oos["clk_extra"]["warning"], oos["clk_extra"]

def test_secondary_reset_names_are_recognised() -> None:
    """Defect 165: 第二复位 `rst_extra` / `rst2` / `rst_i` 等识别不到。

    与时钟（缺陷 164）完全同构：`_RST_RE` 旧式样要求复位词在名称**末尾**，
    只允许一个 `_n`/`_b` 极性后缀。于是多复位域设计里常见的 `rst_extra`、
    `rst2`、`rst_i`、`reset_extra`、`rst_async`、`rst_sync_i` 全部识别不到：
    它们落进 out_of_scope_ports 被 tie 到固定电平，且因为 `_is_reset()` 返回
    False，连既有的 "clock/reset-like port left out of scope" 警告都不触发。
    """
    print("coverage: secondary reset names are recognised")
    from fpga_toolbox.iface_extractor import _is_reset, _reset_active_low
    from fpga_toolbox.registry import call_tool

    resets = ("rst_n", "rst", "reset_n", "nrst", "aresetn", "hresetn", "presetn",
              "rst_extra", "rst2", "rst_i", "reset_extra", "por_n", "arst_n",
              "sys_rst", "rst_async", "rst_b", "rst_sync_i", "rst_hard")
    for n in resets:
        assert _is_reset(n), f"reset name not recognised: {n}"
    # 含 rst 但不是复位
    for n in ("rst_en", "reset_en", "restart", "result", "rstdone", "burst"):
        assert not _is_reset(n), f"false positive reset name: {n}"

    # 极性判定不能因新增形态而错
    assert _reset_active_low("rst_n") is True
    assert _reset_active_low("rst") is False
    assert _reset_active_low("nrst") is True
    assert _reset_active_low("aresetn") is True
    assert _reset_active_low("rst_b") is True
    assert _reset_active_low("rst_extra") is False
    assert _reset_active_low("rst_async") is False

    # 端到端：第二复位落进 out_of_scope 并带 warning
    rtl = (
        "module tworeset (\n"
        "  input  logic clk,\n"
        "  input  logic rst_n,\n"
        "  input  logic rst_extra,\n"
        "  input  logic psel, input logic penable, input logic pwrite,\n"
        "  input  logic [7:0] paddr, input logic [31:0] pwdata, input logic [3:0] pstrb,\n"
        "  output logic pready, output logic [31:0] prdata, output logic pslverr\n"
        ");\n"
        "  assign pready = 1\'b1; assign prdata = pwdata; assign pslverr = 1\'b0;\n"
        "endmodule\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "tworeset.sv"
        f.write_text(rtl, encoding="utf-8")
        rr = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert rr["ok"], str(rr.get("error"))[:200]
        mod = rr["data"]["modules"][0]
        assert (mod["reset"] or {}).get("port") == "rst_n", mod["reset"]
        oos = {o["port"]: o for o in mod["out_of_scope_ports"]}
        assert "rst_extra" in oos, sorted(oos)
        assert "warning" in oos["rst_extra"], oos["rst_extra"]
        assert "clock/reset-like" in oos["rst_extra"]["warning"], oos["rst_extra"]

def test_direction_suffixed_bus_ports_are_classified() -> None:
    """Defect 166: `psel_i` / `pready_o` 方向后缀让协议识别彻底失败。

    各 `_*_PATS` 的形态都是 `(?:^|_)sig$`（信号名必须在末尾），方向后缀
    `_i`/`_o`/`_in`/`_out` 因此完全无解：

      - 标准 APB 从机用 psel_i/penable_i/.../pready_o/prdata_o/pslverr_o
        → interfaces=[]，9 个信号全落进 out_of_scope_ports
      - 标准 AXI4-Lite 用 awaddr_o/awready_i/... → 17 个信号全落空

    前缀问题（缺陷 131）至少有 `(?:^|_)` 兜住，后缀没有任何保底。
    """
    print("coverage: direction-suffixed bus ports are classified")
    from fpga_toolbox.registry import call_tool

    apb = (
        "module apb_suffix_dut (\n"
        "  input  logic        clk,\n"
        "  input  logic        rst_n,\n"
        "  input  logic        psel_i,\n"
        "  input  logic        penable_i,\n"
        "  input  logic        pwrite_i,\n"
        "  input  logic [7:0]  paddr_i,\n"
        "  input  logic [31:0] pwdata_i,\n"
        "  input  logic [3:0]  pstrb_i,\n"
        "  output logic        pready_o,\n"
        "  output logic [31:0] prdata_o,\n"
        "  output logic        pslverr_o\n"
        ");\n"
        "  assign pready_o = 1\'b1; assign prdata_o = pwdata_i; assign pslverr_o = 1\'b0;\n"
        "endmodule\n")

    axi = (
        "module axi_suffix_dut (\n"
        "  input  logic        aclk,\n"
        "  input  logic        aresetn,\n"
        "  output logic [7:0]  awaddr_o,\n"
        "  output logic        awvalid_o,\n"
        "  input  logic        awready_i,\n"
        "  output logic [31:0] wdata_o,\n"
        "  output logic [3:0]  wstrb_o,\n"
        "  output logic        wvalid_o,\n"
        "  input  logic        wready_i,\n"
        "  input  logic [1:0]  bresp_i,\n"
        "  input  logic        bvalid_i,\n"
        "  output logic        bready_o,\n"
        "  output logic [7:0]  araddr_o,\n"
        "  output logic        arvalid_o,\n"
        "  input  logic        arready_i,\n"
        "  input  logic [31:0] rdata_i,\n"
        "  input  logic [1:0]  rresp_i,\n"
        "  input  logic        rvalid_i,\n"
        "  output logic        rready_o\n"
        ");\n"
        "endmodule\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, src, proto, nports in (
                ("apb.sv", apb, "apb", 9),
                ("axi.sv", axi, "axi4_lite", 17)):
            f = root / name
            f.write_text(src, encoding="utf-8")
            r = call_tool("extract_interfaces", {"rtl_file": str(f)})
            assert r["ok"], str(r.get("error"))[:200]
            mod = r["data"]["modules"][0]
            ifaces = mod["interfaces"]
            assert len(ifaces) == 1, [(i["protocol"], i["group_prefix"]) for i in ifaces]
            assert ifaces[0]["protocol"] == proto, ifaces[0]["protocol"]
            assert len(ifaces[0]["port_map"]) == nports, sorted(ifaces[0]["port_map"])
            # 方向后缀端口不能被丢进 out_of_scope
            assert mod["out_of_scope_ports"] == [], mod["out_of_scope_ports"]
            # port_map 必须指向**真实**（带后缀的）端口名
            assert ifaces[0]["port_map"].get("psel", ifaces[0]["port_map"].get("awaddr", "")).endswith(("_i", "_o")), \
                ifaces[0]["port_map"]

        # 端到端：带后缀的 APB 仍能生成 TB，且 TB 里映射到真实端口名
        from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench
        r2 = emit_protocol_testbench(str(root / "apb.sv"), str(root / "tb"), "apb")
        assert r2["ok"], str(r2.get("error"))[:250]
        tb = Path(r2["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        assert ".psel_i(psel_i)" in tb, tb[:600]

def test_canonical_interface_binding_engine() -> None:
    """Canonical-interface binding engine (replaces per-protocol regex patches).

    Defects 131/166/170 were three separate patches on the same root cause: a
    hand-written regex per canonical signal, extended once per naming style.
    The engine now declares each standard interface once and binds DUT ports by
    tokenizing names (snake_case, camelCase, direction prefixes/suffixes) and
    matching a token suffix against the canonical spelling.
    """
    print("coverage: canonical interface binding engine")
    from fpga_toolbox.canonical_iface import (
        CANONICAL_INTERFACES,
        _filter_group_tokens,
        _match_suffix,
        _tokens_ok,
        bind_protocol,
        interface_score,
        tokenize_port_name,
    )

    # every declared protocol has canonical signals and a sane threshold
    for proto, ci in CANONICAL_INTERFACES.items():
        assert ci.protocol == proto, proto
        assert ci.signals, proto
        assert ci.min_signals >= 1, proto
        for sig in ci.signals:
            # `concat` normalizes underscore-containing canonicals (cs_n)
            assert sig.concat == sig.name.replace("_", "").lower(), sig.name
            assert sig.all_spellings()[0] == sig.name, sig.name

    # tokenizer styles
    assert tokenize_port_name("s0_p_sel_i") == ["s0", "p", "sel", "i"], \
        tokenize_port_name("s0_p_sel_i")
    assert tokenize_port_name("sAwAddr") == ["s", "aw", "addr"], \
        tokenize_port_name("sAwAddr")
    assert tokenize_port_name("PSlvErr") == ["p", "slv", "err"], \
        tokenize_port_name("PSlvErr")
    assert tokenize_port_name("__") == []

    # suffix matching concatenates tokens, so spacing/case do not matter
    assert _match_suffix(["s0", "p", "sel"], "psel") == (1, "psel")
    assert _match_suffix(["aw", "addr"], "awaddr") == (0, "awaddr")
    assert _match_suffix(["x"], "nosuch") is None

    # group prefix filtering: direction words never split a channel
    ci_apb = CANONICAL_INTERFACES["apb"]
    assert _filter_group_tokens(["i"], ci_apb) == []
    assert _filter_group_tokens(["o"], ci_apb) == []
    assert _filter_group_tokens(["s0"], ci_apb) == ["s0"]
    ci_pcie = CANONICAL_INTERFACES["pcie"]
    assert _filter_group_tokens(["pcie", "rx"], ci_pcie) == ["pcie"]
    assert _filter_group_tokens(["pcie", "tx"], ci_pcie) == ["pcie"]

    # protocol token rules
    assert _tokens_ok(["ddr3", "addr"], CANONICAL_INTERFACES["ddr3"]) is True
    assert _tokens_ok(["ddr4", "addr"], CANONICAL_INTERFACES["ddr3"]) is False
    assert _tokens_ok(["hbm", "awaddr"], CANONICAL_INTERFACES["hbm"]) is True
    assert _tokens_ok(["awaddr"], CANONICAL_INTERFACES["hbm"]) is False
    assert _tokens_ok(["whatever"], ci_apb) is True

    # an APB with i_/o_ prefixes binds as ONE group of 9 signals
    apb_src = (
        "module dp (\n"
        "  input  logic        clk, input logic rst_n,\n"
        "  input  logic        i_psel, input logic i_penable, input logic i_pwrite,\n"
        "  input  logic [7:0]  i_paddr, input logic [31:0] i_pwdata, input logic [3:0] i_pstrb,\n"
        "  output logic        o_pready, output logic [31:0] o_prdata, output logic o_pslverr\n"
        ");\n"
        "  assign o_pready = 1\'b1; assign o_prdata = i_pwdata; assign o_pslverr = 1\'b0;\n"
        "endmodule\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dp.sv"
        f.write_text(apb_src, encoding="utf-8")
        mod = parse_sv_file(f)[0]
    bound = bind_protocol(ci_apb, mod.ports, set())
    assert len(bound) == 1, [(b.group, len(b.signals)) for b in bound]
    bi = bound[0]
    assert bi.group == "default", bi.group
    assert len(bi.signals) == 9, sorted(bi.port_map())
    req_n, tot_n = interface_score(bi, ci_apb)
    assert tot_n == 9 and req_n >= 1, (req_n, tot_n)
    assert bi.port_map()["psel"] == "i_psel", bi.port_map()
    assert bi.port_map()["pready"] == "o_pready", bi.port_map()
    # details carry direction/width for the emitter
    det = bi.port_details()
    assert det["psel"]["direction"] == "input", det["psel"]
    assert det["pwdata"]["width"] == 32, det["pwdata"]
    assert bi.ambiguous == [], bi.ambiguous

    # `_sig` builds CanonSignal with aliases and required flag
    from fpga_toolbox.canonical_iface import _sig
    cs = _sig("tx_tvalid", "out", required=False, aliases=("tx_valid",))
    assert cs.name == "tx_tvalid" and cs.direction == "out"
    assert cs.required is False and cs.aliases == ("tx_valid",)
    assert cs.all_spellings() == ("tx_tvalid", "tx_valid")

def test_nonstandard_prefix_dut_ports_are_bound_and_wired() -> None:
    """Defect 172/173/174/175: 非标准前缀 DUT + 双向 dq 拆分的完整链路。

    绑定层（canonical_iface）已经能识别 `mem_ddr3_*` 这类前缀，但旧模板仍把
    端口名写死成 `ddr3_ck_p` 等字面量：

    - 174: `_missing_tb_ports` 拿模板侧拼写（`ddr3_ck_p`）去查以 canonical 名
      （`ck_p`）为键的 port_map，永远查不到 → 合法的带前缀 DUT 被判「缺端口」。
    - 175: 即使校验放过，实例连线仍是 `.ddr3_ck_p(...)` → PINNOTFOUND，
      工具报 ok=True 而 TB 编译失败。
    - 172: DDR 的 `dq` 在真实 RTL 里常拆成 `_dq_in`/`_dq_out` 两个端口，
      绑定层把它们当成冲突（选一个、拒一个），驱动侧或观察侧被丢进
      out_of_scope 再 tie 一次。
    - 173: DDR3 canonical 漏了 `rd_valid`，该端口落进 out_of_scope。
    """
    print("coverage: non-standard prefix DUT ports bound and wired")
    from fpga_toolbox.registry import call_tool

    ref = Path(__file__).resolve().parent.parent / "examples" / "multi_bus_ref"

    # (172) 双向 dq 拆分必须成对绑定，且两半都进 claimed（不再落 out_of_scope）
    r = call_tool("extract_interfaces", {"rtl_file": str(ref / "ddr3_mem_ctrl.sv")})
    assert r["ok"], str(r.get("error"))[:200]
    mod = r["data"]["modules"][0]
    iface = next(i for i in mod["interfaces"] if i["protocol"] == "ddr3")
    split = iface["split_port_map"]
    assert "dq" in split, iface
    assert split["dq"]["in"] == "ddr3_dq_in", split
    assert split["dq"]["out"] == "ddr3_dq_out", split
    # (173) rd_valid 必须被绑定，而不是 tie 掉
    assert iface["port_map"].get("rd_valid") == "ddr3_rd_valid", iface["port_map"]
    assert mod["out_of_scope_ports"] == [], mod["out_of_scope_ports"]
    # 拆分对不能再出现在越界端口里
    oos = {o["port"] for o in mod["out_of_scope_ports"]}
    assert "ddr3_dq_out" not in oos and "ddr3_dq_in" not in oos, oos

    with tempfile.TemporaryDirectory() as td:
        # (174/175) 非标准前缀：模板必须按真实端口名连线
        src = (ref / "ddr3_mem_ctrl.sv").read_text(encoding="utf-8")
        prefixed = src.replace("ddr3_", "mem_ddr3_")
        f = Path(td) / "mem_ddr3.sv"
        f.write_text(prefixed, encoding="utf-8")

        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        im = ex["data"]["modules"][0]["interfaces"]
        assert [(x["protocol"], len(x["port_map"])) for x in im] == [("ddr3", 11)], im
        assert im[0]["split_port_map"]["dq"] == {
            "in": "mem_ddr3_dq_in", "out": "mem_ddr3_dq_out"}, im[0]["split_port_map"]

        out = Path(td) / "tb"
        em = emit_protocol_testbench(str(f), str(out), "ddr3")
        assert em["ok"], str(em.get("error"))[:250]
        tb = Path(em["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        # 实例连线必须是真实端口名
        for pin in ("mem_ddr3_ck_p", "mem_ddr3_cke", "mem_ddr3_cs_n",
                    "mem_ddr3_ba", "mem_ddr3_addr", "mem_ddr3_dq_in",
                    "mem_ddr3_dq_out", "mem_ddr3_rd_valid"):
            assert f".{pin}(" in tb, (pin, tb[:800])
        # 不能残留硬编码的旧拼写作为 pin 名
        assert ".ddr3_ck_p(" not in tb, tb[:800]
        assert ".ddr3_dq_out(" not in tb, tb[:800]

def test_bind_order_is_derived_from_canonical_declarations() -> None:
    """Extensibility: declaring a CanonInterface must be enough to make it bind.

    `_BIND_ORDER` used to be a literal tuple inside classify_module, i.e. TWO
    hand-maintained lists (CANONICAL_INTERFACES + the priority tuple) that had to
    agree.  Adding a protocol to one and forgetting the other would silently make
    it undetectable.

    `bind_order()` now derives the sequence: explicit priority first (most
    specific buses first, otherwise HBM/PCIe AXI-style channels get eaten by
    axi4_lite), then any remaining declared protocol appended.
    """
    print("coverage: bind order derived from canonical declarations")
    from fpga_toolbox.canonical_iface import (
        _BIND_PRIORITY,
        CANONICAL_INTERFACES,
        CanonInterface,
        CanonSignal,
        bind_order,
    )

    order = bind_order()
    # every declared protocol appears exactly once
    assert set(order) == set(CANONICAL_INTERFACES), (order, sorted(CANONICAL_INTERFACES))
    assert len(order) == len(set(order)), order
    # declared priority protocols keep their relative order
    declared = [p for p in _BIND_PRIORITY if p in CANONICAL_INTERFACES]
    assert list(order[:len(declared)]) == declared, (order, declared)
    # specificity: prefixed memory buses must precede the generic AXI/APB ones
    for specific in ("hbm", "pcie", "ddr5", "ddr4", "ddr3"):
        if specific in order and "axi4_lite" in order:
            assert order.index(specific) < order.index("axi4_lite"), (specific, order)

    # a dynamically declared protocol becomes bindable without touching the
    # priority tuple -- this is the extensibility guarantee
    probe = "unit_probe_bus"
    assert probe not in CANONICAL_INTERFACES
    CANONICAL_INTERFACES[probe] = CanonInterface(
        protocol=probe, min_signals=2,
        signals=(CanonSignal("pclk_o", "out"), CanonSignal("pdata_o", "out")))
    try:
        assert probe in bind_order(), bind_order()
    finally:
        del CANONICAL_INTERFACES[probe]

    # DDR5 is declared and detected, but has no TB template: emitting must say so
    # explicitly instead of silently mis-using the DDR4 template.
    ddr5 = (
        "module ddr5_ctrl (\n"
        "  input  logic        clk,\n  input  logic rst_n,\n"
        "  output logic        ddr5_ck_t,\n  output logic ddr5_ck_c,\n"
        "  output logic        ddr5_cs_n,\n  output logic ddr5_act_n,\n"
        "  output logic [1:0]  ddr5_ca,\n  output logic [2:0] ddr5_ba,\n"
        "  output logic [1:0]  ddr5_bg,\n  inout  wire [31:0] ddr5_dq,\n"
        "  output logic [3:0]  ddr5_dqs_t,\n  output logic [3:0] ddr5_dqs_c,\n"
        "  output logic        ddr5_rd_valid\n"
        ");\nendmodule\n")
    from fpga_toolbox.protocol_tb_emitter import (
        SUPPORTED_MULTI_PROTOCOLS,
    )
    from fpga_toolbox.registry import call_tool
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "ddr5.sv"
        f.write_text(ddr5, encoding="utf-8")
        ex = call_tool("extract_interfaces", {"rtl_file": str(f)})
        assert ex["ok"], str(ex.get("error"))[:200]
        mod = ex["data"]["modules"][0]
        ifaces = mod["interfaces"]
        assert len(ifaces) == 1, [(i["protocol"], len(i["port_map"])) for i in ifaces]
        # one declaration was enough: 11 signals bound, nothing out of scope
        assert ifaces[0]["protocol"] == "ddr5", ifaces[0]["protocol"]
        assert len(ifaces[0]["port_map"]) == 11, sorted(ifaces[0]["port_map"])
        assert mod["out_of_scope_ports"] == [], mod["out_of_scope_ports"]
        # ddr5 此前只在 canonical_iface 里（分类器能认、emitter 无模板）。
        # 本轮补齐了 ddr5 原生模板，因此断言反转为「已支持」。
        assert "ddr5" in SUPPORTED_MULTI_PROTOCOLS, SUPPORTED_MULTI_PROTOCOLS
        em = emit_protocol_testbench(str(f), str(Path(td) / "tb"), None)
        assert em["ok"] is True, str(em.get("error"))[:300]
        assert em["data"]["protocol"] == "ddr5", em["data"]["protocol"]
        # 生成物必须真的按 ddr5 语义命名（cable: tb_<mod>_ddr5）
        assert em["data"]["artifacts"]["sv_testbench"].endswith("_ddr5.sv")

def test_draft_yaml_exports_all_protocols_and_validates() -> None:
    """Defect 179/180: draft 只导出 AXI/APB，且非 AXI 契约无法通过校验。

    179: `emit_draft_yaml` 的生成逻辑只认 `axi4_lite`（含 full AXI4）与 `apb`
    两种接口。一个 DDR3 控制器的 draft 里**没有 ddr3 段**，反而因为
    `axil or not apb` 对「两者都没有」也成立，被塞进
    `axi4_lite: {port_map: {}}` —— 凭空谎报了一种不存在的总线，下游按它选
    模板就会用错协议。现在把所有已识别接口按协议名导出（含
    split_port_map / pcie_gen 等），只在真的没有任何接口时保留 AXI 占位段。

    180: `validate_contract` 里 `has_axil = isinstance(axil_raw, dict) or
    not has_apb`，对既非 AXI 也非 APB 的设备（纯 DDR3/HBM/PCIe）成立，于是强行
    进入 AXI 校验并报 17 条 `E001 Missing required 'awaddr'`，把合法契约判死。
    """
    print("coverage: draft yaml exports all protocols and validates")
    import yaml

    from fpga_toolbox.registry import call_tool

    ref = Path(__file__).resolve().parent.parent / "examples" / "multi_bus_ref"
    expects = {
        "apb_slave.sv": "apb",
        "axi4_full_slave.sv": "axi4_lite",
        "ahb_lite_slave.sv": "ahb_lite",
        "ddr3_mem_ctrl.sv": "ddr3",
        "ddr4_mem_ctrl.sv": "ddr4",
        "hbm_pc_ctrl.sv": "hbm",
        "pcie_tlp_ep.sv": "pcie",
    }
    _META = {"module_name", "rtl_files", "parameters", "clock", "reset",
             "out_of_scope_ports", "instance_hierarchy"}

    with tempfile.TemporaryDirectory() as td:
        for fname, proto in expects.items():
            out = Path(td) / f"{fname}.yaml"
            r = call_tool("extract_interfaces", {
                "rtl_file": str(ref / fname), "emit_draft_yaml": str(out)})
            assert r["ok"], (fname, str(r.get("error"))[:200])
            boundary = yaml.safe_load(out.read_text(encoding="utf-8"))["boundary"]
            buses = [k for k in boundary if k not in _META]
            # 只导出真实存在的总线，不凭空造 axi4_lite
            assert buses == [proto], (fname, buses)
            # 非 AXI/APB 协议必须带完整 port_map（不是空 dict）
            if proto not in ("axi4_lite", "apb"):
                assert boundary[proto]["port_map"], (fname, boundary[proto])
            # 且这份 draft 必须能通过自家校验
            v = call_tool("validate_contract", {
                "spec_path": str(out), "rtl_file": str(ref / fname)})
            assert v["ok"], (fname, str(v.get("error"))[:200],
                             [d["code"] for d in (v.get("diagnostics") or [])][:5])

        # DDR3 draft 要带双向拆分信息，下游才知道 dq 的两半
        d3 = Path(td) / "ddr3_mem_ctrl.sv.yaml"
        b3 = yaml.safe_load(d3.read_text(encoding="utf-8"))["boundary"]
        assert b3["ddr3"]["split_port_map"]["dq"] == {
            "in": "ddr3_dq_in", "out": "ddr3_dq_out"}, b3["ddr3"]

        # 完全没有总线时仍保留 AXI 占位段（契约层需要它作为统一出口）
        nobus = Path(td) / "nobus.sv"
        nobus.write_text(
            "module nobus (input logic clk, input logic rst_n,\n"
            "  input logic [7:0] din, output logic [7:0] dout);\n"
            "  assign dout = din;\nendmodule\n", encoding="utf-8")
        nout = Path(td) / "nobus.yaml"
        rn = call_tool("extract_interfaces", {
            "rtl_file": str(nobus), "emit_draft_yaml": str(nout)})
        assert rn["ok"], str(rn.get("error"))[:200]
        bn = yaml.safe_load(nout.read_text(encoding="utf-8"))["boundary"]
        assert "axi4_lite" in bn, [k for k in bn if k not in _META]


def main() -> int:
    test_all_11_protocol_classifiers_and_clean_pt_registry()
    test_iface_direction_prefix_and_ahb_ready_split()
    test_secondary_clock_names_are_recognised()
    test_secondary_reset_names_are_recognised()
    test_direction_suffixed_bus_ports_are_classified()
    test_canonical_interface_binding_engine()
    test_nonstandard_prefix_dut_ports_are_bound_and_wired()
    test_bind_order_is_derived_from_canonical_declarations()
    test_draft_yaml_exports_all_protocols_and_validates()
    print("ALL test_iface_binding_coverage.py TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
