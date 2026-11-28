"""AXI4 -> AXI4 桥接联合 TB（位宽转换 / OoO 重排器）

从原 ``emitters/bridge.py`` 拆出；模板本体逐字搬移，只补 import。
用 @@SENTINEL@@ + replace() 填充（SV 模板里大量 $display 的 %0d/%08h 与
Python % 占位符冲突，% 格式化会让占位符与参数元组错位）。
"""

from __future__ import annotations

from typing import Any, Dict

from ..common import _pm, _pw


def _emit_axi4_to_axi4_bridge_tb(
    mod: str, clk: str, rst: str,
    up: Dict[str, Any], down: Dict[str, Any],
) -> str:
    """AXI4 -> AXI4 桥接联合 TB（位宽转换 / OoO 重排器）。

    与 axi4->apb 的结构差异：下游也是 AXI4，所以 TB 要实现一个 **AXI4 从机
    模型**（而不是 APB 的 setup/access 两相模型）。从机行为按 AXI4 规范：
    AW 收地址、W 逐拍收数据并在 wlast 时回 bvalid、AR 收地址后回 rdata。

    自检重点（跨总线一致性，单侧 TB 覆盖不到）：
      1. 上游写事务必须在下游产生写（下游端口真的有流量）
      2. 下游收到的写数据必须与上游发出的**一致**（同宽逐位相等；位宽转换
         时校验低位段透传）
      3. 上游必须拿到 bvalid 响应（下游应答沿原路返回）
    """
    p_up = {k: _pm(up, k, "s_axi_" + k) for k in (
        "awid", "awaddr", "awlen", "awsize", "awburst", "awvalid", "awready",
        "wdata", "wstrb", "wlast", "wvalid", "wready",
        "bid", "bresp", "bvalid", "bready",
        "arid", "araddr", "arlen", "arsize", "arburst", "arvalid", "arready",
        "rid", "rdata", "rresp", "rlast", "rvalid", "rready")}
    p_dn = {k: _pm(down, k, "m_axi_" + k) for k in p_up}

    # 宽度字段必须覆盖模板里**每一条**网线。之前漏了 arsize/arburst/rresp，
    # 导致这三条被声明成 1 位，与 DUT 的 3/2 位端口不匹配 —— Verilator 报
    # WIDTHTRUNC/WIDTHEXPAND，且默认警告即错误，构建直接失败（实测复现）。
    # AXI4 里 arsize==awsize、arburst==awburst，取同宽即可。
    u = {"id": _pw(up, "awid", 4), "aw": _pw(up, "awaddr", 16),
         "len": _pw(up, "awlen", 8), "size": _pw(up, "awsize", 3),
         "burst": _pw(up, "awburst", 2), "dw": _pw(up, "wdata", 32),
         "sw": _pw(up, "wstrb", 4), "resp": _pw(up, "bresp", 2),
         "rresp": _pw(up, "rresp", 2)}
    d = {"id": _pw(down, "awid", u["id"]), "aw": _pw(down, "awaddr", u["aw"]),
         "len": _pw(down, "awlen", u["len"]), "size": _pw(down, "awsize", u["size"]),
         "burst": _pw(down, "awburst", u["burst"]), "dw": _pw(down, "wdata", u["dw"]),
         "sw": _pw(down, "wstrb", u["sw"]), "resp": _pw(down, "bresp", u["resp"]),
         "rresp": _pw(down, "rresp", u["rresp"])}

    uhex = max(1, (u["dw"] + 3) // 4)
    dhex = max(1, (d["dw"] + 3) // 4)
    ahex = max(1, (u["aw"] + 3) // 4)
    test_addr = 0x20 & ((1 << u["aw"]) - 1) & ((1 << d["aw"]) - 1)
    test_data_u = 0xC0FFEE00 & ((1 << u["dw"]) - 1)
    same_width = u["dw"] == d["dw"]

    up_conns = ",\n    ".join(".%s(%s)" % (p_up[k], p_up[k]) for k in p_up)
    dn_conns = ",\n    ".join(".%s(%s)" % (p_dn[k], p_dn[k]) for k in p_dn)

    up_decls = "\n".join([
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["id"]-1, p_up["awid"], u["aw"]-1, p_up["awaddr"]),
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["len"]-1, p_up["awlen"], u["size"]-1, p_up["awsize"]),
        "  logic [%d:0] %s = 0; logic %s = 0;" % (u["burst"]-1, p_up["awburst"], p_up["awvalid"]),
        "  logic %s;" % p_up["awready"],
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["dw"]-1, p_up["wdata"], u["sw"]-1, p_up["wstrb"]),
        "  logic %s = 0; logic %s = 0;" % (p_up["wlast"], p_up["wvalid"]),
        "  logic %s; logic [%d:0] %s;" % (p_up["wready"], u["id"]-1, p_up["bid"]),
        "  logic [%d:0] %s; logic %s; logic %s = 1;" % (u["resp"]-1, p_up["bresp"], p_up["bvalid"], p_up["bready"]),
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["id"]-1, p_up["arid"], u["aw"]-1, p_up["araddr"]),
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["len"]-1, p_up["arlen"], u["size"]-1, p_up["arsize"]),
        "  logic [%d:0] %s = 0; logic %s = 0;" % (u["burst"]-1, p_up["arburst"], p_up["arvalid"]),
        "  logic %s;" % p_up["arready"],
        "  logic [%d:0] %s; logic [%d:0] %s;" % (u["id"]-1, p_up["rid"], u["dw"]-1, p_up["rdata"]),
        "  logic [%d:0] %s; logic %s; logic %s;" % (u["resp"]-1, p_up["rresp"], p_up["rlast"], p_up["rvalid"]),
        "  logic %s = 1;" % p_up["rready"],
    ])
    dn_decls = "\n".join([
        "  logic %s; logic %s = 1;" % (p_dn["awvalid"], p_dn["awready"]),
        "  logic [%d:0] %s; logic [%d:0] %s;" % (d["id"]-1, p_dn["awid"], d["aw"]-1, p_dn["awaddr"]),
        "  logic [%d:0] %s;" % (d["len"]-1, p_dn["awlen"]),
        "  logic [%d:0] %s = 0;" % (d["size"]-1, p_dn["awsize"]),
        "  logic [%d:0] %s = 0;" % (d["burst"]-1, p_dn["awburst"]),
        "  logic [%d:0] %s = 0;" % (d["size"]-1, p_dn["arsize"]),
        "  logic [%d:0] %s = 0;" % (d["burst"]-1, p_dn["arburst"]),
        "  logic %s; logic %s = 1;" % (p_dn["wvalid"], p_dn["wready"]),
        "  logic [%d:0] %s; logic [%d:0] %s;" % (d["dw"]-1, p_dn["wdata"], d["sw"]-1, p_dn["wstrb"]),
        "  logic %s;" % p_dn["wlast"],
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (d["id"]-1, p_dn["bid"], d["resp"]-1, p_dn["bresp"]),
        "  logic %s = 0; logic %s;" % (p_dn["bvalid"], p_dn["bready"]),
        "  logic %s; logic %s = 1;" % (p_dn["arvalid"], p_dn["arready"]),
        "  logic [%d:0] %s; logic [%d:0] %s;" % (d["id"]-1, p_dn["arid"], d["aw"]-1, p_dn["araddr"]),
        "  logic [%d:0] %s;" % (d["len"]-1, p_dn["arlen"]),
        "  logic [%d:0] %s = 0; logic %s = 0;" % (d["dw"]-1, p_dn["rdata"], p_dn["rvalid"]),
        "  logic [%d:0] %s = 0;" % (d["rresp"]-1, p_dn["rresp"]),
        "  logic %s; logic %s = 0;" % (p_dn["rready"], p_dn["rlast"]),
        "  logic [%d:0] %s = 0;" % (d["id"]-1, p_dn["rid"]),
    ])

    if same_width:
        data_check = ("    if (dn_wdata !== up_wdata) begin\n"
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=0x%0' + str(uhex) + 'h | got=0x%0' + str(dhex) + 'h | status=FAIL",\n'
                      "               up_wdata, dn_wdata);\n"
                      "      errs++;\n"
                      "    end else begin\n"
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=0x%0' + str(uhex) + 'h | got=0x%0' + str(dhex) + 'h | status=PASS",\n'
                      "               up_wdata, dn_wdata);\n"
                      "    end")
    else:
        data_check = ("    // 位宽不同（%d -> %d）：只校验低 %d 位透传\n" % (u["dw"], d["dw"], d["dw"]) +
                      "    if (dn_wdata !== up_wdata[%d:0]) begin\n" % (d["dw"]-1) +
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=low%d bits | got=0x%%0%dh | status=FAIL", dn_wdata);\n' % (d["dw"], dhex) +
                      "      errs++;\n"
                      "    end else begin\n"
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=low%d bits | got=0x%%0%dh | status=PASS", dn_wdata);\n' % (d["dw"], dhex) +
                      "    end")

    return """`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.emitters.bridge (protocol=axi4->axi4)
// Joint AXI4-to-AXI4 bridge TB: TB drives the upstream slave port as master and
// models an AXI4 slave on the downstream master port.
module tb_%s_bridge;
  logic %s = 0, %s = 0;
  int   errs = 0;
  int   dn_writes = 0;
  int   up_bs = 0;
  logic [%d:0] up_wdata = 0;
  logic [%d:0] dn_wdata = 0;
  logic [%d:0] dn_mem [0:15];

%s
%s

  %s dut (
    .%s(%s), .%s(%s),
    %s,
    %s
  );
  always #5 %s = ~%s;

  // ---- 下游 AXI4 从机模型（恒 ready，重点观测数据路径）----
  always @(posedge %s) begin
    if (%s) begin
      %s <= 0;
      %s <= 0;
      if (%s) begin
        %s <= %s;
      end
      if (%s) begin
        dn_writes++;
        dn_wdata = %s;
        dn_mem[%s[3:0]] = %s;
        $display("[CHECK] id=B2W | proto=axi4->axi4 | cat=downstream_write | addr=0x%%0%dh data=0x%%0%dh | status=PASS",
                 %s, %s);
        if (%s) %s <= 1;
      end
      if (%s && %s) begin
        %s <= 0;
      end
      if (%s) begin
        %s <= dn_mem[%s[3:0]];
        %s <= %s;
        %s <= 1;
        %s <= 1;
      end
      if (%s && %s) begin
        %s <= 0;
      end
    end
  end

  // ---- 上游写：单拍（awlen=0），便于断言地址/数据原样透传 ----
  task automatic axi4_write(input logic [%d:0] addr, input logic [%d:0] data);
    @(posedge %s); #1;
    %s = %d'd1; %s = addr;
    %s = %d'd0; %s = %d'd3;
    %s = %d'b01; %s = 1;
    up_wdata = data;
    while (!%s) begin @(posedge %s); #1; end
    @(posedge %s); #1;
    %s = 0;
    %s = data; %s = '1; %s = 1; %s = 1;
    while (!%s) begin @(posedge %s); #1; end
    @(posedge %s); #1;
    %s = 0; %s = 0;
    // R3：等响应必须同时查 VALID 与 READY —— 只等 bvalid 会被 lint_tb 判 R3，
    // 且真实硬件里若从机 bvalid 恒高而 master 未置 bready，事务也不会完成。
    while (!(%s && %s)) begin @(posedge %s); #1; end
    up_bs++;
    @(posedge %s); #1;
  endtask

  initial begin
    repeat (3) @(posedge %s);
    %s = 1;
    repeat (2) @(posedge %s);

    axi4_write(%d'h%0*x, %d'h%0*x);
    repeat (20) @(posedge %s);

    if (dn_writes == 0) begin
      $display("[CHECK] id=B2W_NONE | proto=axi4->axi4 | cat=downstream_write | exp=>0 | got=0 | status=FAIL");
      errs++;
    end else begin
      $display("[CHECK] id=B2W_NONE | proto=axi4->axi4 | cat=downstream_write | exp=>0 | got=%%0d | status=PASS", dn_writes);
%s
    end

    if (up_bs == 0) begin
      $display("[CHECK] id=B2B | proto=axi4->axi4 | cat=response_path | exp=bvalid | got=none | status=FAIL");
      errs++;
    end else begin
      $display("[CHECK] id=B2B | proto=axi4->axi4 | cat=response_path | exp=bvalid | got=%%0d | status=PASS", up_bs);
    end

    if (errs == 0) begin
      $display("PASS: bridge axi4->axi4 upstream write reaches %%0d downstream port(s) with data intact", dn_writes);
      $finish;
    end else begin
      $display("FAIL: bridge axi4->axi4 %%0d errors", errs);
      $fatal(1);
    end
  end

  initial begin
    #2000000;
    $display("FAIL: bridge axi4->axi4 timeout (handshake deadlock)");
    $fatal(1);
  end
endmodule
""" % (
        mod, clk, rst,
        u["dw"]-1, d["dw"]-1, d["dw"]-1,
        up_decls, dn_decls,
        mod, clk, clk, rst, rst,
        up_conns, dn_conns,
        clk, clk,
        clk, rst,
        p_dn["bvalid"], p_dn["rvalid"],
        " && ".join([p_dn["awvalid"] + " === 1'b1"]), p_dn["bid"], p_dn["awid"],
        " && ".join([p_dn["wvalid"] + " === 1'b1"]),
        p_dn["wdata"], p_dn["awaddr"], p_dn["wdata"],
        ahex, dhex, p_dn["awaddr"], p_dn["wdata"],
        p_dn["wlast"], p_dn["bvalid"],
        p_dn["bvalid"], p_dn["bready"], p_dn["bvalid"],
        p_dn["arvalid"] + " === 1'b1",
        p_dn["rdata"], p_dn["araddr"], p_dn["rid"], p_dn["arid"],
        p_dn["rlast"], p_dn["rvalid"],
        p_dn["rvalid"], p_dn["rready"], p_dn["rvalid"],
        u["aw"]-1, u["dw"]-1,
        clk,
        p_up["awid"], u["id"], p_up["awaddr"],
        p_up["awlen"], u["len"], p_up["awsize"], u["size"],
        p_up["awburst"], u["burst"], p_up["awvalid"],
        p_up["awready"], clk, clk,
        p_up["awvalid"],
        p_up["wdata"], p_up["wstrb"], p_up["wlast"], p_up["wvalid"],
        p_up["wready"], clk, clk,
        p_up["wvalid"], p_up["wlast"],
        p_up["bvalid"], p_dn["bready"], clk, clk,
        clk, rst, clk,
        u["aw"], ahex, test_addr, u["dw"], uhex, test_data_u,
        clk,
        data_check,
    )
