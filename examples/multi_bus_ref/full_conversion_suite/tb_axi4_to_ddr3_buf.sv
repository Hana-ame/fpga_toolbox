`timescale 1ns/1ps
module tb_axi4_to_ddr3_buf #(parameter int BUF_CYCS = 2);
  logic        clk = 0, por_n = 0, sr_req = 0, sr_ack, ddr3_ready, ddr3_zqcl_done;
  logic [3:0]  s_axi_awid = 0, s_axi_bid, s_axi_arid = 0, s_axi_rid;
  logic [15:0] s_axi_awaddr = 0, s_axi_araddr = 0;
  logic [7:0]  s_axi_awlen = 0, s_axi_arlen = 0;
  logic [2:0]  s_axi_awsize = 3'd2, s_axi_arsize = 3'd2;
  logic [1:0]  s_axi_awburst = 2'b01, s_axi_arburst = 2'b01, s_axi_bresp, s_axi_rresp;
  logic        s_axi_awvalid = 0, s_axi_awready, s_axi_wvalid = 0, s_axi_wready, s_axi_wlast = 0;
  logic        s_axi_bvalid, s_axi_bready = 1, s_axi_arvalid = 0, s_axi_arready, s_axi_rvalid, s_axi_rready = 1, s_axi_rlast;
  logic [31:0] s_axi_wdata = 0, s_axi_rdata;
  logic [3:0]  s_axi_wstrb = 4'hF;
  logic        ddr3_cke, ddr3_cs_n, ddr3_ras_n, ddr3_cas_n, ddr3_we_n;
  logic [2:0]  ddr3_ba;
  logic [13:0] ddr3_addr;
  logic [15:0] ddr3_dq;

  axi4_to_ddr3_buf_ctrl #(.BUF_CYCS(BUF_CYCS)) dut (.*);
  always #5 clk = ~clk;

  logic [31:0] golden [0:63];
  int errs = 0;

  task automatic axi_wr(input logic [15:0] addr, input logic [31:0] data, output logic [1:0] resp);
    @(posedge clk); #1; s_axi_awaddr = addr; s_axi_awvalid = 1;
    while (!s_axi_awready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_awvalid = 0;
    s_axi_wdata = data; s_axi_wstrb = 4'hF; s_axi_wlast = 1; s_axi_wvalid = 1;
    while (!s_axi_wready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_wvalid = 0; s_axi_wlast = 0;
    while (!(s_axi_bvalid && s_axi_bready)) begin @(posedge clk); #1; end
    resp = s_axi_bresp; @(posedge clk); #1;
  endtask

  task automatic axi_rd(input logic [15:0] addr, output logic [31:0] data, output logic [1:0] resp);
    @(posedge clk); #1; s_axi_araddr = addr; s_axi_arvalid = 1;
    while (!s_axi_arready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_arvalid = 0;
    while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge clk); #1; end
    data = s_axi_rdata; resp = s_axi_rresp; @(posedge clk); #1;
  endtask

  initial begin
    logic [31:0] rd; logic [1:0] resp; int bad = 0;
    repeat (2) @(posedge clk); #1; por_n = 1;
    // 1. POR init rejection
    axi_wr(16'h0000, 32'hBAD0_0000, resp);
    if (resp !== 2'b10) begin
      $display("[CHECK] id=AXI2DDR3_B%0d_001 | proto=AXI4-to-DDR3 | cat=por_init_guard | tx=WR during POR (BUF=%0d) | exp=SLVERR(2) | got=%0d | status=FAIL", BUF_CYCS, BUF_CYCS, resp); errs++;
    end else begin
      $display("[CHECK] id=AXI2DDR3_B%0d_001 | proto=AXI4-to-DDR3 | cat=por_init_guard | tx=WR during POR (BUF=%0d) | exp=SLVERR(2) | got=%0d | status=PASS", BUF_CYCS, BUF_CYCS, resp);
    end
    // 2. Full 256B write -> Self-Refresh sleep -> Wake -> Full 256B readback audit!
    while (!ddr3_ready) @(posedge clk);
    @(posedge clk); #1;
    for (int w = 0; w < 64; w++) begin
      golden[w] = 32'h7700_0000 ^ (32'h0503_0107 * 32'(w + 1));
      axi_wr(16'(w * 4), golden[w], resp);
      if (resp !== 2'b00) bad++;
    end
    sr_req = 1; repeat (4) @(posedge clk); #1;
    axi_rd(16'h0000, rd, resp);
    if (!sr_ack || ddr3_cke || resp !== 2'b10) bad++;
    sr_req = 0; repeat (4) @(posedge clk); #1;
    for (int w = 0; w < 64; w++) begin
      axi_rd(16'(w * 4), rd, resp);
      if (resp !== 2'b00 || rd !== golden[w]) bad++;
    end
    if (bad > 0) begin
      $display("[CHECK] id=AXI2DDR3_B%0d_002 | proto=AXI4-to-DDR3 | cat=full_256B_sr_retention | tx=64W->SR(reject)->wake->64R (BUF=%0d) | exp=256/256B_match | got=bad=%0d | status=FAIL", BUF_CYCS, BUF_CYCS, bad); errs++;
    end else begin
      $display("[CHECK] id=AXI2DDR3_B%0d_002 | proto=AXI4-to-DDR3 | cat=full_256B_sr_retention | tx=64W->SR(reject)->wake->64R (BUF=%0d) | exp=256/256B_match | got=256/256B_match | status=PASS", BUF_CYCS, BUF_CYCS);
    end
    if (errs == 0) begin $display("PASS: axi4_to_ddr3_buf(BUF=%0d) full 256B + POR + SR passed", BUF_CYCS); $finish; end
    else begin $display("FAIL: axi4_to_ddr3_buf(BUF=%0d) %0d errors", BUF_CYCS, errs); $fatal(1); end
  end
endmodule
