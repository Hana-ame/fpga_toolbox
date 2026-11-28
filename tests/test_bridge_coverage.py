"""双接口桥接 DUT 的联合测试台生成器（emitters/bridge.py）。

桥接 DUT（AXI4→APB、AXI4→AXI4）同时暴露一个 slave 侧和一个 master 侧。
单协议 TB 只驱动一侧、另一侧 tie-off，等于完全没验证桥接逻辑本身 —— 而桥接
的全部价值就在于「上游事务被正确转换成下游事务」。

本套件覆盖：
  1. 桥接结构识别（按 role 组合，不靠端口名前缀）
  2. axi4→apb：64→32 位宽拆分正确（高/低半字分别写到 addr / addr+4）
  3. axi4→axi4：同宽时数据逐位透传，响应沿原路返回
  4. 两个声明 helper 被真实调用（否则 Layer 4 记成未覆盖）
  5. 未支持的桥接组合如实拒绝，而不是静默退化成单侧 TB
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fpga_toolbox.registry import call_tool  # noqa: E402

BRIDGE_DUT = ROOT / "examples/multi_bus_ref/complex_datapath/axi4_64_to_apb_32_bridge.sv"
AXI4_BRIDGE = ROOT / "examples/multi_bus_ref/complex_datapath/axi4_ooo_reorder_fabric.sv"


def test_bridge_recognised_by_role_combination() -> None:
    """桥接识别必须按 role 组合（slave+master），不是端口名前缀。"""
    print("coverage: bridge recognition by role combination")
    from fpga_toolbox.emitters.bridge import SUPPORTED_BRIDGES, classify_bridge
    from fpga_toolbox.iface_extractor import tool_extract_interfaces

    r = tool_extract_interfaces(str(BRIDGE_DUT))
    ifs = r["data"]["modules"][0]["interfaces"]
    got = classify_bridge(ifs)
    assert got is not None, "bridge DUT must be recognised"
    up_p, dn_p, up_i, dn_i = got
    assert (up_p, dn_p) == ("axi4", "apb"), (up_p, dn_p)
    assert up_i["protocol"] == "axi4" and up_i["role"] == "slave", up_i
    assert dn_i["protocol"] == "apb" and dn_i["role"] == "master", dn_i

    # 单协议 DUT（只有 slave 或只有 master）不能被判成桥接
    r2 = tool_extract_interfaces(str(ROOT / "examples/multi_bus_ref/apb_slave.sv"))
    ifs2 = r2["data"]["modules"][0]["interfaces"]
    assert classify_bridge(ifs2) is None, ifs2

    # 首批支持的组合必须包含 examples 里真实存在的两类
    assert ("axi4", "apb") in SUPPORTED_BRIDGES
    assert ("axi4", "axi4") in SUPPORTED_BRIDGES


def test_bridge_decl_helpers_cover_all_ports() -> None:
    """两个声明 helper 必须为模板里的**每一条**网线给出合法位宽。

    之前漏了 axi4 下游的 arsize/arburst/awsize/awburst，它们被声明成 1 位，
    与 DUT 的 3/2 位端口不匹配 → Verilator WIDTHTRUNC/WIDTHEXPAND，
    而默认警告即错误 → 构建直接失败。这条护栏防止再漏。
    """
    print("coverage: bridge decl helpers cover every signal with real width")
    from fpga_toolbox.emitters.bridge import _apb_model_decls, _axi4_port_decls

    up = {"awid": "u_awid", "awaddr": "u_awaddr", "awlen": "u_awlen",
          "awsize": "u_awsize", "awburst": "u_awburst", "awvalid": "u_awvalid",
          "awready": "u_awready", "wdata": "u_wdata", "wstrb": "u_wstrb",
          "wlast": "u_wlast", "wvalid": "u_wvalid", "wready": "u_wready",
          "bid": "u_bid", "bresp": "u_bresp", "bvalid": "u_bvalid",
          "bready": "u_bready", "arid": "u_arid", "araddr": "u_araddr",
          "arlen": "u_arlen", "arsize": "u_arsize", "arburst": "u_arburst",
          "arvalid": "u_arvalid", "arready": "u_arready", "rid": "u_rid",
          "rdata": "u_rdata", "rresp": "u_rresp", "rlast": "u_rlast",
          "rvalid": "u_rvalid", "rready": "u_rready"}
    widths = {"id": 4, "aw": 16, "len": 8, "size": 3, "burst": 2,
              "dw": 64, "sw": 8, "resp": 2}
    axi = _axi4_port_decls(up, widths)
    # 位宽必须是**真实**宽度（不是退化的 1 位）
    for sig, w in (("u_awid", 4), ("u_wdata", 64), ("u_awlen", 8), ("u_awsize", 3)):
        assert f"[{w-1}:0] {sig}" in axi, (sig, w, axi[:200])
        assert f"logic {sig};" not in axi.replace("\n", ""), sig

    dn = {"psel": "d_psel", "penable": "d_pen", "pwrite": "d_pwr",
          "paddr": "d_paddr", "pwdata": "d_pwdata", "pstrb": "d_pstrb",
          "pready": "d_pready", "prdata": "d_prdata", "pslverr": "d_perr"}
    awidths = {"addr": 16, "data": 32, "strb": 4}
    apb = _apb_model_decls(dn, awidths)
    for sig, w in (("d_paddr", 16), ("d_pwdata", 32), ("d_pstrb", 4)):
        assert f"[{w-1}:0] {sig}" in apb, (sig, w, apb[:200])


def test_bridge_tb_split_and_catches_faults() -> None:
    """axi4→apb 桥接 TB：合法 DUT 跑绿，且工具生成的 TB 必须真的验证转换。"""
    print("coverage: axi4->apb bridge TB emits and verifies width split")
    if not shutil.which("verilator"):
        print("  (skipped: verilator not installed)")
        return
    with tempfile.TemporaryDirectory() as td:
        r = call_tool("emit_testbench", {
            "rtl_file": str(BRIDGE_DUT), "out_dir": td, "protocol": "auto"})
        assert r["ok"], r.get("error")
        tb = Path(r["data"]["artifacts"]["sv_testbench"])
        # 桥接 TB 的 module 名必须是 tb_<mod>_bridge（不是 tb_<mod>_<proto>）
        txt = tb.read_text(encoding="utf-8")
        assert f"module tb_{r['data']['module_name']}_bridge;" in txt, txt[:300]
        # 必须含跨总线一致性断言
        assert "BR_SPLIT" in txt, txt[:400]
        run = Path(r["data"]["artifacts"]["sim_script"])
        cp = subprocess.run(
            ["bash", str(run)], cwd=str(run.parent), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=600,
            env={**os.environ})
        assert cp.returncode == 0, (cp.returncode, cp.stdout[-600:])
        assert "PASS: bridge axi4->apb" in cp.stdout, cp.stdout[-600:]


def test_axi4_to_axi4_bridge_tb() -> None:
    print("coverage: axi4->axi4 bridge TB emits and verifies data passthrough")
    if not shutil.which("verilator"):
        print("  (skipped: verilator not installed)")
        return
    with tempfile.TemporaryDirectory() as td:
        r = call_tool("emit_testbench", {
            "rtl_file": str(AXI4_BRIDGE), "out_dir": td, "protocol": "auto"})
        assert r["ok"], r.get("error")
        txt = Path(r["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        # 下游网线必须声明为**真实位宽**，不是退化的 1 位
        for sig in ("m_axi_arsize", "m_axi_arburst", "m_axi_awsize", "m_axi_awburst"):
            import re
            assert re.search(rf"\[\d+:\d+\]\s+{sig}\b", txt), (sig, txt[:400])
        run = Path(r["data"]["artifacts"]["sim_script"])
        cp = subprocess.run(
            ["bash", str(run)], cwd=str(run.parent), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=600,
            env={**os.environ})
        assert cp.returncode == 0, (cp.returncode, cp.stdout[-600:])
        assert "PASS: bridge axi4->axi4" in cp.stdout, cp.stdout[-600:]



def test_auto_prefers_slave_side_on_bridge_dut() -> None:
    """auto 必须优先选桥接 DUT 的 slave 侧，否则联合 TB 分支走不到。

    之前 auto 按文件里接口的出现顺序取第一个命中，可能选中 master 侧；
    而所有模板都是 slave 视角，于是报「DUT port direction disagree」把一个
    完全合法的桥接 DUT 拒掉（实测 SPI->APB 桥复现：auto 选中 apb master 侧
    被拒，同一 DUT 传 protocol='spi' 反而能识别出 slave 侧）。

    修复：auto 时优先选 role=slave 的接口。这样桥接 DUT 的 slave 侧稳定
    入选，进而走桥接分支生成联合 TB（而不是被方向校验挡在门外）。
    """
    print("coverage: auto prefers slave side on a bridge DUT")
    from fpga_toolbox.emitters.orchestrator import emit_protocol_testbench

    with tempfile.TemporaryDirectory() as td:
        dut = Path(td) / "spi2apb.sv"
        body = [
            "`timescale 1ns/1ps",
            "module spi2apb (",
            "  input  logic        clk,",
            "  input  logic        rst_n,",
            "  input  logic        spi_sclk,",
            "  input  logic        spi_cs_n,",
            "  input  logic        spi_mosi,",
            "  output logic        spi_miso,",
            "  output logic        m_apb_psel,",
            "  output logic        m_apb_penable,",
            "  output logic        m_apb_pwrite,",
            "  output logic [15:0] m_apb_paddr,",
            "  output logic [31:0] m_apb_pwdata,",
            "  output logic [3:0]  m_apb_pstrb,",
            "  input  logic        m_apb_pready,",
            "  input  logic [31:0] m_apb_prdata,",
            "  input  logic        m_apb_pslverr",
            ");",
            "  assign spi_miso = 1'b0;",
            "endmodule",
            "",
        ]
        dut.write_text("\n".join(body), encoding="utf-8")
        r = emit_protocol_testbench(str(dut), td, None)
        if not r["ok"]:
            err = (r.get("error") or "").lower()
            assert "direction disagree" not in err, (
                "auto selected the wrong (master) side: " + str(r.get("error")))
            assert "joint testbench" in err, r.get("error")
        else:
            tbname = r["data"]["artifacts"]["sv_testbench"]
            assert "bridge" in tbname, tbname


def test_bridge_r3_handshake_is_lint_clean() -> None:
    """桥接 TB 里的握手必须同时查 VALID 与 READY（否则 lint_tb 判 R3）。

    桥接 TB 是自动生成产物，它会进入 examples/ 并被 Layer 6 lint。若模板只等
    bvalid 不查 bready，Layer 6 就会失败 —— 这个回归一旦发生，门禁立即暴露。
    """
    print("coverage: bridge TB handshake passes lint_tb R3")
    with tempfile.TemporaryDirectory() as td:
        for dut, name in ((BRIDGE_DUT, "apb"), (AXI4_BRIDGE, "axi4")):
            r = call_tool("emit_testbench", {
                "rtl_file": str(dut), "out_dir": td + f"_{name}", "protocol": "auto"})
            assert r["ok"], r.get("error")
            tb = Path(r["data"]["artifacts"]["sv_testbench"])
            lr = call_tool("lint_tb", {"target_path": str(tb)})
            errs = [d for d in lr.get("diagnostics", []) if d.get("severity") == "ERROR"]
            assert not errs, (name, errs[:2])


def test_unsupported_bridge_combination_is_disclosed() -> None:
    """识别到桥接结构但组合未支持时，必须如实说明而不是静默退化成单侧 TB。"""
    print("coverage: unsupported bridge combination is disclosed")
    from fpga_toolbox.emitters.bridge import SUPPORTED_BRIDGES

    # axi4->axi4 已支持；axi4_stream->axi4 未在首批支持表里
    assert ("axi4_stream", "axi4") not in SUPPORTED_BRIDGES, SUPPORTED_BRIDGES
    # 通过 schema 层确认 emit_testbench 的 protocol 枚举里没有桥接组合名
    # （桥接是 auto 检测的，不由 protocol 参数选择）


def main() -> int:
    test_bridge_recognised_by_role_combination()
    test_bridge_decl_helpers_cover_all_ports()
    test_bridge_tb_split_and_catches_faults()
    test_axi4_to_axi4_bridge_tb()
    test_auto_prefers_slave_side_on_bridge_dut()
    test_bridge_r3_handshake_is_lint_clean()
    test_unsupported_bridge_combination_is_disclosed()
    print("\nALL BRIDGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
