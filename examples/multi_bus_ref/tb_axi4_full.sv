`timescale 1ns/1ps
module tb_axi4_full;
  logic        aclk = 0, aresetn = 0;
  logic [3:0]  s_axi_awid = 0, s_axi_bid, s_axi_arid = 0, s_axi_rid;
  logic [15:0] s_axi_awaddr = 0, s_axi_araddr = 0;
  logic [7:0]  s_axi_awlen = 0, s_axi_arlen = 0;
  logic [2:0]  s_axi_awsize = 3'd2, s_axi_arsize = 3'd2;
  logic [1:0]  s_axi_awburst = 2'b01, s_axi_arburst = 2'b01, s_axi_bresp, s_axi_rresp;
  logic        s_axi_awvalid = 0, s_axi_awready, s_axi_wvalid = 0, s_axi_wready, s_axi_wlast = 0;
  logic        s_axi_bvalid, s_axi_bready = 1, s_axi_arvalid = 0, s_axi_arready, s_axi_rvalid, s_axi_rready = 1, s_axi_rlast;
  logic [31:0] s_axi_wdata = 0, s_axi_rdata;
  logic [3:0]  s_axi_wstrb = 4'hF;
  int          errs = 0;

  axi4_full_slave dut (.*);
  always #5 aclk = ~aclk;

  task automatic axi_wr_burst(input string id_s, input logic [3:0] id, input logic [15:0] addr, input int beats, input logic [31:0] base_data, input logic [1:0] exp_resp);
    @(posedge aclk); #1;
    s_axi_awid = id; s_axi_awaddr = addr; s_axi_awlen = 8'(beats - 1); s_axi_awburst = 2'b01; s_axi_awvalid = 1;
    while (!s_axi_awready) begin @(posedge aclk); #1; end
    @(posedge aclk); #1;
    s_axi_awvalid = 0;
    for (int i = 0; i < beats; i++) begin
      s_axi_wdata = base_data + 32'(i); s_axi_wstrb = 4'hF; s_axi_wlast = (i == beats - 1); s_axi_wvalid = 1;
      while (!s_axi_wready) begin @(posedge aclk); #1; end
      @(posedge aclk); #1;
    end
    s_axi_wvalid = 0; s_axi_wlast = 0;
    while (!(s_axi_bvalid && s_axi_bready)) begin @(posedge aclk); #1; end
    if (s_axi_bid !== id || s_axi_bresp !== exp_resp) begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_write | tx=INCR%0d id=%0d addr=0x%04h | exp=id:%0d,bresp:%0d | got=id:%0d,bresp:%0d | status=FAIL",
               id_s, beats, id, addr, id, exp_resp, s_axi_bid, s_axi_bresp);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_write | tx=INCR%0d id=%0d addr=0x%04h | exp=id:%0d,bresp:%0d | got=id:%0d,bresp:%0d | status=PASS",
               id_s, beats, id, addr, id, exp_resp, s_axi_bid, s_axi_bresp);
    end
    @(posedge aclk); #1;
  endtask

  task automatic axi_rd_burst(input string id_s, input logic [3:0] id, input logic [15:0] addr, input int beats, input logic [31:0] base_data, input logic [1:0] exp_resp);
    int bad = 0;
    logic [31:0] last_got = 0;
    @(posedge aclk); #1;
    s_axi_arid = id; s_axi_araddr = addr; s_axi_arlen = 8'(beats - 1); s_axi_arburst = 2'b01; s_axi_arvalid = 1;
    while (!s_axi_arready) begin @(posedge aclk); #1; end
    @(posedge aclk); #1;
    s_axi_arvalid = 0;
    for (int i = 0; i < beats; i++) begin
      while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge aclk); #1; end
      last_got = s_axi_rdata;
      if (s_axi_rid !== id || s_axi_rresp !== exp_resp || (exp_resp == 2'b00 && s_axi_rdata !== (base_data + 32'(i))) || (s_axi_rlast !== (i == beats - 1))) bad++;
      @(posedge aclk); #1;
    end
    if (bad > 0) begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_read | tx=INCR%0d id=%0d addr=0x%04h | exp=last:0x%08h,rresp:%0d | got=last:0x%08h(bad=%0d) | status=FAIL",
               id_s, beats, id, addr, base_data + 32'(beats - 1), exp_resp, last_got, bad);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AXI4 | cat=burst_read | tx=INCR%0d id=%0d addr=0x%04h | exp=last:0x%08h,rresp:%0d | got=last:0x%08h,rlast=1 | status=PASS",
               id_s, beats, id, addr, base_data + 32'(beats - 1), exp_resp, last_got);
    end
  endtask

  initial begin
    repeat (3) @(posedge aclk);
    aresetn = 1;
    axi_wr_burst("AXI4_001", 4'd3, 16'h0010, 4, 32'hA000_0000, 2'b00);
    axi_rd_burst("AXI4_002", 4'd3, 16'h0010, 4, 32'hA000_0000, 2'b00);
    axi_wr_burst("AXI4_003", 4'd7, 16'h0040, 8, 32'hBEEF_1000, 2'b00);
    axi_rd_burst("AXI4_004", 4'd7, 16'h0040, 8, 32'hBEEF_1000, 2'b00);
    axi_wr_burst("AXI4_005", 4'd9, 16'h0200, 1, 32'hDEAD_BEEF, 2'b10); // SLVERR out-of-range
    axi_rd_burst("AXI4_006", 4'd9, 16'h0200, 1, 32'h0000_0000, 2'b10); // SLVERR out-of-range
    if (errs == 0) begin $display("PASS: axi4_full all 6 burst/SLVERR checks passed"); $finish; end
    else begin $display("FAIL: axi4_full %0d errors", errs); $fatal(1); end
  end
endmodule
