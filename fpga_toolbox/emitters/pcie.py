"""PCIe TLP 端点测试台模板（Gen3/4/5 × x1/x4/x16 矩阵）

从原 ``protocol_tb_emitter.py`` 拆出；模板本体逐字搬移，只补 import。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .common import (
    _pm,
)

_PCIE_GENS = (3, 4, 5)
_PCIE_LANES = (1, 4, 16)


def _pcie_dw_for_lanes(lanes: int) -> int:
    return 512 if lanes == 16 else (128 if lanes == 4 else 64)

def _emit_pcie_tb(
    mod: str,
    clk: str,
    rst: str,
    iface: Optional[Dict[str, Any]] = None,
    *,
    dut_params: Optional[Dict[str, int]] = None,
    port_widths: Optional[Dict[str, int]] = None,
) -> Tuple[str, List[str], bool]:
    """生成 PCIe TLP endpoint 的 TB，返回 (源码, suites, 是否走 3x3 配置矩阵)。

    旧模板把三件事都写死了，DUT 稍有不同就产出**编译不过**的 TB 却仍报 ok=True：

    1. 无条件实例化 ``dut #(.GEN(GEN), .LANES(LANES), .DW(DW))``。固定位宽的
       PCIe endpoint 不声明这些 parameter（很常见：cfg 直接 assign 常量），
       Verilator 报 ``Parameter not found: 'GEN'`` → 四个 ``%Error`` 全部重试失败。
    2. ``pcie_cfg_speed`` / ``pcie_cfg_lanes`` 的 TB 网线写死 ``[3:0]`` / ``[5:0]``。
       DUT 用 8 位 cfg 端口时生成 ``WIDTHEXPAND``，而 Verilator 默认警告即错误 →
       构建失败（实测 2 条 warning 直接 Exiting）。
    3. ``DW`` 恒由 ``LANES`` 推导；DUT 未声明 DW 参数时等于覆盖一个不存在的参数，
       即使声明了但 tdata 位宽不随它变，也会让 TB 网线与 DUT 端口位宽不一致。

    正确做法：参数覆盖只对 DUT **真实声明**的参数发出；网线位宽取 RTL 真实端口
    位宽；只有当 GEN/LANES 都声明时才跑 3x3 矩阵，且仅在 DW 确实「映射 LANES →
    tdata 位宽」时把 DW 一起覆盖（否则固定真实位宽，矩阵只变链路配置）。
    """
    params: Dict[str, int] = dict(dut_params or {})
    widths: Dict[str, int] = dict(port_widths or {})
    tx_data = _pm(iface, "tx_tdata", _pm(iface, "tx_data", "pcie_tx_tdata"))
    rx_data = _pm(iface, "rx_tdata", _pm(iface, "rx_data", "pcie_rx_tdata"))
    spd = _pm(iface, "speed", "pcie_cfg_speed")
    lns = _pm(iface, "lanes", "pcie_cfg_lanes")
    # 其余 PCIe 端口名也按 DUT 真实名字连线，模板里不再出现硬编码的
    # `pcie_tx_tvalid` 之类字面量（DUT 可能用 tl_/ep_ 等前缀）。
    tx_valid_n = _pm(iface, "tx_tvalid", "pcie_tx_tvalid")
    tx_ready_n = _pm(iface, "tx_tready", "pcie_tx_tready")
    tx_last_n = _pm(iface, "tx_tlast", "pcie_tx_tlast")
    rx_valid_n = _pm(iface, "rx_tvalid", "pcie_rx_tvalid")
    rx_ready_n = _pm(iface, "rx_tready", "pcie_rx_tready")
    rx_last_n = _pm(iface, "rx_tlast", "pcie_rx_tlast")
    link_up_n = _pm(iface, "link_up", "pcie_link_up")

    real_dw = int(widths.get(rx_data) or widths.get(tx_data) or 0)
    if real_dw <= 0:
        real_dw = _pcie_dw_for_lanes(int(params.get("LANES") or 4))
    sw = int(widths.get(spd) or 4)
    lw = int(widths.get(lns) or 6)

    has_gen = "GEN" in params
    has_lanes = "LANES" in params
    has_dw = "DW" in params
    matrix = has_gen and has_lanes

    # cfg_speed / cfg_lanes 的位宽必须能表示要断言的配置值。
    #
    # 旧实现的期望值是 `{sw}'(GEN)` / `{lw}'(LANES)` —— **先截断再比较**。
    # 若 DUT 的 cfg_lanes 只有 5 位而 LANES=16，TB 期望 `5'(16) == 0`，DUT 内部
    # 若是 `assign cfg_lanes = 5'(LANES)` 也得到 0，两边"一致"于是判 PASS：
    # 声称验证了 x16，实际验的是 lanes=0。这是**假通过**，比编译失败隐蔽得多
    # （实测 5 位 cfg_lanes 的 DUT 9 个 suite 全 PASS）。
    #
    # 正确做法：期望值用**真实配置值**、不截断（`{lns} !== {lw}'(LANES)` 改成
    # 与整型 LANES 比较）。装不下时仿真会诚实地 FAIL，而不是自欺欺人地通过。
    _cfg_overflow: List[str] = []
    for _g in _PCIE_GENS:
        if _g >= (1 << sw):
            _cfg_overflow.append(f"Gen{_g} (cfg_speed is {sw}-bit)")
    for _n in _PCIE_LANES:
        if _n >= (1 << lw):
            _cfg_overflow.append(f"x{_n} (cfg_lanes is {lw}-bit)")
    # DW 是否真的「参数化 tdata 位宽」：默认 LANES 推导值必须等于真实端口位宽。
    dw_is_live = has_dw and matrix and real_dw == _pcie_dw_for_lanes(int(params["LANES"]))

    overrides: List[str] = []
    if matrix:
        overrides += [".GEN(GEN)", ".LANES(LANES)"]
        if dw_is_live:
            overrides.append(".DW(DW)")
    pin_conn = (" #(" + ", ".join(overrides) + ")") if overrides else ""

    if matrix:
        tb_header = (
            f"module tb_{mod}_pcie #(\n"
            "  parameter int GEN   = 4,\n"
            "  parameter int LANES = 4\n"
            ");\n"
            + (
                "  localparam int DW = (LANES == 16) ? 512 : ((LANES == 4) ? 128 : 64);\n"
                if dw_is_live
                else f"  localparam int DW = {real_dw};  // RTL 实际 tdata 位宽（不随 LANES 变）\n"
            )
        )
        # 把**两端都零扩展**到同一宽度再比较，避免 NEQCASE 的宽度告警（旧版
        # `$unsigned(port) !== 32 位整型` 会报 WIDTHEXPAND）。
        # 语义：先判断 LANES 能否装进 cfg_lanes 的位宽；装不下就是"该配置在这颗
        # DUT 上不可表示"——直接判 FAIL，而不是像旧实现那样两边同时截断成 0
        # 互相印证成 PASS（实测 5 位 cfg_lanes 的 DUT 9 个 suite 全 PASS）。
        _cmp_w = max(sw, lw, 32)
        link_expect = (
            f"!pcie_link_up"
            f" || ({sw} <= 31 && GEN >= (1 << {sw}))"
            f" || ({lw} <= 31 && LANES >= (1 << {lw}))"
            f" || {sw}'($unsigned({spd})) !== {sw}'(GEN % (1 << {sw}))"
            f" || {lw}'($unsigned({lns})) !== {lw}'(LANES % (1 << {lw}))"
        )
        del _cmp_w
        link_expect_note = (
            f"  // NOTE: cfg_speed={sw}-bit, cfg_lanes={lw}-bit; comparisons use "
            f"the TRUE GEN/LANES value (mod port range) so unrepresentable "
            f"configs fail instead of silently matching a truncated value.\n"
        )
        link_exp_txt = 'up=1,Gen%0d,x%0d'
        link_got_fmt = "up=%0b,Gen=%0d,x%0d"  # 参数见下方 link_args
        cid = "PCIE_G%0dX%0d_001"
        proto_txt = "PCIe-Gen%0d-x%0d"
        summary = (
            '    if (errs == 0) begin $display("PASS: pcie Gen%0d x%0d (DW=%0d) '
            'MWr/MRd/CplD passed", GEN, LANES, DW); $finish; end\n'
            '    else begin $display("FAIL: pcie Gen%0d x%0d %0d errors", GEN, LANES, errs); $fatal(1); end\n'
        )
        suites = [f"pcie_g{g}x{n}" for g in _PCIE_GENS for n in _PCIE_LANES]
    else:
        tb_header = (
            f"module tb_{mod}_pcie;\n"
            f"  localparam int DW = {real_dw};  // RTL 实际 tdata 位宽\n"
        )
        # 固定配置 DUT：静态无从得知具体 Gen/lane 值，但对「链路没起来」和
        # 「Gen=0 / lanes 非法（不是 1/4/16）」仍是确定错误，退化为合法性检查。
        link_expect = (
            f"!pcie_link_up || {spd} < 1 || {spd} > 5\n"
            f"        || !({lns} == 1 || {lns} == 4 || {lns} == 16)"
        )
        link_exp_txt = "up=1,1<=Gen<=5,lanes in {1,4,16}"
        link_expect_note = ""
        link_got_fmt = "up=%0b,Gen=%0d,x%0d"
        cid = "PCIE_LINK_001"
        proto_txt = "PCIe-fixed"
        summary = (
            '    if (errs == 0) begin $display("PASS: pcie fixed config (DW=%0d) '
            'MWr/MRd/CplD passed", DW); $finish; end\n'
            '    else begin $display("FAIL: pcie fixed config %0d errors", errs); $fatal(1); end\n'
        )
        suites = ["pcie"]

    # CHECK id 的格式化参数：矩阵模式带 Gen/LANES，固定模式无。
    if matrix:
        link_args = "GEN, LANES, GEN, LANES, DW, GEN, LANES, pcie_link_up, " + spd + ", " + lns
        tlp_id = "PCIE_G%0dX%0d_002"
        tlp_args = f"GEN, LANES, GEN, LANES, {tx_data}[63:0], pcie_tx_tvalid, pcie_tx_tlast"
        tlp_proto = "PCIe-Gen%0d-x%0d"
    else:
        link_args = "DW, pcie_link_up, " + spd + ", " + lns
        tlp_id = "PCIE_TLP_002"
        tlp_args = f"{tx_data}[63:0], pcie_tx_tvalid, pcie_tx_tlast"
        tlp_proto = "PCIe-fixed"

    link_check = (
        link_expect_note
        + f"    if ({link_expect}) begin\n"
        f'      $display("[CHECK] id={cid} | proto={proto_txt} | cat=link_cfg | '
        f'tx=LTSSM link_up + cfg check (DW=%0d) | exp={link_exp_txt} | got={link_got_fmt} | status=FAIL",\n'
        f"               {link_args});\n"
        "      errs++;\n"
        "    end else begin\n"
        f'      $display("[CHECK] id={cid} | proto={proto_txt} | cat=link_cfg | '
        f'tx=LTSSM link_up + cfg check (DW=%0d) | exp={link_exp_txt} | got={link_got_fmt} | status=PASS",\n'
        f"               {link_args});\n"
        "    end\n"
    )

    tlp_check = (
        f"    if (!pcie_tx_tvalid || !pcie_tx_tlast || {tx_data}[63:0] !== 64'h4A3C001055AA1234) begin\n"
        f'      $display("[CHECK] id={tlp_id} | proto={tlp_proto} | cat=tlp_mwr_mrd_cpld | '
        "tx=MWr(0x0010,0x55AA1234)+MRd(tag=0x3C) | exp=CplD(0x4A3C001055AA1234) | "
        'got=0x%016h(v=%0b,last=%0b) | status=FAIL",\n'
        f"               {tlp_args});\n"
        "      errs++;\n"
        "    end else begin\n"
        f'      $display("[CHECK] id={tlp_id} | proto={tlp_proto} | cat=tlp_mwr_mrd_cpld | '
        "tx=MWr(0x0010,0x55AA1234)+MRd(tag=0x3C) | exp=CplD(0x4A3C001055AA1234) | "
        'got=0x%016h(v=%0b,last=%0b) | status=PASS",\n'
        f"               {tlp_args});\n"
        "    end\n"
    )

    body = (
        f"\n"
        f"  logic          {clk} = 0, {rst} = 0;\n"
        "  logic          pcie_rx_tvalid = 0, pcie_rx_tready, pcie_rx_tlast = 0;\n"
        f"  logic [DW-1:0] {rx_data} = '0, {tx_data};\n"
        "  logic          pcie_tx_tvalid, pcie_tx_tready = 1, pcie_tx_tlast;\n"
        "  logic          pcie_link_up;\n"
        f"  logic [{sw - 1}:0] {spd};\n"
        f"  logic [{lw - 1}:0] {lns};\n"
        "  int            errs = 0;\n"
        "\n"
        f"  {mod}{pin_conn} dut (\n"
        f"    .{clk}({clk}), .{rst}({rst}),\n"
        f"    .{tx_valid_n}(pcie_tx_tvalid), .{tx_ready_n}(pcie_tx_tready),\n"
        f"    .{tx_data}({tx_data}), .{tx_last_n}(pcie_tx_tlast),\n"
        f"    .{rx_valid_n}(pcie_rx_tvalid), .{rx_ready_n}(pcie_rx_tready),\n"
        f"    .{rx_data}({rx_data}), .{rx_last_n}(pcie_rx_tlast),\n"
        f"    .{link_up_n}(pcie_link_up), .{spd}({spd}), .{lns}({lns})\n"
        "  );\n"
        f"  always #5 {clk} = ~{clk};\n"
        "\n"
        "  initial begin\n"
        f"    repeat (3) @(posedge {clk});\n"
        f"    {rst} = 1;\n"
        f"    @(posedge {clk}); #1;\n"
        + link_check
        + f"    pcie_rx_tvalid = 1; pcie_rx_tlast = 1;\n"
        f"    {rx_data} = '0; {rx_data}[63:0] = 64'h4007001055AA1234;\n"
        f"    @(posedge {clk}); #1;\n"
        f"    {rx_data}[63:0] = 64'h003C001000000000;\n"
        f"    @(posedge {clk}); #1;\n"
        "    pcie_rx_tvalid = 0; pcie_rx_tlast = 0;\n"
        + tlp_check
        + summary
        + "  end\n"
        "endmodule\n"
    )

    head = (
        "`timescale 1ns/1ps\n"
        "// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=pcie, "
        + ("Gen3/4/5 x x1/x4/x16 matrix)" if matrix else "fixed configuration)")
        + "\n"
        + tb_header
    )
    return head + body, suites, matrix
