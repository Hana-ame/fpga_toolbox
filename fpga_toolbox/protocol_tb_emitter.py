"""Multi-protocol SystemVerilog testbench & Verilator runner emitter.

Extends ``emit_testbench`` beyond AXI4-Lite / dual-side AXI4-Lite<->APB bridges to
natively generate compilable, lint-clean, self-checking SystemVerilog testbenches
and Verilator run scripts directly from ``extract_interfaces`` (slang AST) for:
  * apb        (APB3/4 slave: reset read, PSTRB write, RO, W1C, PSLVERR)
  * axi4       (AXI4-Full burst slave: INCR4/INCR8 + ID + RLAST + SLVERR)
  * ahb_lite   (AHB-Lite pipelined slave: RW/RO + HREADYOUT + HRESP error)
  * ddr3       (DDR3 controller: ACT/WR/RD CL=2 + multi-bank + PRE)
  * ddr4       (DDR4 controller: ACT_n + BG/BA 64-bit + DM_DBI_n byte mask)
  * hbm        (HBM 256-bit pseudo-channel: ID tracking + rready=0 backpressure)
  * pcie       (PCIe TLP endpoint: full 3x3 matrix Gen3/4/5 x x1/x4/x16)
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .core.types import err_result, ok_result
from .iface_extractor import tool_extract_interfaces

SUPPORTED_MULTI_PROTOCOLS = ("apb", "axi4", "ahb_lite", "ddr3", "ddr4", "hbm", "pcie")

# 各协议 testbench 模板**实际硬连**的端口。分类器只需若干个信号就能认出协议
# （APB 只需 4 个），但 TB 模板会连全部 9 个 —— 若 RTL 缺其中任何一个（如 APB3
# 无 pstrb、无 pslverr），生成出的 TB 会 `.pstrb(pstrb)` 连到不存在的端口，
# Verilator 报 PINNOTFOUND 编译失败，而工具却返回 ok=True（静默生成坏产物）。
# 因此生成前按此表校验，缺失即 fail-fast。
REQUIRED_TB_PORTS: Dict[str, Tuple[str, ...]] = {
    # APB 模板硬连全部 9 个信号（含 pstrb/pslverr）——虽然 APB3 允许它们缺失，
    # 但模板会连，所以缺了必须报错，而不是生成 PINNOTFOUND 的坏 TB。
    "apb": ("psel", "penable", "pwrite", "paddr", "pwdata", "pstrb", "pready", "prdata", "pslverr"),
    "axi4": (
        "awaddr", "awvalid", "awready", "wdata", "wstrb", "wvalid", "wready",
        "bresp", "bvalid", "bready", "araddr", "arvalid", "arready",
        "rdata", "rresp", "rvalid", "rready",
    ),
    "ahb_lite": ("hsel", "haddr", "htrans", "hwrite", "hsize", "hwdata", "hready", "hrdata", "hresp"),
    # ddr3/ddr4/hbm/pcie 的模板不用 port_map，直接按标准端口名连线并用 `.*`
    # 兜底。`.*` 只连接**TB 里已声明**的同名变量，所以 DUT 端口名一旦与模板
    # 声明不一致（如 DUT 用 `inout [15:0] ddr3_dq` 而模板声明的是
    # ddr3_dq_in/ddr3_dq_out），`.*` 会报 "Can't find definition of variable"
    # 编译失败，而工具仍返回 ok=True。这里按模板声明的实际端口名校验。
    # 注意 *_dq_in 必须一起校验：模板对 DUT 是**双向**语义 —— dq_in 由 TB 驱动
    # （写入数据），dq_out 由 DUT 驱动（读回数据）。旧表只列了 dq_out，于是
    # 一个没有 dq_in 端口的 DDR3 DUT 通过了生成前校验，产出的 TB 带
    # `.ddr3_dq_in(...)` 连线，Verilator 报 PINNOTFOUND 编译失败而工具报 ok=True
    # （实测复现）。ddr4 同理。
    "ddr3": (
        "ddr3_ck_p", "ddr3_ck_n", "ddr3_cke", "ddr3_cs_n", "ddr3_ras_n",
        "ddr3_cas_n", "ddr3_we_n", "ddr3_ba", "ddr3_addr",
        "ddr3_dq_in", "ddr3_dq_out", "ddr3_rd_valid",
    ),
    "ddr4": (
        "ddr4_ck_t", "ddr4_ck_c", "ddr4_act_n", "ddr4_bg", "ddr4_ba", "ddr4_adr",
        "ddr4_cs_n", "ddr4_odt", "ddr4_dq_in", "ddr4_dq_out",
        "ddr4_dm_dbi_n", "ddr4_rd_valid",
    ),
    "hbm": (
        "hbm_awid", "hbm_awaddr", "hbm_awvalid", "hbm_awready",
        "hbm_wdata", "hbm_wvalid", "hbm_wready",
        "hbm_bid", "hbm_bvalid", "hbm_bready",
        "hbm_arid", "hbm_araddr", "hbm_arvalid", "hbm_arready",
        "hbm_rid", "hbm_rdata", "hbm_rvalid", "hbm_rready",
    ),
    "pcie": (
        "pcie_tx_tvalid", "pcie_tx_tready", "pcie_tx_tdata", "pcie_tx_tlast",
        "pcie_rx_tvalid", "pcie_rx_tready", "pcie_rx_tdata", "pcie_rx_tlast",
        "pcie_link_up", "pcie_cfg_speed", "pcie_cfg_lanes",
    ),
}


# TB 模板对各端口的**期望方向**：TB 主动驱动的是 DUT 的输入，TB 读取作响应
# 的是 DUT 的输出。方向不匹配时不会编译失败（Verilator 只报 UNDRIVEN 之类），
# 但会在运行期永久挂起——例如 DUT 的 pready 若被写成 input，TB 的
# `do begin ... end while (!pready)` 永远等不到响应，仿真卡死。工具却报
# ok=True。这比编译失败更隐蔽：CI 表现为超时而不是失败。
EXPECTED_TB_DIR: Dict[str, Dict[str, str]] = {
    "apb": {
        "psel": "input", "penable": "input", "pwrite": "input", "paddr": "input",
        "pwdata": "input", "pstrb": "input",
        "pready": "output", "prdata": "output", "pslverr": "output",
    },
    "ahb_lite": {
        "hsel": "input", "haddr": "input", "htrans": "input", "hwrite": "input",
        "hsize": "input", "hwdata": "input",
        "hready": "output", "hrdata": "output", "hresp": "output",
    },
    "axi4": {
        "awaddr": "input", "awvalid": "input", "wdata": "input", "wstrb": "input",
        "wvalid": "input", "bready": "input", "araddr": "input", "arvalid": "input", "rready": "input",
        "awready": "output", "wready": "output", "bresp": "output", "bvalid": "output",
        "arready": "output", "rdata": "output", "rresp": "output", "rvalid": "output",
    },
}


def _wrong_dir_ports(
    proto: str, iface: Dict[str, Any], rtl_dir: Dict[str, str],
) -> List[str]:
    """返回期望方向与实际方向不符的端口（仅对有方向表的协议生效）。"""
    expected = EXPECTED_TB_DIR.get(proto)
    if not expected:
        return []
    bad: List[str] = []
    for canon, want in expected.items():
        mapped = _pm(iface, canon, canon)
        actual = rtl_dir.get(mapped)
        if actual is None or actual == "inout":
            continue  # inout 由 DUT 侧三态驱动，TB 端按 input/output 皆可接受
        # logic 输出在 SV 解析里可能记为 output，input 记 input
        if actual != want:
            bad.append(f"{canon}->{mapped} (expected DUT {want}, got {actual})")
    return bad


def _missing_tb_ports(proto: str, iface: Dict[str, Any], rtl_ports: List[str]) -> List[str]:
    """返回该协议 TB 模板需要、但 DUT 实际没有的端口名。

    优先用 port_map 的映射结果判断（emitter 就是按 port_map 连线），若 port_map
    没给某项则退回 RTL 端口名本身——两者都不存在就是真缺失。
    """
    required = REQUIRED_TB_PORTS.get(proto)
    if not required:
        return []
    port_set = {p.lower() for p in rtl_ports}
    missing: List[str] = []
    for canon in required:
        mapped = _pm(iface, canon, canon)
        if mapped.lower() not in port_set:
            missing.append(f"{canon}->{mapped}")
    return missing


def _pm(iface: Dict[str, Any], canon: str, default: str) -> str:
    pm = iface.get("port_map") or {}
    val = pm.get(canon)
    if isinstance(val, dict):
        return str(val.get("port") or default)
    return str(val or default)


def _pw(iface: Dict[str, Any], canon: str, default: int) -> int:
    """取 canon 信号在 **RTL 里真实声明的位宽**（port_details 由扫描 DUT 端口得到）。

    模板必须按真实端口位宽声明 TB 网线：写死位宽时 DUT 稍有不同就是
    WIDTHTRUNC / WIDTHEXPAND，而 Verilator 默认**警告即错误**，构建直接失败
    （实测 DDR3 8 位 dq：8'hBEEF 报 Too many digits；HBM 64 位 wdata 对 256 位
    模板报 WIDTHEXPAND）。工具却仍返回 ok=True —— DUT 一点没验到。
    """
    details = iface.get("port_details") or {}
    d = details.get(canon)
    if isinstance(d, dict):
        w = d.get("width")
        if isinstance(w, int) and w > 0:
            return w
    if canon in ("addr", "adr", "awaddr", "araddr"):
        aw = iface.get("addr_width")
        if isinstance(aw, int) and aw > 0:
            return aw
    if canon in ("dq", "wdata", "rdata"):
        dw = iface.get("data_width")
        if isinstance(dw, int) and dw > 0:
            return dw
    return default


def _emit_apb_tb(mod: str, clk: str, rst: str, iface: Dict[str, Any]) -> str:
    psel = _pm(iface, "psel", "psel")
    pen = _pm(iface, "penable", "penable")
    pwr = _pm(iface, "pwrite", "pwrite")
    paddr = _pm(iface, "paddr", "paddr")
    pwdata = _pm(iface, "pwdata", "pwdata")
    pstrb = _pm(iface, "pstrb", "pstrb")
    pready = _pm(iface, "pready", "pready")
    prdata = _pm(iface, "prdata", "prdata")
    pslverr = _pm(iface, "pslverr", "pslverr")
    aw = _pw(iface, "paddr", int(iface.get("addr_width") or 16))
    dw = _pw(iface, "pwdata", int(iface.get("data_width") or 32))
    # strobe 位宽必须取 **pstrb 端口真实位宽**，不能用 dw//8 推导：
    # APB 规范并不要求 PSTRB 的位宽 == DATA/8。实测 DUT 用 16 位 pwdata +
    # 4 位 pstrb（合法的按字节方案）时，TB 按 16//8 生成 [1:0] 网线，
    # 连线报 WIDTHEXPAND、构建失败（Verilator 警告即错误），而工具报 ok=True。
    sw = _pw(iface, "pstrb", max(1, dw // 8))
    # 测试向量必须随实际位宽缩放：旧模板写死 32 位的 8 位十六进制字面量，
    # 套到窄总线（如 dw=8）上会得到 `8'h12345678`——Verilator 报
    # WIDTHDIGITS/WIDTHTRUNC，数据被截断、断言值失真（测试仍能"跑通"，
    # 但验证的是被截断后的错误值）。这里按 dw 生成掩码与递进图案。
    dw_mask = (1 << dw) - 1
    pat_a = 0x12345678 & dw_mask
    pat_b = 0xAA00BB00 & dw_mask
    pat_c = 0xAA34BB78 & dw_mask
    pat_all = dw_mask
    pat_d = 0xDEAD0001 & dw_mask
    pat_w1c = 0x0000000F & dw_mask
    pat_w1c_exp = 0x000000F0 & dw_mask
    sw_mask = (1 << sw) - 1
    # 部分写掩码：原模板用 4'b1010（隔字节使能）验证「部分写只影响选中字节」。
    # 必须保留这个语义（改成全 1 会让 APB_003 的读回期望失效），只是按 sw 缩放。
    strb_alt = int("10" * ((sw + 1) // 2), 2) & sw_mask if sw > 1 else 1
    # 十六进制位数必须与 dw 匹配：`{dw}'h{val:08X}` 在 dw=8 时仍会写成
    # `8'h00000078`（8 个十六进制数字塞进 8 bit）→ 依旧 WIDTHDIGITS。
    dhex = max(1, (dw + 3) // 4)
    ahex = max(1, (aw + 3) // 4)
    # 非法地址用例：默认 0x40（APB 靶场惯例）。但窄地址总线（如 aw=4）根本装不下
    # 0x40——`4'h40` 本身超宽，Verilator 报 WIDTHDIGITS。此时退化为该地址空间内
    # 的「保留字」写法：取 aw 可表示的最大地址，并把期望 error 放宽为“不检查”，
    # 避免在窄总线上断言一个物理上不存在的越界地址。
    ill_addr, ill_check = 0x40, True
    if ill_addr > ((1 << aw) - 1):
        ill_addr, ill_check = (1 << aw) - 1, False
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=apb)
module tb_{mod}_apb;
  logic          {clk} = 0, {rst} = 0;
  logic          {psel} = 0, {pen} = 0, {pwr} = 0, {pready}, {pslverr};
  logic [{aw-1}:0] {paddr} = '0;
  logic [{dw-1}:0] {pwdata} = '0, {prdata};
  logic [{sw-1}:0] {pstrb} = '1;
  int            errs = 0;

  {mod} dut (
    .{clk}({clk}), .{rst}({rst}), .{psel}({psel}), .{pen}({pen}), .{pwr}({pwr}),
    .{paddr}({paddr}), .{pwdata}({pwdata}), .{pstrb}({pstrb}),
    .{pready}({pready}), .{prdata}({prdata}), .{pslverr}({pslverr})
  );
  always #5 {clk} = ~{clk};

  task automatic apb_tx(
    input string id, input string cat, input logic wr,
    input logic [{aw-1}:0] addr, input logic [{dw-1}:0] wdata, input logic [{sw-1}:0] strb,
    input logic [{dw-1}:0] exp_data, input logic exp_err
  );
    int waits = 0;
    @(posedge {clk}); #1;
    {psel} = 1; {pen} = 0; {pwr} = wr; {paddr} = addr; {pwdata} = wdata; {pstrb} = strb;
    @(posedge {clk}); #1;
    {pen} = 1;
    do begin @(posedge {clk}); #1; waits++; end while (!{pready});
    if ({pslverr} !== exp_err || (!wr && !exp_err && {prdata} !== exp_data)) begin
      $display("[CHECK] id=%s | proto=APB | cat=%s | tx=%s addr=0x%04h wdata=0x%08h strb=0x%1h waits=%0d | exp=0x%08h(err=%0b) | got=0x%08h(err=%0b) | status=FAIL",
               id, cat, wr ? "WR" : "RD", addr, wdata, strb, waits, exp_data, exp_err, {prdata}, {pslverr});
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=APB | cat=%s | tx=%s addr=0x%04h wdata=0x%08h strb=0x%1h waits=%0d | exp=0x%08h(err=%0b) | got=0x%08h(err=%0b) | status=PASS",
               id, cat, wr ? "WR" : "RD", addr, wdata, strb, waits, exp_data, exp_err, wr ? wdata : {prdata}, {pslverr});
    end
    {psel} = 0; {pen} = 0;
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    apb_tx("APB_001", "reset_read",  1'b0, {aw}'h{0:0{ahex}X}, {dw}'h0,        {sw}'h{sw_mask:X}, {dw}'h{pat_a:0{dhex}X}, 1'b0);
    apb_tx("APB_002", "pstrb_write", 1'b1, {aw}'h{0:0{ahex}X}, {dw}'h{pat_b:0{dhex}X}, {sw}'h{strb_alt:X}, {dw}'h{pat_b:0{dhex}X}, 1'b0);
    apb_tx("APB_003", "pstrb_read",  1'b0, {aw}'h{0:0{ahex}X}, {dw}'h0,        {sw}'h{sw_mask:X}, {dw}'h{pat_c:0{dhex}X}, 1'b0);
    apb_tx("APB_004", "ro_protect",  1'b1, {aw}'h{4:0{ahex}X}, {dw}'h{pat_all:0{dhex}X}, {sw}'h{sw_mask:X}, {dw}'h{pat_all:0{dhex}X}, 1'b0);
    apb_tx("APB_005", "ro_verify",   1'b0, {aw}'h{4:0{ahex}X}, {dw}'h0,        {sw}'h{sw_mask:X}, {dw}'h{pat_d:0{dhex}X}, 1'b0);
    apb_tx("APB_006", "w1c_clear",   1'b1, {aw}'h{8:0{ahex}X}, {dw}'h{pat_w1c:0{dhex}X}, {sw}'h{sw_mask:X}, {dw}'h{pat_w1c:0{dhex}X}, 1'b0);
    apb_tx("APB_007", "w1c_verify",  1'b0, {aw}'h{8:0{ahex}X}, {dw}'h0,        {sw}'h{sw_mask:X}, {dw}'h{pat_w1c_exp:0{dhex}X}, 1'b0);
    apb_tx("APB_008", "pslverr_ill", 1'b0, {aw}'h{ill_addr:0{ahex}X}, {dw}'h0,        {sw}'h{sw_mask:X}, {dw}'h0, 1'b{int(ill_check)});
    if (errs == 0) begin $display("PASS: apb all 8 checks passed"); $finish; end
    else begin $display("FAIL: apb %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_axi4_tb(mod: str, clk: str, rst: str, iface: Dict[str, Any]) -> str:
    """生成 AXI4-Full burst slave TB。

    ID / len / size / burst / resp 位宽都必须跟随 DUT 真实端口：旧模板把
    ``awid/bid/arid/rid`` 写死 ``[3:0]``、``awlen/arlen`` 写死 ``[7:0]``。
    AXI4 允许 ID 任意位宽、len 也可窄到 4 位，DUT 一不同宽就是 WIDTHEXPAND /
    WIDTHTRUNC —— 而 Verilator 默认警告即错误，构建失败却返回 ok=True
    （实测 8 位 ID：4 条 WIDTHEXPAND；4 位 len：2 条 WIDTHTRUNC）。

    测试向量也必须能装进实际位宽：ID 值按 ``id_w`` 截断、``beats-1`` 按
    ``len_w`` 收紧、地址/数据常量按 ``aw``/``dw`` 掩码、``$display`` 的十六进制
    位数同步 —— 否则窄总线上 ``8'h0200`` / ``16'hA000_0000`` 又是 WIDTHDIGITS。
    """
    aw = _pw(iface, "awaddr", int(iface.get("addr_width") or 16))
    dw = _pw(iface, "wdata", int(iface.get("data_width") or 32))
    # wstrb 位宽取端口真实值（AXI4 允许 WSTRB 位宽 != DATA/8，例如窄传输实现）
    sw = _pw(iface, "wstrb", max(1, dw // 8))
    id_w = _pw(iface, "awid", 4)
    len_w = _pw(iface, "awlen", 8)
    size_w = _pw(iface, "awsize", 3)
    burst_w = _pw(iface, "awburst", 2)
    resp_w = _pw(iface, "bresp", 2)
    ahex = max(1, (aw + 3) // 4)
    dhex = max(1, (dw + 3) // 4)
    idhex = max(1, (id_w + 3) // 4)
    aw_mask = (1 << aw) - 1
    dw_mask = (1 << dw) - 1
    id_mask = (1 << id_w) - 1
    resp_ok = 0 if resp_w <= 1 else 2  # SLVERR 编码；窄 resp 退化为最大码
    # ID / 地址 / 数据都要装进实际位宽（旧模板写死 4'd9 / 0x0200 / 0xA0000000）
    id_a, id_b, id_c = 3 & id_mask, 7 & id_mask, 9 & id_mask
    ad_a, ad_b = 0x0010 & aw_mask, 0x0040 & aw_mask
    ad_ill, ill_check = 0x0200, True
    if ad_ill > aw_mask:
        ad_ill, ill_check = aw_mask, False
    # len 字段是 beats-1，必须 <= 2**len_w - 1
    max_beats = 1 << len_w
    beats_a, beats_b = min(4, max_beats), min(8, max_beats)
    # 数据图案按 dw 掩码；rd 期望是 base + i，i < beats <= max_beats
    base_a = 0xA0000000 & dw_mask
    base_b = 0xBEEF1000 & dw_mask
    base_c = 0xDEADBEEF & dw_mask
    p = {k: _pm(iface, k, f"s_axi_{k}") for k in (
        "awid", "awaddr", "awlen", "awsize", "awburst", "awvalid", "awready",
        "wdata", "wstrb", "wlast", "wvalid", "wready",
        "bid", "bresp", "bvalid", "bready",
        "arid", "araddr", "arlen", "arsize", "arburst", "arvalid", "arready",
        "rid", "rdata", "rresp", "rlast", "rvalid", "rready",
    )}
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=axi4)
module tb_{mod}_axi4;
  logic          {clk} = 0, {rst} = 0;
  logic [{id_w-1}:0]    {p['awid']} = 0, {p['bid']}, {p['arid']} = 0, {p['rid']};
  logic [{aw-1}:0] {p['awaddr']} = '0, {p['araddr']} = '0;
  logic [{len_w-1}:0]    {p['awlen']} = 0, {p['arlen']} = 0;
  logic [{size_w-1}:0]    {p['awsize']} = {size_w}'d{(dw // 8).bit_length() - 1 if size_w >= 3 else size_w}, {p['arsize']} = {size_w}'d{(dw // 8).bit_length() - 1 if size_w >= 3 else size_w};
  logic [{burst_w-1}:0]    {p['awburst']} = {burst_w}'b01, {p['arburst']} = {burst_w}'b01, {p['bresp']}, {p['rresp']};
  logic          {p['awvalid']} = 0, {p['awready']}, {p['wvalid']} = 0, {p['wready']}, {p['wlast']} = 0;
  logic          {p['bvalid']}, {p['bready']} = 1, {p['arvalid']} = 0, {p['arready']}, {p['rvalid']}, {p['rready']} = 1, {p['rlast']};
  logic [{dw-1}:0] {p['wdata']} = '0, {p['rdata']};
  logic [{sw-1}:0] {p['wstrb']} = '1;
  int            errs = 0;

  {mod} dut (
    .{clk}({clk}), .{rst}({rst}),
    .{p['awid']}({p['awid']}), .{p['awaddr']}({p['awaddr']}), .{p['awlen']}({p['awlen']}),
    .{p['awsize']}({p['awsize']}), .{p['awburst']}({p['awburst']}), .{p['awvalid']}({p['awvalid']}), .{p['awready']}({p['awready']}),
    .{p['wdata']}({p['wdata']}), .{p['wstrb']}({p['wstrb']}), .{p['wvalid']}({p['wvalid']}), .{p['wready']}({p['wready']}), .{p['wlast']}({p['wlast']}),
    .{p['bid']}({p['bid']}), .{p['bresp']}({p['bresp']}), .{p['bvalid']}({p['bvalid']}), .{p['bready']}({p['bready']}),
    .{p['arid']}({p['arid']}), .{p['araddr']}({p['araddr']}), .{p['arlen']}({p['arlen']}),
    .{p['arsize']}({p['arsize']}), .{p['arburst']}({p['arburst']}), .{p['arvalid']}({p['arvalid']}), .{p['arready']}({p['arready']}),
    .{p['rid']}({p['rid']}), .{p['rdata']}({p['rdata']}), .{p['rresp']}({p['rresp']}), .{p['rvalid']}({p['rvalid']}), .{p['rready']}({p['rready']}), .{p['rlast']}({p['rlast']})
  );
  always #5 {clk} = ~{clk};

  task automatic axi_wr_burst(input string id_s, input logic [{id_w-1}:0] id, input logic [{aw-1}:0] addr, input int beats, input logic [{dw-1}:0] base_data, input logic [{resp_w-1}:0] exp_resp, input bit chk_resp);
    @(posedge {clk}); #1;
    {p['awid']} = id; {p['awaddr']} = addr; {p['awlen']} = {len_w}'(beats - 1); {p['awburst']} = {burst_w}'b01; {p['awvalid']} = 1;
    while (!{p['awready']}) begin @(posedge {clk}); #1; end
    @(posedge {clk}); #1;
    {p['awvalid']} = 0;
    for (int i = 0; i < beats; i++) begin
      {p['wdata']} = base_data + {dw}'(i); {p['wstrb']} = '1; {p['wlast']} = (i == beats - 1); {p['wvalid']} = 1;
      while (!{p['wready']}) begin @(posedge {clk}); #1; end
      @(posedge {clk}); #1;
    end
    {p['wvalid']} = 0; {p['wlast']} = 0;
    while (!({p['bvalid']} && {p['bready']})) begin @(posedge {clk}); #1; end
    if ({p['bid']} !== id || (chk_resp && {p['bresp']} !== exp_resp)) begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_write | tx=INCR%0d id=0x%0{idhex}h addr=0x%0{ahex}h | exp=id:0x%0{idhex}h,bresp:0x%0{max(1,(resp_w+3)//4)}h | got=id:0x%0{idhex}h,bresp:0x%0{max(1,(resp_w+3)//4)}h | status=FAIL",
               id_s, beats, id, addr, id, exp_resp, {p['bid']}, {p['bresp']});
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_write | tx=INCR%0d id=0x%0{idhex}h addr=0x%0{ahex}h | exp=id:0x%0{idhex}h,bresp:0x%0{max(1,(resp_w+3)//4)}h | got=id:0x%0{idhex}h,bresp:0x%0{max(1,(resp_w+3)//4)}h | status=PASS",
               id_s, beats, id, addr, id, exp_resp, {p['bid']}, {p['bresp']});
    end
    @(posedge {clk}); #1;
  endtask

  task automatic axi_rd_burst(input string id_s, input logic [{id_w-1}:0] id, input logic [{aw-1}:0] addr, input int beats, input logic [{dw-1}:0] base_data, input logic [{resp_w-1}:0] exp_resp, input bit chk_resp);
    int bad = 0;
    logic [{dw-1}:0] last_got = '0;
    @(posedge {clk}); #1;
    {p['arid']} = id; {p['araddr']} = addr; {p['arlen']} = {len_w}'(beats - 1); {p['arburst']} = {burst_w}'b01; {p['arvalid']} = 1;
    while (!{p['arready']}) begin @(posedge {clk}); #1; end
    @(posedge {clk}); #1;
    {p['arvalid']} = 0;
    for (int i = 0; i < beats; i++) begin
      while (!({p['rvalid']} && {p['rready']})) begin @(posedge {clk}); #1; end
      last_got = {p['rdata']};
      if ({p['rid']} !== id || (chk_resp && {p['rresp']} !== exp_resp) || (chk_resp && {p['rdata']} !== (base_data + {dw}'(i))) || ({p['rlast']} !== (i == beats - 1))) bad++;
      @(posedge {clk}); #1;
    end
    if (bad > 0) begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_read | tx=INCR%0d id=0x%0{idhex}h addr=0x%0{ahex}h | exp=last:0x%0{dhex}h,rresp:0x%0{max(1,(resp_w+3)//4)}h | got=last:0x%0{dhex}h(bad=%0d) | status=FAIL",
               id_s, beats, id, addr, base_data + {dw}'(beats - 1), exp_resp, last_got, bad);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_read | tx=INCR%0d id=0x%0{idhex}h addr=0x%0{ahex}h | exp=last:0x%0{dhex}h,rresp:0x%0{max(1,(resp_w+3)//4)}h | got=last:0x%0{dhex}h,rlast=1 | status=PASS",
               id_s, beats, id, addr, base_data + {dw}'(beats - 1), exp_resp, last_got);
    end
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    axi_wr_burst("AXI4_001", {id_w}'d{id_a}, {aw}'h{ad_a:0{ahex}X}, {beats_a}, {dw}'h{base_a:0{dhex}X}, {resp_w}'d0, 1'b1);
    axi_rd_burst("AXI4_002", {id_w}'d{id_a}, {aw}'h{ad_a:0{ahex}X}, {beats_a}, {dw}'h{base_a:0{dhex}X}, {resp_w}'d0, 1'b1);
    axi_wr_burst("AXI4_003", {id_w}'d{id_b}, {aw}'h{ad_b:0{ahex}X}, {beats_b}, {dw}'h{base_b:0{dhex}X}, {resp_w}'d0, 1'b1);
    axi_rd_burst("AXI4_004", {id_w}'d{id_b}, {aw}'h{ad_b:0{ahex}X}, {beats_b}, {dw}'h{base_b:0{dhex}X}, {resp_w}'d0, 1'b1);
    axi_wr_burst("AXI4_005", {id_w}'d{id_c}, {aw}'h{ad_ill:0{ahex}X}, 1, {dw}'h{base_c:0{dhex}X}, {resp_w}'d{resp_ok}, {int(ill_check)});
    axi_rd_burst("AXI4_006", {id_w}'d{id_c}, {aw}'h{ad_ill:0{ahex}X}, 1, {dw}'h0, {resp_w}'d{resp_ok}, {int(ill_check)});
    if (errs == 0) begin $display("PASS: axi4_full all 6 burst/SLVERR checks passed"); $finish; end
    else begin $display("FAIL: axi4_full %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_ahb_tb(mod: str, clk: str, rst: str, iface: Dict[str, Any]) -> str:
    # 与其他模板统一：位宽优先取**端口真实位宽**（port_details），
    # classifier 的 addr_width/data_width 只作兜底。海量第三方 AHB 设计里
    # HADDR/HWDATA 位宽与推导值不一致（如 20 位地址 + 32 位数据）时，
    # 写死推导值会产出 WIDTHTRUNC/WIDTHEXPAND 而 Verilator 警告即错误。
    aw = _pw(iface, "haddr", int(iface.get("addr_width") or 16))
    dw = _pw(iface, "hwdata", int(iface.get("data_width") or 32))
    # htrans / hsize / hresp 也不能写死：AHB-Lite 规范给的是 2/3/1 位，但真实 IP
    # 常把它扩展（部分实现用 2 位 HRESP 表示 SPLIT/RETRY）。写死会生成
    # `logic HRESP`（1 位）连到 2 位端口 → WIDTHEXPAND，Verilator 警告即错误
    # → 构建失败而工具报 ok=True（实测：'HRESP' expects 2 bits, generates 1）。
    htrans_w = _pw(iface, "htrans", 2)
    hsize_w = _pw(iface, "hsize", 3)
    hresp_w = _pw(iface, "hresp", 1)
    # HSIZE 的合法编码：2**n 字节 → n = log2(dw/8)，但必须能装进 hsize_w 位
    _size_code = max(0, (dw // 8).bit_length() - 1)
    if hsize_w < 32:
        _size_code = min(_size_code, (1 << hsize_w) - 1)
    # htrans 的 NONSEQ(0b10) / IDLE(0b00) 编码同理，超窄 htrans 退化为能表示的值
    _htrans_non = 0b10 & ((1 << htrans_w) - 1)
    hsel = _pm(iface, "hsel", "HSEL")
    haddr = _pm(iface, "haddr", "HADDR")
    htrans = _pm(iface, "htrans", "HTRANS")
    hwrite = _pm(iface, "hwrite", "HWRITE")
    hsize = _pm(iface, "hsize", "HSIZE")
    hwdata = _pm(iface, "hwdata", "HWDATA")
    hready = _pm(iface, "hready", "HREADYOUT")
    hrdata = _pm(iface, "hrdata", "HRDATA")
    hresp = _pm(iface, "hresp", "HRESP")
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=ahb_lite)
module tb_{mod}_ahb_lite;
  logic          {clk} = 0, {rst} = 0, {hsel} = 0, {hwrite} = 0, {hready};
  logic [{aw-1}:0] {haddr} = '0;
  logic [{htrans_w-1}:0]    {htrans} = 0;
  logic [{hsize_w-1}:0]    {hsize} = {hsize_w}'d{_size_code};
  logic [{hresp_w-1}:0]    {hresp};
  logic [{dw-1}:0] {hwdata} = '0, {hrdata};
  int            errs = 0;

  {mod} dut (
    .{clk}({clk}), .{rst}({rst}), .{hsel}({hsel}), .{haddr}({haddr}), .{htrans}({htrans}),
    .{hwrite}({hwrite}), .{hsize}({hsize}), .{hwdata}({hwdata}),
    .{hready}({hready}), .{hrdata}({hrdata}), .{hresp}({hresp})
  );
  always #5 {clk} = ~{clk};

  task automatic ahb_write(input string id, input string cat, input logic [{aw-1}:0] addr, input logic [{dw-1}:0] data, input logic [{hresp_w-1}:0] exp_err);
    @(posedge {clk}); #1;
    {hsel} = 1; {htrans} = {htrans_w}'b{_htrans_non:0{htrans_w}b}; {hwrite} = 1; {haddr} = addr;
    @(posedge {clk}); #1;
    {hsel} = 0; {htrans} = {htrans_w}'d0; {hwdata} = data;
    do begin @(posedge {clk}); #1; end while (!{hready});
    if ({hresp} !== exp_err) begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=WR addr=0x%04h data=0x%08h | exp=HRESP:0x%0{max(1,(hresp_w+3)//4)}h | got=HRESP:0x%0{max(1,(hresp_w+3)//4)}h | status=FAIL", id, cat, addr, data, exp_err, {hresp});
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=WR addr=0x%04h data=0x%08h | exp=HRESP:0x%0{max(1,(hresp_w+3)//4)}h | got=HRESP:0x%0{max(1,(hresp_w+3)//4)}h | status=PASS", id, cat, addr, data, exp_err, {hresp});
    end
  endtask

  task automatic ahb_read(input string id, input string cat, input logic [{aw-1}:0] addr, input logic [{dw-1}:0] exp_data, input logic [{hresp_w-1}:0] exp_err);
    @(posedge {clk}); #1;
    {hsel} = 1; {htrans} = {htrans_w}'b{_htrans_non:0{htrans_w}b}; {hwrite} = 0; {haddr} = addr;
    @(posedge {clk}); #1;
    {hsel} = 0; {htrans} = {htrans_w}'d0;
    do begin @(posedge {clk}); #1; end while (!{hready});
    if ({hresp} !== exp_err || ((exp_err == 0) && {hrdata} !== exp_data)) begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=RD addr=0x%04h | exp=0x%08h(HRESP=%0b) | got=0x%08h(HRESP=%0b) | status=FAIL", id, cat, addr, exp_data, exp_err, {hrdata}, {hresp});
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=RD addr=0x%04h | exp=0x%08h(HRESP=%0b) | got=0x%08h(HRESP=%0b) | status=PASS", id, cat, addr, exp_data, exp_err, {hrdata}, {hresp});
    end
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    ahb_read ("AHB_001", "reset_read",  {aw}'h0000, {dw}'h11223344, {hresp_w}'d0);
    ahb_write("AHB_002", "rw_write",    {aw}'h0000, {dw}'hA5A55A5A, {hresp_w}'d0);
    ahb_read ("AHB_003", "rw_readback", {aw}'h0000, {dw}'hA5A55A5A, {hresp_w}'d0);
    ahb_write("AHB_004", "ro_write",    {aw}'h0004, {dw}'hFFFFFFFF, {hresp_w}'d0);
    ahb_read ("AHB_005", "ro_protect",  {aw}'h0004, {dw}'hCAFEBABE, {hresp_w}'d0);
    ahb_read ("AHB_006", "hresp_error", {aw}'h0040, {dw}'h00000000, {hresp_w}'d1);
    if (errs == 0) begin $display("PASS: ahb_lite all 6 checks passed"); $finish; end
    else begin $display("FAIL: ahb_lite %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_ddr3_tb(mod: str, clk: str, rst: str, iface: Optional[Dict[str, Any]] = None) -> str:
    """生成 DDR3 控制器 TB。

    所有位宽必须跟随 DUT 真实端口：旧实现把 ``ddr3_ba`` 写死 ``[2:0]``、数据常量
    写死 ``0xBEEF`` / ``0x1234`` / ``%04h``。DUT 用 2 位 BA 或 8 位 DQ 时生成
    ``WIDTHTRUNC`` / ``WIDTHDIGITS`` —— 而 Verilator 默认**警告即错误**，构建
    直接失败，工具却仍返回 ok=True（实测 8 位 dq：4 条 WIDTHDIGITS）。
    """
    _i = iface or {}
    aw_d = _pw(_i, "addr", 14)
    dw_d = _pw(_i, "dq", 16)
    ba_d = _pw(_i, "ba", 3)
    # 数据常量必须能装进 dw_d：8 位总线上 `8'hBEEF` 是 Too many digits。
    dw_hex = max(1, (dw_d + 3) // 4)
    pat_a = 0xBEEF & ((1 << dw_d) - 1)
    pat_b = 0x1234 & ((1 << dw_d) - 1)
    ba_max = (1 << ba_d) - 1
    ba_a, ba_b = 2 & ba_max, 5 & ba_max
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=ddr3)
module tb_{mod}_ddr3;
  logic        {clk} = 0, {rst} = 0, ddr3_cke = 1, ddr3_cs_n = 1, ddr3_ras_n = 1, ddr3_cas_n = 1, ddr3_we_n = 1, ddr3_rd_valid;
  logic [{ba_d-1}:0]  ddr3_ba = 0;
  logic [{aw_d-1}:0] ddr3_addr = 0;
  logic [{dw_d-1}:0] ddr3_dq_in = 0, ddr3_dq_out;
  int          errs = 0;

  {mod} dut (
    .{clk}({clk}), .{rst}({rst}), .ddr3_ck_p({clk}), .ddr3_ck_n(~{clk}),
    .ddr3_cke(ddr3_cke), .ddr3_cs_n(ddr3_cs_n), .ddr3_ras_n(ddr3_ras_n),
    .ddr3_cas_n(ddr3_cas_n), .ddr3_we_n(ddr3_we_n), .ddr3_ba(ddr3_ba),
    .ddr3_addr(ddr3_addr), .ddr3_dq_in(ddr3_dq_in), .ddr3_dq_out(ddr3_dq_out),
    .ddr3_rd_valid(ddr3_rd_valid)
  );
  always #5 {clk} = ~{clk};

  task automatic ddr3_cmd(input logic [2:0] cmd, input logic [{ba_d-1}:0] ba, input logic [{aw_d-1}:0] addr, input logic [{dw_d-1}:0] dq);
    @(posedge {clk}); #1;
    ddr3_cs_n = 0; {{ddr3_ras_n, ddr3_cas_n, ddr3_we_n}} = cmd; ddr3_ba = ba; ddr3_addr = addr; ddr3_dq_in = dq;
    @(posedge {clk}); #1;
    ddr3_cs_n = 1; {{ddr3_ras_n, ddr3_cas_n, ddr3_we_n}} = 3'b111;
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    ddr3_cmd(3'b011, {ba_d}'d{ba_a}, {aw_d}'d1, {dw_d}'h0);
    ddr3_cmd(3'b100, {ba_d}'d{ba_a}, {aw_d}'d3, {dw_d}'h{pat_a:0{dw_hex}X});
    ddr3_cmd(3'b011, {ba_d}'d{ba_b}, {aw_d}'d2, {dw_d}'h0);
    ddr3_cmd(3'b100, {ba_d}'d{ba_b}, {aw_d}'d6, {dw_d}'h{pat_b:0{dw_hex}X});
    ddr3_cmd(3'b101, {ba_d}'d{ba_a}, {aw_d}'d3, {dw_d}'h0);
    repeat (2) @(posedge {clk}); #1;
    if (!ddr3_rd_valid || ddr3_dq_out !== {dw_d}'h{pat_a:0{dw_hex}X}) begin
      $display("[CHECK] id=DDR3_001 | proto=DDR3 | cat=act_wr_rd_cl2 | tx=ACT/WR/RD bank={ba_a} col=3 CL=2 | exp=0x{pat_a:0{dw_hex}X}(valid=1) | got=0x%0{dw_hex}h(valid=%0b) | status=FAIL", ddr3_dq_out, ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_001 | proto=DDR3 | cat=act_wr_rd_cl2 | tx=ACT/WR/RD bank={ba_a} col=3 CL=2 | exp=0x{pat_a:0{dw_hex}X}(valid=1) | got=0x%0{dw_hex}h(valid=%0b) | status=PASS", ddr3_dq_out, ddr3_rd_valid);
    end
    ddr3_cmd(3'b101, {ba_d}'d{ba_b}, {aw_d}'d6, {dw_d}'h0);
    repeat (2) @(posedge {clk}); #1;
    if (!ddr3_rd_valid || ddr3_dq_out !== {dw_d}'h{pat_b:0{dw_hex}X}) begin
      $display("[CHECK] id=DDR3_002 | proto=DDR3 | cat=multi_bank_isolation | tx=ACT/WR/RD bank={ba_b} col=6 CL=2 | exp=0x{pat_b:0{dw_hex}X}(valid=1) | got=0x%0{dw_hex}h(valid=%0b) | status=FAIL", ddr3_dq_out, ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_002 | proto=DDR3 | cat=multi_bank_isolation | tx=ACT/WR/RD bank={ba_b} col=6 CL=2 | exp=0x{pat_b:0{dw_hex}X}(valid=1) | got=0x%0{dw_hex}h(valid=%0b) | status=PASS", ddr3_dq_out, ddr3_rd_valid);
    end
    ddr3_cmd(3'b010, {ba_d}'d{ba_a}, {aw_d}'d0, {dw_d}'h0);
    ddr3_cmd(3'b101, {ba_d}'d{ba_a}, {aw_d}'d3, {dw_d}'h0);
    repeat (2) @(posedge {clk}); #1;
    if (ddr3_rd_valid !== 1'b0) begin
      $display("[CHECK] id=DDR3_003 | proto=DDR3 | cat=precharge_close | tx=PRE bank={ba_a} then RD | exp=valid=0 | got=valid=%0b | status=FAIL", ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_003 | proto=DDR3 | cat=precharge_close | tx=PRE bank={ba_a} then RD | exp=valid=0 | got=valid=%0b | status=PASS", ddr3_rd_valid);
    end
    if (errs == 0) begin $display("PASS: ddr3 ACT/WR/RD(CL=2)/PRE all checks passed"); $finish; end
    else begin $display("FAIL: ddr3 %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_ddr4_tb(mod: str, clk: str, rst: str, iface: Optional[Dict[str, Any]] = None) -> str:
    """生成 DDR4 控制器 TB。

    位宽必须跟随 DUT 真实端口（``ddr4_bg`` / ``ddr4_ba`` / ``ddr4_adr`` /
    ``ddr4_dq_*`` / ``ddr4_dm_dbi_n``）。旧模板把 adr 写死 17 位、dq 写死 64 位、
    dm_dbi_n 写死 8 位，DUT 不同宽时生成 WIDTHTRUNC/WIDTHEXPAND，而 Verilator
    默认警告即错误 → 构建失败但工具报 ok=True。

    ``ddr4_adr[16:14]`` 承载命令编码（100=WR / 101=RD / 010=PRE），这是 DDR4
    协议语义，只在 adr 足够宽时使用；窄 adr（如 14 位）放不下命令位，此时这些
    位仍是 DUT 自己解释的地址位，TB 照常按同一位段驱动（模板不需要理解 DUT）。
    """
    _i = iface or {}
    aw_d = _pw(_i, "adr", 17)
    dw_d = _pw(_i, "dq", 64)
    bg_d = _pw(_i, "bg", 2)
    ba_d = _pw(_i, "ba", 2)
    dm_d = _pw(_i, "dm_dbi_n", 8)
    dw_hex = max(1, (dw_d + 3) // 4)
    dm_hex = max(1, (dm_d + 3) // 4)
    dw_mask = (1 << dw_d) - 1
    pat_lo = 0xCAFE0123 & dw_mask
    pat_alt = 0xCAFE0199 & dw_mask
    dm_all = (1 << dm_d) - 1
    bg_max = (1 << bg_d) - 1
    ba_max = (1 << ba_d) - 1
    bg_v, ba_v = 2 & bg_max, 1 & ba_max
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=ddr4)
module tb_{mod}_ddr4;
  logic        {clk} = 0, {rst} = 0, ddr4_act_n = 1, ddr4_cs_n = 1, ddr4_odt = 0, ddr4_rd_valid;
  logic [{bg_d-1}:0]  ddr4_bg = 0;
  logic [{ba_d-1}:0]  ddr4_ba = 0;
  logic [{aw_d-1}:0] ddr4_adr = 0;
  logic [{dw_d-1}:0] ddr4_dq_in = 0, ddr4_dq_out;
  logic [{dm_d-1}:0]  ddr4_dm_dbi_n = '1;
  int          errs = 0;

  {mod} dut (
    .{clk}({clk}), .{rst}({rst}), .ddr4_ck_t({clk}), .ddr4_ck_c(~{clk}),
    .ddr4_act_n(ddr4_act_n), .ddr4_cs_n(ddr4_cs_n), .ddr4_odt(ddr4_odt),
    .ddr4_bg(ddr4_bg), .ddr4_ba(ddr4_ba), .ddr4_adr(ddr4_adr),
    .ddr4_dq_in(ddr4_dq_in), .ddr4_dq_out(ddr4_dq_out),
    .ddr4_dm_dbi_n(ddr4_dm_dbi_n), .ddr4_rd_valid(ddr4_rd_valid)
  );
  always #5 {clk} = ~{clk};

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_act_n = 0; ddr4_bg = {bg_d}'d{bg_v}; ddr4_ba = {ba_d}'d{ba_v}; ddr4_adr = '0;
    @(posedge {clk}); #1; ddr4_cs_n = 1; ddr4_act_n = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {aw_d}'h{(0b100 << (aw_d - 3)) if aw_d >= 3 else 0:0{max(1,(aw_d+3)//4)}X}; ddr4_dm_dbi_n = {dm_d}'h{dm_all:0{dm_hex}X}; ddr4_dq_in = {dw_d}'h{pat_lo:0{dw_hex}X};
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {aw_d}'h{(0b101 << (aw_d - 3)) if aw_d >= 3 else 0:0{max(1,(aw_d+3)//4)}X};
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    if (!ddr4_rd_valid || ddr4_dq_out !== {dw_d}'h{pat_lo:0{dw_hex}X}) begin
      $display("[CHECK] id=DDR4_001 | proto=DDR4 | cat=bg_ba_full_rw | tx=ACT(BG={bg_v},BA={ba_v})+WR+RD DW={dw_d} | exp=0x{pat_lo:0{dw_hex}X} | got=0x%0{dw_hex}h(v=%0b) | status=FAIL", ddr4_dq_out, ddr4_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR4_001 | proto=DDR4 | cat=bg_ba_full_rw | tx=ACT(BG={bg_v},BA={ba_v})+WR+RD DW={dw_d} | exp=0x{pat_lo:0{dw_hex}X} | got=0x%0{dw_hex}h(v=%0b) | status=PASS", ddr4_dq_out, ddr4_rd_valid);
    end
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {aw_d}'h{(0b100 << (aw_d - 3)) if aw_d >= 3 else 0:0{max(1,(aw_d+3)//4)}X}; ddr4_dm_dbi_n = {dm_d}'h{1:0{dm_hex}X}; ddr4_dq_in = {dw_d}'h{pat_alt:0{dw_hex}X};
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {aw_d}'h{(0b101 << (aw_d - 3)) if aw_d >= 3 else 0:0{max(1,(aw_d+3)//4)}X};
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    if (!ddr4_rd_valid || ddr4_dq_out !== {dw_d}'h{pat_alt:0{dw_hex}X}) begin
      $display("[CHECK] id=DDR4_002 | proto=DDR4 | cat=dm_dbi_byte_mask | tx=WR dm_dbi_n=0x1 DW={dw_d} -> RD | exp=0x{pat_alt:0{dw_hex}X} | got=0x%0{dw_hex}h(v=%0b) | status=FAIL", ddr4_dq_out, ddr4_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR4_002 | proto=DDR4 | cat=dm_dbi_byte_mask | tx=WR dm_dbi_n=0x1 DW={dw_d} -> RD | exp=0x{pat_alt:0{dw_hex}X} | got=0x%0{dw_hex}h(v=%0b) | status=PASS", ddr4_dq_out, ddr4_rd_valid);
    end
    if (errs == 0) begin $display("PASS: ddr4 BG/BA + DM_DBI_n masked write/readback passed"); $finish; end
    else begin $display("FAIL: ddr4 %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_hbm_tb(mod: str, clk: str, rst: str, iface: Optional[Dict[str, Any]] = None) -> str:
    """生成 HBM pseudo-channel TB。

    位宽必须跟随 DUT 真实端口：旧模板写死 ID=6 位 / addr=28 位 / data=256 位。
    HBM 各厂商实现差别很大（pseudo-channel 位宽 64/128/256、ID 4~8 位、地址
    30~34 位），DUT 不同宽时生成 WIDTHTRUNC/WIDTHEXPAND，Verilator 默认警告即
    错误 → 构建失败而工具报 ok=True（实测 64 位 wdata 对 256 位模板即 2 条
    WIDTHEXPAND）。
    """
    _i = iface or {}
    id_d = _pw(_i, "awid", 6)
    aw_d = _pw(_i, "awaddr", 28)
    dw_d = _pw(_i, "wdata", 256)
    dw_hex = max(1, (dw_d + 3) // 4)
    id_hex = max(1, (id_d + 3) // 4)
    # 数据图案按真实位宽构造：低 64 位放可辨识常量，其余位重复该常量。
    # 写死的 256 位拼接在窄总线上直接 WIDTHTRUNC。
    id_w = 0x2A & ((1 << id_d) - 1)
    id_r = 0x15 & ((1 << id_d) - 1)
    pat_lo = 0x01234567_89ABCDEF
    # 拼接图案必须按**位宽**构造，不能按十六进制字符数截断：dw=256 时
    # 16 字符 × 5 = 80 字符截到 64 字符，低 64 位就变成了
    # 0xF123456789ABCDEF（多进了一个半字节）——实测直接让 HBM_002 期望值错。
    # 正确做法是：低 64 位放常量，高位按 64 位块重复，再取整 dw 位。
    rep = max(1, (dw_d + 63) // 64)
    pat_val = 0
    for i in range(rep):
        pat_val |= (pat_lo << (64 * i))
    pat_val &= (1 << dw_d) - 1
    pat_hex = f"{pat_val:0{dw_hex}X}"
    lo_hex = f"{pat_val & 0xFFFFFFFFFFFFFFFF:016X}"
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=hbm)
module tb_{mod}_hbm;
  logic         {clk} = 0, {rst} = 0;
  logic [{id_d-1}:0]   hbm_awid = 0, hbm_bid, hbm_arid = 0, hbm_rid;
  logic [{aw_d-1}:0]  hbm_awaddr = 0, hbm_araddr = 0;
  logic [{dw_d-1}:0] hbm_wdata = 0, hbm_rdata;
  logic         hbm_awvalid = 0, hbm_awready, hbm_wvalid = 0, hbm_wready, hbm_bvalid, hbm_bready = 1;
  logic         hbm_arvalid = 0, hbm_arready, hbm_rvalid, hbm_rready = 1;
  int           errs = 0;

  {mod} dut (
    .{clk}({clk}), .{rst}({rst}),
    .hbm_awid(hbm_awid), .hbm_awaddr(hbm_awaddr), .hbm_awvalid(hbm_awvalid), .hbm_awready(hbm_awready),
    .hbm_wdata(hbm_wdata), .hbm_wvalid(hbm_wvalid), .hbm_wready(hbm_wready),
    .hbm_bid(hbm_bid), .hbm_bvalid(hbm_bvalid), .hbm_bready(hbm_bready),
    .hbm_arid(hbm_arid), .hbm_araddr(hbm_araddr), .hbm_arvalid(hbm_arvalid), .hbm_arready(hbm_arready),
    .hbm_rid(hbm_rid), .hbm_rdata(hbm_rdata), .hbm_rvalid(hbm_rvalid), .hbm_rready(hbm_rready)
  );
  always #5 {clk} = ~{clk};

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    @(posedge {clk}); #1;
    hbm_awid = {id_d}'h{id_w:0{id_hex}X}; hbm_awaddr = {aw_d}'h60; hbm_awvalid = 1;
    hbm_wdata = {dw_d}'h{pat_hex}; hbm_wvalid = 1;
    @(posedge {clk}); #1;
    hbm_awvalid = 0; hbm_wvalid = 0;
    if (!hbm_bvalid || hbm_bid !== {id_d}'h{id_w:0{id_hex}X}) begin
      $display("[CHECK] id=HBM_001 | proto=HBM | cat=pc_write_id | tx=WR DW={dw_d} id=0x{id_w:0{id_hex}X} addr=0x60 | exp=bvalid=1,bid=0x{id_w:0{id_hex}X} | got=bvalid=%0b,bid=0x%0{id_hex}h | status=FAIL", hbm_bvalid, hbm_bid);
      errs++;
    end else begin
      $display("[CHECK] id=HBM_001 | proto=HBM | cat=pc_write_id | tx=WR DW={dw_d} id=0x{id_w:0{id_hex}X} addr=0x60 | exp=bvalid=1,bid=0x{id_w:0{id_hex}X} | got=bvalid=%0b,bid=0x%0{id_hex}h | status=PASS", hbm_bvalid, hbm_bid);
    end
    @(posedge {clk}); #1;
    hbm_arid = {id_d}'h{id_r:0{id_hex}X}; hbm_araddr = {aw_d}'h60; hbm_arvalid = 1; hbm_rready = 0;
    @(posedge {clk}); #1;
    hbm_arvalid = 0;
    @(posedge {clk}); #1;
    if (!hbm_rvalid || hbm_rid !== {id_d}'h{id_r:0{id_hex}X} || hbm_rdata[63:0] !== 64'h{lo_hex}) begin
      $display("[CHECK] id=HBM_002 | proto=HBM | cat=pc_read_backpressure | tx=RD DW={dw_d} id=0x{id_r:0{id_hex}X} rready=0 hold | exp=rvalid=1,rid=0x{id_r:0{id_hex}X},lo64=0x{lo_hex} | got=rvalid=%0b,rid=0x%0{id_hex}h,lo64=0x%016h | status=FAIL", hbm_rvalid, hbm_rid, hbm_rdata[63:0]);
      errs++;
    end else begin
      $display("[CHECK] id=HBM_002 | proto=HBM | cat=pc_read_backpressure | tx=RD DW={dw_d} id=0x{id_r:0{id_hex}X} rready=0 hold | exp=rvalid=1,rid=0x{id_r:0{id_hex}X},lo64=0x{lo_hex} | got=rvalid=%0b,rid=0x%0{id_hex}h,lo64=0x%016h | status=PASS", hbm_rvalid, hbm_rid, hbm_rdata[63:0]);
    end
    hbm_rready = 1;
    @(posedge {clk});
    if (errs == 0) begin $display("PASS: hbm pseudo-channel ID+data+backpressure passed"); $finish; end
    else begin $display("FAIL: hbm %0d errors", errs); $fatal(1); end
  end
endmodule
"""


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
    tx_data = _pm(iface, "tx_data", "pcie_tx_tdata")
    rx_data = _pm(iface, "rx_data", "pcie_rx_tdata")
    spd = _pm(iface, "speed", "pcie_cfg_speed")
    lns = _pm(iface, "lanes", "pcie_cfg_lanes")

    real_dw = int(widths.get(rx_data) or widths.get(tx_data) or 0)
    if real_dw <= 0:
        real_dw = _pcie_dw_for_lanes(int(params.get("LANES") or 4))
    sw = int(widths.get(spd) or 4)
    lw = int(widths.get(lns) or 6)

    has_gen = "GEN" in params
    has_lanes = "LANES" in params
    has_dw = "DW" in params
    matrix = has_gen and has_lanes
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
        link_expect = (
            f"!pcie_link_up || {spd} !== {sw}'(GEN) || {lns} !== {lw}'(LANES)"
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
        f"    if ({link_expect}) begin\n"
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
        "    .pcie_tx_tvalid(pcie_tx_tvalid), .pcie_tx_tready(pcie_tx_tready),\n"
        f"    .{tx_data}({tx_data}), .pcie_tx_tlast(pcie_tx_tlast),\n"
        "    .pcie_rx_tvalid(pcie_rx_tvalid), .pcie_rx_tready(pcie_rx_tready),\n"
        f"    .{rx_data}({rx_data}), .pcie_rx_tlast(pcie_rx_tlast),\n"
        f"    .pcie_link_up(pcie_link_up), .{spd}({spd}), .{lns}({lns})\n"
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


def add_lint_off(tb_src: str) -> str:
    """在生成的 TB 里关掉 PINMISSING。

    DUT 常带有 TB 不关心的额外端口（调试口、test_mode、scan_en 等）。模板用
    `.*` 通配连接，只覆盖 TB 里声明了同名变量的端口，其余会触发 Verilator 的
    PINMISSING 警告——而 `emit` 的 run 脚本默认把 warning 当错误，构建直接
    "Exiting due to 1 warning(s)" 失败，工具却已返回 ok=True。
    """
    out = []
    in_header = False
    for line in tb_src.splitlines(keepends=True):
        out.append(line)
        if line.startswith("module tb_"):
            # module 头可能是单行 `module tb_x;`，也可能是多行参数化形式
            # （module 名后跟 #( ... );）——后者要等到 `);` 才结束。
            if line.rstrip().endswith(";"):
                in_header = False
                out.append(
                    "  // DUT 可能有 TB 不关心的额外端口（调试/测试/scan），\n"
                    "  // 显式连接覆盖不到会触发 PINMISSING，故显式关闭。\n"
                    "  /* verilator lint_off PINMISSING */\n"
                )
            else:
                in_header = True
        elif in_header and line.rstrip().endswith(";"):
            in_header = False
            out.append(
                "  // DUT 可能有 TB 不关心的额外端口（调试/测试/scan），\n"
                "  // 显式连接覆盖不到会触发 PINMISSING，故显式关闭。\n"
                "  /* verilator lint_off PINMISSING */\n"
            )
    return "".join(out)


def add_waveform_dump(tb_src: str, top_module: str) -> str:
    """给生成的 TB 在 initial 块最前面注入 $dumpfile / $dumpvars。

    为什么要用环境变量控制：波形是可选的（会让仿真慢、文件大），不该默认开启。
    但 TB 一旦编译成二进制，dump 目标就在运行期固定了——所以这里用
    `$value$plusargs` 从仿真命令行读 +WAVE / +WAVE_FILE，无需重新生成 TB。

    关键事实（实测）：Verilator 在**没有** `--trace` 时只打一条
    `-Info: $dumpvar ignored, as Verilated without --trace`，仿真照常
    PASS、exit 0，波形文件根本不存在。$dumpvars 写在 TB 里只是必要条件，
    run 脚本必须同时加 --trace（由 WAVE=1 控制），二者缺一不可。

    注入位置：第一个 `initial begin` 之后的第一行。此时 timescale/clk 已定义，
    且在任何激励之前开始记录，不会漏掉开头几个周期的信号。
    """
    snippet = (
        "    // ---- waveform dump (optional; enable with +WAVE on the sim command line) ----\n"
        '    if ($test$plusargs("WAVE")) begin\n'
        '      if ($value$plusargs("WAVE_FILE=%s", wave_path)) begin\n'
        '        $dumpfile(wave_path);\n'
        "      end else begin\n"
        '        $dumpfile("wave.vcd");\n'
        "      end\n"
        f"      $dumpvars(0, {top_module});\n"
        "    end\n"
    )
    # 在第一个 initial begin 之后插入
    marker = "  initial begin\n"
    idx = tb_src.find(marker)
    if idx < 0:
        return tb_src  # 模板形态变了：不静默破坏，保持原样
    insert_at = idx + len(marker)
    out = tb_src[:insert_at] + snippet + tb_src[insert_at:]

    # $value$plusargs 的目标必须是一个已声明的变量（Verilator 会报
    # "Can't find definition of variable: 'wave_path'" 直接拒绝编译——
    # 这是实际跑一遍才发现的）。声明放在 module 顶部、endmodule 之前。
    decl = '  string wave_path;\n'
    if decl not in out:
        close = out.rfind("endmodule")
        if close > 0:
            out = out[:close] + decl + out[close:]
    return out


def _emit_run_script(
    proto: str,
    mod: str,
    rtl_abs: Path,
    tb_abs: Path,
    *,
    pcie_suites: Optional[List[str]] = None,
    pcie_matrix: bool = False,
) -> str:
    """生成 suite 派发脚本。

    suite 列表必须与 TB 模板**实际支持的配置**一致：
    * TB 走 3x3 矩阵（DUT 声明了 GEN/LANES）时才是 9 个 suite，且必须把
      ``-GGEN`` / ``-GLANES`` 传给 verilator 才能覆盖参数；
    * TB 是固定配置时只有一个 suite，**不能**再传 ``-GGEN/-GLANES``——
      对没有该参数的设计跑 ``verilator -GGEN=3`` 会得到
      ``Parameters from the command line were not found in the design`` 并以
      非 0 退出，9 个「suite」全部是编译失败（旧 `sim.sh` 就踩过这个）。
    """
    top = f"tb_{mod}_{proto}"
    if proto == "pcie":
        names = list(pcie_suites or [])
        if not names:
            names = [f"pcie_g{g}x{n}" for g in _PCIE_GENS for n in _PCIE_LANES]
        cases: List[str] = []
        for sname in names:
            flags = ""
            if pcie_matrix:
                # suite 名形如 pcie_g4x16，直接解析出需要覆盖的参数值。
                mm = re.match(r"pcie_g(\d+)x(\d+)$", sname)
                if mm:
                    flags = f" -GGEN={mm.group(1)} -GLANES={mm.group(2)}"
            cases.append(f'  {sname}) run_one {sname} {top}{flags} "{rtl_abs}" "{tb_abs}" ;;')
        all_s = " ".join(names)
    else:
        all_s = proto
        cases = [f'  {proto}) run_one {proto} {top} "{rtl_abs}" "{tb_abs}" ;;']
    cases_s = "\n".join(cases)

    return f"""#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
VLT="${{VERILATOR:-verilator}}"

# 波形开关：WAVE=1 时给 verilator 加 --trace / --trace-fst，并让 TB 的
# $dumpfile 落到 WAVE_DIR（默认脚本目录）。
# 注意：Verilator 在没有 --trace 时**静默忽略** $dumpvars（只打一条 -Info），
# 仿真仍 PASS、exit 0，但波形文件不存在。所以 --trace 必须与 TB 里的
# $dumpfile/$dumpvars 配对，run_sim 再用 require_waveform 对产物做断言。
WAVE="${{WAVE:-0}}"
WAVE_FORMAT="${{WAVE_FORMAT:-vcd}}"
TRACE_FLAG=""
if [ "$WAVE" = "1" ]; then
  case "$WAVE_FORMAT" in
    fst) TRACE_FLAG="--trace-fst" ;;
    *)   TRACE_FLAG="--trace" ;;
  esac
fi

run_one() {{
  local name="$1"; shift
  local top="$1"; shift
  local out="$HERE/obj_$name"
  local bin="$out/V$top"
  # 波形开关变化必须重建：带 --trace / --trace-fst 与不带是三套二进制。
  # 复用旧 binary 会造成两种假成功：
  #   * 加了 WAVE=1 却没有波形（旧 binary 无 trace）
  #   * WAVE_FORMAT=fst 却产出 VCD 内容（旧 binary 是 --trace 编的）
  # 所以标记文件里记下**格式**，不只是"开没开"。
  #
  # want_tag 必须在 if/else **之外**声明：若放在某一支里，另一支根本不执行
  # 声明，而脚本用 `set -u`，之后 `printf '%s' "$want_tag"` 报
  # `unbound variable` 并以 rc=1 失败。实测踩坑：全新目录**首次**构建
  # （走 then 分支，binary 不存在）100% 失败；第二次运行 binary 已存在、
  # 走 else 分支反而正常 —— 这个反差把缺陷完全掩盖住了。
  local want_tag="off"
  if [ "$WAVE" = "1" ]; then want_tag="$WAVE_FORMAT"; fi
  local need_build=0
  if [ ! -x "$bin" ]; then
    need_build=1
  else
    for arg in "$@"; do
      if [ -f "$arg" ] && [ "$arg" -nt "$bin" ]; then
        need_build=1
      fi
    done
    local have_tag=""
    if [ -f "$out/.wave_mode" ]; then have_tag="$(cat "$out/.wave_mode" 2>/dev/null || true)"; fi
    if [ "$want_tag" != "$have_tag" ]; then
      need_build=1
    fi
  fi
  echo "== sim: $name (top=$top) =="
  if [ "$need_build" -eq 1 ]; then
    rm -rf "$out"
    "$VLT" --binary --build-jobs 4 $TRACE_FLAG -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME -Wno-PINMISSING --top-module "$top" -Mdir "$out" "$@" >/dev/null
    printf '%s' "$want_tag" > "$out/.wave_mode"
  fi
  if [ "$WAVE" = "1" ]; then
    # +WAVE 让 TB 里的 $dumpvars 真正执行；+WAVE_FILE 指定落盘路径。
    # 只用环境变量不生效：TB 读的是 plusargs，不是 env。
    local wave_file="${{WAVE_DIR:-$HERE}}/wave_${{name}}.${{WAVE_FORMAT:-vcd}}"
    "$bin" +WAVE "+WAVE_FILE=$wave_file"
  else
    "$bin"
  fi
}}

case "${{1:-all}}" in
{cases_s}
  all)
    for s in {all_s}; do
      bash "$0" "$s"
    done
    ;;
  *) echo "Unknown suite: $1" >&2; exit 2 ;;
esac
"""


def emit_protocol_testbench(
    rtl_file: str,
    out_dir: str,
    protocol: Optional[str] = None,
) -> Dict[str, Any]:
    """Emit a self-checking SV testbench + Verilator runner from RTL AST for any supported bus protocol."""
    rp = Path(rtl_file).resolve()
    if not rp.is_file():
        return err_result("emit_testbench", f"RTL file not found: {rtl_file}")

    ex = tool_extract_interfaces(str(rp))
    if not ex.get("ok"):
        return err_result("emit_testbench", f"Interface extraction failed for {rtl_file}: {ex.get('error')}")
    mods = (ex.get("data") or {}).get("modules") or []
    if not mods:
        return err_result("emit_testbench", f"No modules found in {rtl_file}.")

    # 模块选择：一个文件里可以有多个 module（helper/package wrapper/顶层），
    # 旧实现固定取 mods[0]——若第一个是 helper，就会报 "Protocol 'apb' not found
    # in helper" 而拒绝一个完全合法的文件。正确做法是在所有模块里挑**含目标
    # 协议接口**的那个（auto 时挑第一个含任一支持协议的）。
    want = None if (not protocol or protocol == "auto") else protocol
    m = None
    chosen = None
    for cand in mods:
        cand_ifaces = cand.get("interfaces") or []
        for i in cand_ifaces:
            if want is not None:
                if i.get("protocol") == want:
                    m, chosen = cand, i
                    break
            elif i.get("protocol") in SUPPORTED_MULTI_PROTOCOLS:
                m, chosen = cand, i
                break
        if m is not None:
            break
    if m is None or chosen is None:
        found = {cand.get("module_name"): [i.get("protocol") for i in (cand.get("interfaces") or [])] for cand in mods}
        return err_result(
            "emit_testbench",
            f"{'Protocol ' + repr(want) + ' not found' if want else 'No supported multi-bus interface found'} "
            f"in any module of {rp.name} (modules: {found}).",
        )
    mod_name = str(m.get("module_name") or rp.stem)
    # 时钟/复位必须从 RTL 真实推断出来，不能回退到 "clk"/"rst_n" 这类猜的名字：
    # DUT 没有 reset 端口时（extract_interfaces 会报 W011 并给 reset=None），
    # 旧实现仍硬连 `.rst_n(rst_n)`，得到 `%Error-PINNOTFOUND: Pin not found:
    # 'rst_n'` —— 编译失败但工具报 ok=True。
    clk_port = (m.get("clock") or {}).get("port")
    rst_port = (m.get("reset") or {}).get("port")
    if not clk_port:
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{protocol or 'auto'}' testbench for {mod_name}: no clock "
            "port detected in the RTL (the template must drive a clock). "
            "Add a clock port (clk/aclk/hclk/...) or wire it explicitly.",
        )
    if not rst_port:
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{protocol or 'auto'}' testbench for {mod_name}: no reset "
            "port detected in the RTL. The generated template hard-wires a reset pin, "
            "so a fabricated name would not compile (PINNOTFOUND). "
            "Add a reset port (rst/rst_n/aresetn/hresetn/...) or drive the DUT without one.",
        )
    clk = str(clk_port)
    rst = str(rst_port)
    proto = str(chosen["protocol"])

    # 生成前校验 TB 模板硬连的端口在 DUT 上确实存在，避免产出无法编译的 TB
    # 却仍报 ok=True（Verilator PINNOTFOUND 才是最终判据，但那已经太晚）。
    # extract_interfaces 的模块数据不含 ports 字段（只有 total_ports），所以
    # 端口名与方向都要从 RTL 直接解析一次拿到（缺端口校验与方向校验都需要）。
    from .core.sv_parser import parse_sv_file as _psf

    rtl_ports: List[str] = []
    rtl_dir: Dict[str, str] = {}
    rtl_widths: Dict[str, int] = {}
    rtl_params: Dict[str, int] = {}
    for _m in _psf(rp):
        if _m.name == mod_name:
            rtl_ports = [p.name for p in _m.ports]
            rtl_dir = {p.name: str(p.direction) for p in _m.ports}
            rtl_widths = {p.name: int(p.width) for p in _m.ports}
            rtl_params = dict(_m.parameters or {})
            break

    missing_ports = _missing_tb_ports(proto, chosen, rtl_ports)
    if missing_ports:
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{proto}' testbench for {mod_name}: DUT is missing port(s) "
            f"{', '.join(missing_ports)} that the generated template drives. "
            f"Detected RTL ports: {sorted(rtl_ports)}",
        )

    # 方向校验：方向不匹配不会编译失败，但会在运行期永久挂起（TB 等不到响应），
    # 比编译失败更隐蔽——所以必须在生成前拦住。
    wrong_dir = _wrong_dir_ports(proto, chosen, rtl_dir)
    if wrong_dir:
        return err_result(
            "emit_testbench",
            f"Cannot emit a '{proto}' testbench for {mod_name}: DUT port direction(s) "
            f"disagree with the template: {'; '.join(wrong_dir)}. "
            "A direction mismatch does not fail to compile but hangs the simulation "
            "(the testbench waits forever for a response).",
        )

    # 同 tb_emitter：空 out_dir 会写进 cwd 并报成功，必须显式拒绝。
    if not str(out_dir).strip():
        return err_result(
            "emit_testbench",
            "out_dir is required: refusing to write the generated testbench into "
            "the current working directory. Pass an explicit output directory.",
        )
    od = Path(out_dir).resolve()
    od.mkdir(parents=True, exist_ok=True)

    pcie_suites: List[str] = []
    pcie_matrix = False
    if proto == "apb":
        tb_src = _emit_apb_tb(mod_name, clk, rst, chosen)
    elif proto == "axi4":
        tb_src = _emit_axi4_tb(mod_name, clk, rst, chosen)
    elif proto == "ahb_lite":
        tb_src = _emit_ahb_tb(mod_name, clk, rst, chosen)
    elif proto == "ddr3":
        tb_src = _emit_ddr3_tb(mod_name, clk, rst, chosen)
    elif proto == "ddr4":
        tb_src = _emit_ddr4_tb(mod_name, clk, rst, chosen)
    elif proto == "hbm":
        tb_src = _emit_hbm_tb(mod_name, clk, rst, chosen)
    elif proto == "pcie":
        tb_src, pcie_suites, pcie_matrix = _emit_pcie_tb(
            mod_name, clk, rst, chosen,
            dut_params=rtl_params, port_widths=rtl_widths,
        )
    else:
        return err_result("emit_testbench", f"Unsupported protocol: {proto}")

    # 复位极性：active-low 的模板形态是「声明 `, rst = 0` + 释放时 `rst = 1;`」。
    # active-high 时必须反过来。
    #
    # 这里**不再只靠**两处字符串 replace：模板缩进/写法一变（比如声明行不再是
    # `, {rst} = 0`、initial 里不再是 4 空格缩进），replace 会静默匹配不到，
    # 生成的 TB 就带着反相的复位跑下去 —— 仿真不报错，只是 DUT 永远停在复位
    # （或永远不复位），表现为莫名其妙的 FAIL/超时。因此改成：
    #   1) 先用正则按「语义」改写（容忍任意缩进/空格）；
    #   2) 改写后断言确实改到了（否则这条证据链就是断的），改不到就返回错误，
    #      绝不静默生成一份极性不可信的 TB。
    rst_active_low = bool((m.get("reset") or {}).get("active_low", True))
    if not rst_active_low:
        _before = tb_src
        # 声明处：`, rst = 0` -> `, rst = 1`（允许任意空白）
        tb_src, _n1 = re.subn(
            rf"(,\s*{re.escape(rst)}\s*=\s*)0\b", r"\g<1>1", tb_src, count=1)
        # 释放处：行首缩进 + `rst = 1;` -> `rst = 0;`
        tb_src, _n2 = re.subn(
            rf"^([ \t]*){re.escape(rst)}\s*=\s*1\s*;",
            lambda mm: f"{mm.group(1)}{rst} = 0;", tb_src, count=1, flags=re.M)
        if _n1 == 0 or _n2 == 0:
            return err_result(
                "emit_testbench",
                f"Cannot honour active-high reset '{rst}' for module {mod_name}: the "
                f"'{proto}' template's reset declaration/release no longer matches the "
                "expected shape, so the generated testbench would drive the reset with "
                "the wrong polarity (simulation would hang or never leave reset). "
                "Refusing to emit an unverified artifact.",
                data={"protocol": proto, "reset": rst, "matched_decl": _n1,
                      "matched_release": _n2, "template_changed": tb_src != _before},
            )

    # 给生成的 TB 插入 PINMISSING lint_off：DUT 常有 TB 不关心的额外端口
    # （调试/测试/scan 模式），`.*` 通配覆盖不到，Verilator 会报 PINMISSING
    # 并以 "Exiting due to N warning(s)" 阻断构建，而工具已报 ok=True。
    top_module = f"tb_{mod_name}_{proto}"
    tb_path = od / f"{top_module}.sv"
    # 先注入波形 dump，再套 lint_off：dump 要落在 initial 块内，
    # 而 lint_off 改的是 module 头部之后的 pragma 区，两者互不干扰。
    tb_with_dump = add_waveform_dump(tb_src, top_module)
    tb_path.write_text(add_lint_off(tb_with_dump), encoding="utf-8")

    sim_path = od / f"run_{mod_name}_{proto}.sh"
    sim_path.write_text(
        _emit_run_script(proto, mod_name, rp, tb_path, pcie_suites=pcie_suites, pcie_matrix=pcie_matrix),
        encoding="utf-8",
    )
    os.chmod(sim_path, os.stat(sim_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    suites = pcie_suites if proto == "pcie" else [proto]
    return ok_result(
        "emit_testbench",
        {
            "module_name": mod_name,
            "protocol": proto,
            "clock": clk,
            "reset": rst,
            "suites": suites,
            "artifacts": {
                "sv_testbench": str(tb_path),
                "sim_script": str(sim_path),
            },
        },
    )
