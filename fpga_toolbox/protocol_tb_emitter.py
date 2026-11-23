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
    "ddr3": (
        "ddr3_ck_p", "ddr3_ck_n", "ddr3_cke", "ddr3_cs_n", "ddr3_ras_n",
        "ddr3_cas_n", "ddr3_we_n", "ddr3_ba", "ddr3_addr", "ddr3_dq_out", "ddr3_rd_valid",
    ),
    "ddr4": (
        "ddr4_ck_t", "ddr4_ck_c", "ddr4_act_n", "ddr4_bg", "ddr4_ba", "ddr4_adr",
        "ddr4_cs_n", "ddr4_odt", "ddr4_dq_out", "ddr4_dm_dbi_n", "ddr4_rd_valid",
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
    aw = int(iface.get("addr_width") or 16)
    dw = int(iface.get("data_width") or 32)
    sw = max(1, dw // 8)
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
    aw = int(iface.get("addr_width") or 16)
    dw = int(iface.get("data_width") or 32)
    sw = max(1, dw // 8)
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
  logic [3:0]    {p['awid']} = 0, {p['bid']}, {p['arid']} = 0, {p['rid']};
  logic [{aw-1}:0] {p['awaddr']} = '0, {p['araddr']} = '0;
  logic [7:0]    {p['awlen']} = 0, {p['arlen']} = 0;
  logic [2:0]    {p['awsize']} = 3'd2, {p['arsize']} = 3'd2;
  logic [1:0]    {p['awburst']} = 2'b01, {p['arburst']} = 2'b01, {p['bresp']}, {p['rresp']};
  logic          {p['awvalid']} = 0, {p['awready']}, {p['wvalid']} = 0, {p['wready']}, {p['wlast']} = 0;
  logic          {p['bvalid']}, {p['bready']} = 1, {p['arvalid']} = 0, {p['arready']}, {p['rvalid']}, {p['rready']} = 1, {p['rlast']};
  logic [{dw-1}:0] {p['wdata']} = '0, {p['rdata']};
  logic [{sw-1}:0] {p['wstrb']} = '1;
  int            errs = 0;

  {mod} dut (.*);
  always #5 {clk} = ~{clk};

  task automatic axi_wr_burst(input string id_s, input logic [3:0] id, input logic [{aw-1}:0] addr, input int beats, input logic [{dw-1}:0] base_data, input logic [1:0] exp_resp);
    @(posedge {clk}); #1;
    {p['awid']} = id; {p['awaddr']} = addr; {p['awlen']} = 8'(beats - 1); {p['awburst']} = 2'b01; {p['awvalid']} = 1;
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
    if ({p['bid']} !== id || {p['bresp']} !== exp_resp) begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_write | tx=INCR%0d id=%0d addr=0x%04h | exp=id:%0d,bresp:%0d | got=id:%0d,bresp:%0d | status=FAIL",
               id_s, beats, id, addr, id, exp_resp, {p['bid']}, {p['bresp']});
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_write | tx=INCR%0d id=%0d addr=0x%04h | exp=id:%0d,bresp:%0d | got=id:%0d,bresp:%0d | status=PASS",
               id_s, beats, id, addr, id, exp_resp, {p['bid']}, {p['bresp']});
    end
    @(posedge {clk}); #1;
  endtask

  task automatic axi_rd_burst(input string id_s, input logic [3:0] id, input logic [{aw-1}:0] addr, input int beats, input logic [{dw-1}:0] base_data, input logic [1:0] exp_resp);
    int bad = 0;
    logic [{dw-1}:0] last_got = '0;
    @(posedge {clk}); #1;
    {p['arid']} = id; {p['araddr']} = addr; {p['arlen']} = 8'(beats - 1); {p['arburst']} = 2'b01; {p['arvalid']} = 1;
    while (!{p['arready']}) begin @(posedge {clk}); #1; end
    @(posedge {clk}); #1;
    {p['arvalid']} = 0;
    for (int i = 0; i < beats; i++) begin
      while (!({p['rvalid']} && {p['rready']})) begin @(posedge {clk}); #1; end
      last_got = {p['rdata']};
      if ({p['rid']} !== id || {p['rresp']} !== exp_resp || (exp_resp == 2'b00 && {p['rdata']} !== (base_data + {dw}'(i))) || ({p['rlast']} !== (i == beats - 1))) bad++;
      @(posedge {clk}); #1;
    end
    if (bad > 0) begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_read | tx=INCR%0d id=%0d addr=0x%04h | exp=last:0x%08h,rresp:%0d | got=last:0x%08h(bad=%0d) | status=FAIL",
               id_s, beats, id, addr, base_data + {dw}'(beats - 1), exp_resp, last_got, bad);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_read | tx=INCR%0d id=%0d addr=0x%04h | exp=last:0x%08h,rresp:%0d | got=last:0x%08h,rlast=1 | status=PASS",
               id_s, beats, id, addr, base_data + {dw}'(beats - 1), exp_resp, last_got);
    end
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    axi_wr_burst("AXI4_001", 4'd3, {aw}'h0010, 4, {dw}'hA000_0000, 2'b00);
    axi_rd_burst("AXI4_002", 4'd3, {aw}'h0010, 4, {dw}'hA000_0000, 2'b00);
    axi_wr_burst("AXI4_003", 4'd7, {aw}'h0040, 8, {dw}'hBEEF_1000, 2'b00);
    axi_rd_burst("AXI4_004", 4'd7, {aw}'h0040, 8, {dw}'hBEEF_1000, 2'b00);
    axi_wr_burst("AXI4_005", 4'd9, {aw}'h0200, 1, {dw}'hDEAD_BEEF, 2'b10);
    axi_rd_burst("AXI4_006", 4'd9, {aw}'h0200, 1, {dw}'h0000_0000, 2'b10);
    if (errs == 0) begin $display("PASS: axi4_full all 6 burst/SLVERR checks passed"); $finish; end
    else begin $display("FAIL: axi4_full %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_ahb_tb(mod: str, clk: str, rst: str, iface: Dict[str, Any]) -> str:
    aw = int(iface.get("addr_width") or 16)
    dw = int(iface.get("data_width") or 32)
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
  logic          {clk} = 0, {rst} = 0, {hsel} = 0, {hwrite} = 0, {hready}, {hresp};
  logic [{aw-1}:0] {haddr} = '0;
  logic [1:0]    {htrans} = 0;
  logic [2:0]    {hsize} = 3'd2;
  logic [{dw-1}:0] {hwdata} = '0, {hrdata};
  int            errs = 0;

  {mod} dut (.*);
  always #5 {clk} = ~{clk};

  task automatic ahb_write(input string id, input string cat, input logic [{aw-1}:0] addr, input logic [{dw-1}:0] data, input logic exp_err);
    @(posedge {clk}); #1;
    {hsel} = 1; {htrans} = 2'b10; {hwrite} = 1; {haddr} = addr;
    @(posedge {clk}); #1;
    {hsel} = 0; {htrans} = 2'b00; {hwdata} = data;
    do begin @(posedge {clk}); #1; end while (!{hready});
    if ({hresp} !== exp_err) begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=WR addr=0x%04h data=0x%08h | exp=HRESP:%0b | got=HRESP:%0b | status=FAIL", id, cat, addr, data, exp_err, {hresp});
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=WR addr=0x%04h data=0x%08h | exp=HRESP:%0b | got=HRESP:%0b | status=PASS", id, cat, addr, data, exp_err, {hresp});
    end
  endtask

  task automatic ahb_read(input string id, input string cat, input logic [{aw-1}:0] addr, input logic [{dw-1}:0] exp_data, input logic exp_err);
    @(posedge {clk}); #1;
    {hsel} = 1; {htrans} = 2'b10; {hwrite} = 0; {haddr} = addr;
    @(posedge {clk}); #1;
    {hsel} = 0; {htrans} = 2'b00;
    do begin @(posedge {clk}); #1; end while (!{hready});
    if ({hresp} !== exp_err || (!exp_err && {hrdata} !== exp_data)) begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=RD addr=0x%04h | exp=0x%08h(HRESP=%0b) | got=0x%08h(HRESP=%0b) | status=FAIL", id, cat, addr, exp_data, exp_err, {hrdata}, {hresp});
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=RD addr=0x%04h | exp=0x%08h(HRESP=%0b) | got=0x%08h(HRESP=%0b) | status=PASS", id, cat, addr, exp_data, exp_err, {hrdata}, {hresp});
    end
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    ahb_read ("AHB_001", "reset_read",  {aw}'h0000, {dw}'h11223344, 1'b0);
    ahb_write("AHB_002", "rw_write",    {aw}'h0000, {dw}'hA5A55A5A, 1'b0);
    ahb_read ("AHB_003", "rw_readback", {aw}'h0000, {dw}'hA5A55A5A, 1'b0);
    ahb_write("AHB_004", "ro_write",    {aw}'h0004, {dw}'hFFFFFFFF, 1'b0);
    ahb_read ("AHB_005", "ro_protect",  {aw}'h0004, {dw}'hCAFEBABE, 1'b0);
    ahb_read ("AHB_006", "hresp_error", {aw}'h0040, {dw}'h00000000, 1'b1);
    if (errs == 0) begin $display("PASS: ahb_lite all 6 checks passed"); $finish; end
    else begin $display("FAIL: ahb_lite %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_ddr3_tb(mod: str, clk: str, rst: str) -> str:
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=ddr3)
module tb_{mod}_ddr3;
  logic        {clk} = 0, {rst} = 0, ddr3_cke = 1, ddr3_cs_n = 1, ddr3_ras_n = 1, ddr3_cas_n = 1, ddr3_we_n = 1, ddr3_rd_valid;
  logic [2:0]  ddr3_ba = 0;
  logic [13:0] ddr3_addr = 0;
  logic [15:0] ddr3_dq_in = 0, ddr3_dq_out;
  int          errs = 0;

  {mod} dut (.{clk}({clk}), .{rst}({rst}), .ddr3_ck_p({clk}), .ddr3_ck_n(~{clk}), .*);
  always #5 {clk} = ~{clk};

  task automatic ddr3_cmd(input logic [2:0] cmd, input logic [2:0] ba, input logic [13:0] addr, input logic [15:0] dq);
    @(posedge {clk}); #1;
    ddr3_cs_n = 0; {{ddr3_ras_n, ddr3_cas_n, ddr3_we_n}} = cmd; ddr3_ba = ba; ddr3_addr = addr; ddr3_dq_in = dq;
    @(posedge {clk}); #1;
    ddr3_cs_n = 1; {{ddr3_ras_n, ddr3_cas_n, ddr3_we_n}} = 3'b111;
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    ddr3_cmd(3'b011, 3'd2, 14'd1, 16'h0000);
    ddr3_cmd(3'b100, 3'd2, 14'd3, 16'hBEEF);
    ddr3_cmd(3'b011, 3'd5, 14'd2, 16'h0000);
    ddr3_cmd(3'b100, 3'd5, 14'd6, 16'h1234);
    ddr3_cmd(3'b101, 3'd2, 14'd3, 16'h0000);
    repeat (2) @(posedge {clk}); #1;
    if (!ddr3_rd_valid || ddr3_dq_out !== 16'hBEEF) begin
      $display("[CHECK] id=DDR3_001 | proto=DDR3 | cat=act_wr_rd_cl2 | tx=ACT/WR/RD bank=2 col=3 CL=2 | exp=0xBEEF(valid=1) | got=0x%04h(valid=%0b) | status=FAIL", ddr3_dq_out, ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_001 | proto=DDR3 | cat=act_wr_rd_cl2 | tx=ACT/WR/RD bank=2 col=3 CL=2 | exp=0xBEEF(valid=1) | got=0x%04h(valid=%0b) | status=PASS", ddr3_dq_out, ddr3_rd_valid);
    end
    ddr3_cmd(3'b101, 3'd5, 14'd6, 16'h0000);
    repeat (2) @(posedge {clk}); #1;
    if (!ddr3_rd_valid || ddr3_dq_out !== 16'h1234) begin
      $display("[CHECK] id=DDR3_002 | proto=DDR3 | cat=multi_bank_isolation | tx=ACT/WR/RD bank=5 col=6 CL=2 | exp=0x1234(valid=1) | got=0x%04h(valid=%0b) | status=FAIL", ddr3_dq_out, ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_002 | proto=DDR3 | cat=multi_bank_isolation | tx=ACT/WR/RD bank=5 col=6 CL=2 | exp=0x1234(valid=1) | got=0x%04h(valid=%0b) | status=PASS", ddr3_dq_out, ddr3_rd_valid);
    end
    ddr3_cmd(3'b010, 3'd2, 14'd0, 16'h0000);
    ddr3_cmd(3'b101, 3'd2, 14'd3, 16'h0000);
    repeat (2) @(posedge {clk}); #1;
    if (ddr3_rd_valid !== 1'b0) begin
      $display("[CHECK] id=DDR3_003 | proto=DDR3 | cat=precharge_close | tx=PRE bank=2 then RD | exp=valid=0 | got=valid=%0b | status=FAIL", ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_003 | proto=DDR3 | cat=precharge_close | tx=PRE bank=2 then RD | exp=valid=0 | got=valid=%0b | status=PASS", ddr3_rd_valid);
    end
    if (errs == 0) begin $display("PASS: ddr3 ACT/WR/RD(CL=2)/PRE all checks passed"); $finish; end
    else begin $display("FAIL: ddr3 %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_ddr4_tb(mod: str, clk: str, rst: str) -> str:
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=ddr4)
module tb_{mod}_ddr4;
  logic        {clk} = 0, {rst} = 0, ddr4_act_n = 1, ddr4_cs_n = 1, ddr4_odt = 0, ddr4_rd_valid;
  logic [1:0]  ddr4_bg = 0, ddr4_ba = 0;
  logic [16:0] ddr4_adr = 0;
  logic [63:0] ddr4_dq_in = 0, ddr4_dq_out;
  logic [7:0]  ddr4_dm_dbi_n = 8'hFF;
  int          errs = 0;

  {mod} dut (.{clk}({clk}), .{rst}({rst}), .ddr4_ck_t({clk}), .ddr4_ck_c(~{clk}), .*);
  always #5 {clk} = ~{clk};

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_act_n = 0; ddr4_bg = 2; ddr4_ba = 1; ddr4_adr = 17'h0;
    @(posedge {clk}); #1; ddr4_cs_n = 1; ddr4_act_n = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {{3'b100, 12'd0, 2'd1}}; ddr4_dm_dbi_n = 8'hFF; ddr4_dq_in = 64'hDEADBEEF_CAFE0123;
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {{3'b101, 12'd0, 2'd1}};
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    if (!ddr4_rd_valid || ddr4_dq_out !== 64'hDEADBEEF_CAFE0123) begin
      $display("[CHECK] id=DDR4_001 | proto=DDR4 | cat=bg_ba_full_rw | tx=ACT(BG=2,BA=1)+WR+RD 64b | exp=0xDEADBEEF_CAFE0123 | got=0x%016h(v=%0b) | status=FAIL", ddr4_dq_out, ddr4_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR4_001 | proto=DDR4 | cat=bg_ba_full_rw | tx=ACT(BG=2,BA=1)+WR+RD 64b | exp=0xDEADBEEF_CAFE0123 | got=0x%016h(v=%0b) | status=PASS", ddr4_dq_out, ddr4_rd_valid);
    end
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {{3'b100, 12'd0, 2'd1}}; ddr4_dm_dbi_n = 8'h01; ddr4_dq_in = 64'h00000000_00000099;
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    @(posedge {clk}); #1; ddr4_cs_n = 0; ddr4_adr = {{3'b101, 12'd0, 2'd1}};
    @(posedge {clk}); #1; ddr4_cs_n = 1;
    if (!ddr4_rd_valid || ddr4_dq_out !== 64'hDEADBEEF_CAFE0199) begin
      $display("[CHECK] id=DDR4_002 | proto=DDR4 | cat=dm_dbi_byte_mask | tx=WR dm_dbi_n=0x01 data=0x99 -> RD | exp=0xDEADBEEF_CAFE0199 | got=0x%016h(v=%0b) | status=FAIL", ddr4_dq_out, ddr4_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR4_002 | proto=DDR4 | cat=dm_dbi_byte_mask | tx=WR dm_dbi_n=0x01 data=0x99 -> RD | exp=0xDEADBEEF_CAFE0199 | got=0x%016h(v=%0b) | status=PASS", ddr4_dq_out, ddr4_rd_valid);
    end
    if (errs == 0) begin $display("PASS: ddr4 BG/BA + DM_DBI_n masked write/readback passed"); $finish; end
    else begin $display("FAIL: ddr4 %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_hbm_tb(mod: str, clk: str, rst: str) -> str:
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=hbm)
module tb_{mod}_hbm;
  logic         {clk} = 0, {rst} = 0;
  logic [5:0]   hbm_awid = 0, hbm_bid, hbm_arid = 0, hbm_rid;
  logic [27:0]  hbm_awaddr = 0, hbm_araddr = 0;
  logic [255:0] hbm_wdata = 0, hbm_rdata;
  logic         hbm_awvalid = 0, hbm_awready, hbm_wvalid = 0, hbm_wready, hbm_bvalid, hbm_bready = 1;
  logic         hbm_arvalid = 0, hbm_arready, hbm_rvalid, hbm_rready = 1;
  int           errs = 0;

  {mod} dut (.*);
  always #5 {clk} = ~{clk};

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    @(posedge {clk}); #1;
    hbm_awid = 6'h2A; hbm_awaddr = 28'h000_0060; hbm_awvalid = 1;
    hbm_wdata = {{4{{64'h01234567_89ABCDEF}}}}; hbm_wvalid = 1;
    @(posedge {clk}); #1;
    hbm_awvalid = 0; hbm_wvalid = 0;
    if (!hbm_bvalid || hbm_bid !== 6'h2A) begin
      $display("[CHECK] id=HBM_001 | proto=HBM | cat=pc256_write_id | tx=WR256 id=0x2A addr=0x0000060 | exp=bvalid=1,bid=0x2A | got=bvalid=%0b,bid=0x%02h | status=FAIL", hbm_bvalid, hbm_bid);
      errs++;
    end else begin
      $display("[CHECK] id=HBM_001 | proto=HBM | cat=pc256_write_id | tx=WR256 id=0x2A addr=0x0000060 | exp=bvalid=1,bid=0x2A | got=bvalid=%0b,bid=0x%02h | status=PASS", hbm_bvalid, hbm_bid);
    end
    @(posedge {clk}); #1;
    hbm_arid = 6'h15; hbm_araddr = 28'h000_0060; hbm_arvalid = 1; hbm_rready = 0;
    @(posedge {clk}); #1;
    hbm_arvalid = 0;
    @(posedge {clk}); #1;
    if (!hbm_rvalid || hbm_rid !== 6'h15 || hbm_rdata !== {{4{{64'h01234567_89ABCDEF}}}}) begin
      $display("[CHECK] id=HBM_002 | proto=HBM | cat=pc256_read_backpressure | tx=RD256 id=0x15 rready=0 hold | exp=rvalid=1,rid=0x15,lo64=0x0123456789ABCDEF | got=rvalid=%0b,rid=0x%02h,lo64=0x%016h | status=FAIL", hbm_rvalid, hbm_rid, hbm_rdata[63:0]);
      errs++;
    end else begin
      $display("[CHECK] id=HBM_002 | proto=HBM | cat=pc256_read_backpressure | tx=RD256 id=0x15 rready=0 hold | exp=rvalid=1,rid=0x15,lo64=0x0123456789ABCDEF | got=rvalid=%0b,rid=0x%02h,lo64=0x%016h | status=PASS", hbm_rvalid, hbm_rid, hbm_rdata[63:0]);
    end
    hbm_rready = 1;
    @(posedge {clk});
    if (errs == 0) begin $display("PASS: hbm 256-bit pseudo-channel ID+data+backpressure passed"); $finish; end
    else begin $display("FAIL: hbm %0d errors", errs); $fatal(1); end
  end
endmodule
"""


def _emit_pcie_tb(mod: str, clk: str, rst: str) -> str:
    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.protocol_tb_emitter (protocol=pcie, 3x3 Gen3/4/5 x x1/x4/x16)
module tb_{mod}_pcie #(
  parameter int GEN   = 4,
  parameter int LANES = 4
);
  localparam int DW = (LANES == 16) ? 512 : ((LANES == 4) ? 128 : 64);
  logic          {clk} = 0, {rst} = 0;
  logic          pcie_rx_tvalid = 0, pcie_rx_tready, pcie_rx_tlast = 0;
  logic [DW-1:0] pcie_rx_tdata = '0, pcie_tx_tdata;
  logic          pcie_tx_tvalid, pcie_tx_tready = 1, pcie_tx_tlast;
  logic          pcie_link_up;
  logic [3:0]    pcie_cfg_speed;
  logic [5:0]    pcie_cfg_lanes;
  int            errs = 0;

  {mod} #(.GEN(GEN), .LANES(LANES), .DW(DW)) dut (.*);
  always #5 {clk} = ~{clk};

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    @(posedge {clk}); #1;
    if (!pcie_link_up || pcie_cfg_speed !== 4'(GEN) || pcie_cfg_lanes !== 6'(LANES)) begin
      $display("[CHECK] id=PCIE_G%0dX%0d_001 | proto=PCIe-Gen%0d-x%0d | cat=link_cfg | tx=LTSSM link_up check (DW=%0d) | exp=up=1,Gen%0d,x%0d | got=up=%0b,Gen%0d,x%0d | status=FAIL",
               GEN, LANES, GEN, LANES, DW, GEN, LANES, pcie_link_up, pcie_cfg_speed, pcie_cfg_lanes);
      errs++;
    end else begin
      $display("[CHECK] id=PCIE_G%0dX%0d_001 | proto=PCIe-Gen%0d-x%0d | cat=link_cfg | tx=LTSSM link_up check (DW=%0d) | exp=up=1,Gen%0d,x%0d | got=up=%0b,Gen%0d,x%0d | status=PASS",
               GEN, LANES, GEN, LANES, DW, GEN, LANES, pcie_link_up, pcie_cfg_speed, pcie_cfg_lanes);
    end
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1;
    pcie_rx_tdata = '0; pcie_rx_tdata[63:0] = {{8'h40, 8'h07, 16'h0010, 32'h55AA1234}};
    @(posedge {clk}); #1;
    pcie_rx_tdata[63:0] = {{8'h00, 8'h3C, 16'h0010, 32'h0}};
    @(posedge {clk}); #1;
    pcie_rx_tvalid = 0; pcie_rx_tlast = 0;
    if (!pcie_tx_tvalid || !pcie_tx_tlast || pcie_tx_tdata[63:0] !== {{8'h4A, 8'h3C, 16'h0010, 32'h55AA1234}}) begin
      $display("[CHECK] id=PCIE_G%0dX%0d_002 | proto=PCIe-Gen%0d-x%0d | cat=tlp_mwr_mrd_cpld | tx=MWr(0x0010,0x55AA1234)+MRd(tag=0x3C) | exp=CplD(0x4A3C001055AA1234) | got=0x%016h(v=%0b,last=%0b) | status=FAIL",
               GEN, LANES, GEN, LANES, pcie_tx_tdata[63:0], pcie_tx_tvalid, pcie_tx_tlast);
      errs++;
    end else begin
      $display("[CHECK] id=PCIE_G%0dX%0d_002 | proto=PCIe-Gen%0d-x%0d | cat=tlp_mwr_mrd_cpld | tx=MWr(0x0010,0x55AA1234)+MRd(tag=0x3C) | exp=CplD(0x4A3C001055AA1234) | got=0x%016h(v=%0b,last=%0b) | status=PASS",
               GEN, LANES, GEN, LANES, pcie_tx_tdata[63:0], pcie_tx_tvalid, pcie_tx_tlast);
    end
    if (errs == 0) begin $display("PASS: pcie Gen%0d x%0d (DW=%0d) MWr/MRd/CplD passed", GEN, LANES, DW); $finish; end
    else begin $display("FAIL: pcie Gen%0d x%0d %0d errors", GEN, LANES, errs); $fatal(1); end
  end
endmodule
"""


def _emit_run_script(proto: str, mod: str, rtl_abs: Path, tb_abs: Path) -> str:
    top = f"tb_{mod}_{proto}"
    if proto == "pcie":
        cases: List[str] = []
        all_names: List[str] = []
        for gen in (3, 4, 5):
            for lanes in (1, 4, 16):
                sname = f"pcie_g{gen}x{lanes}"
                all_names.append(sname)
                cases.append(
                    f'  {sname}) run_one {sname} {top} -GGEN={gen} -GLANES={lanes} "{rtl_abs}" "{tb_abs}" ;;'
                )
        cases_s = "\n".join(cases)
        all_s = " ".join(all_names)
    else:
        sname = proto
        all_s = sname
        cases_s = f'  {sname}) run_one {sname} {top} "{rtl_abs}" "{tb_abs}" ;;'

    return f"""#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
VLT="${{VERILATOR:-verilator}}"

run_one() {{
  local name="$1"; shift
  local top="$1"; shift
  local out="$HERE/obj_$name"
  local bin="$out/V$top"
  local need_build=0
  if [ ! -x "$bin" ]; then
    need_build=1
  else
    for arg in "$@"; do
      if [ -f "$arg" ] && [ "$arg" -nt "$bin" ]; then
        need_build=1
      fi
    done
  fi
  echo "== sim: $name (top=$top) =="
  if [ "$need_build" -eq 1 ]; then
    rm -rf "$out"
    "$VLT" --binary --build-jobs 4 -Wno-UNUSED -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME --top-module "$top" -Mdir "$out" "$@" >/dev/null
  fi
  "$bin"
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

    m = mods[0]
    mod_name = str(m.get("module_name") or rp.stem)
    clk = str(((m.get("clock") or {}).get("port")) or "clk")
    rst = str(((m.get("reset") or {}).get("port")) or "rst_n")
    ifaces = m.get("interfaces") or []

    chosen = None
    if protocol and protocol != "auto":
        for i in ifaces:
            if i.get("protocol") == protocol:
                chosen = i
                break
        if chosen is None:
            return err_result("emit_testbench", f"Protocol '{protocol}' not found in {mod_name}.")
    else:
        for i in ifaces:
            if i.get("protocol") in SUPPORTED_MULTI_PROTOCOLS:
                chosen = i
                break
        if chosen is None:
            return err_result(
                "emit_testbench",
                f"No supported multi-bus interface found in {mod_name} (found: {[i.get('protocol') for i in ifaces]}).",
            )

    proto = str(chosen["protocol"])

    # 生成前校验 TB 模板硬连的端口在 DUT 上确实存在，避免产出无法编译的 TB
    # 却仍报 ok=True（Verilator PINNOTFOUND 才是最终判据，但那已经太晚）。
    # extract_interfaces 的模块数据不含 ports 字段（只有 total_ports），所以
    # 端口名与方向都要从 RTL 直接解析一次拿到（缺端口校验与方向校验都需要）。
    from .core.sv_parser import parse_sv_file as _psf

    rtl_ports: List[str] = []
    rtl_dir: Dict[str, str] = {}
    for _m in _psf(rp):
        if _m.name == mod_name:
            rtl_ports = [p.name for p in _m.ports]
            rtl_dir = {p.name: str(p.direction) for p in _m.ports}
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

    od = Path(out_dir).resolve()
    od.mkdir(parents=True, exist_ok=True)

    if proto == "apb":
        tb_src = _emit_apb_tb(mod_name, clk, rst, chosen)
    elif proto == "axi4":
        tb_src = _emit_axi4_tb(mod_name, clk, rst, chosen)
    elif proto == "ahb_lite":
        tb_src = _emit_ahb_tb(mod_name, clk, rst, chosen)
    elif proto == "ddr3":
        tb_src = _emit_ddr3_tb(mod_name, clk, rst)
    elif proto == "ddr4":
        tb_src = _emit_ddr4_tb(mod_name, clk, rst)
    elif proto == "hbm":
        tb_src = _emit_hbm_tb(mod_name, clk, rst)
    elif proto == "pcie":
        tb_src = _emit_pcie_tb(mod_name, clk, rst)
    else:
        return err_result("emit_testbench", f"Unsupported protocol: {proto}")

    tb_path = od / f"tb_{mod_name}_{proto}.sv"
    tb_path.write_text(tb_src, encoding="utf-8")

    sim_path = od / f"run_{mod_name}_{proto}.sh"
    sim_path.write_text(_emit_run_script(proto, mod_name, rp, tb_path), encoding="utf-8")
    os.chmod(sim_path, os.stat(sim_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    suites = (
        [f"pcie_g{g}x{lanes}" for g in (3, 4, 5) for lanes in (1, 4, 16)]
        if proto == "pcie"
        else [proto]
    )
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
