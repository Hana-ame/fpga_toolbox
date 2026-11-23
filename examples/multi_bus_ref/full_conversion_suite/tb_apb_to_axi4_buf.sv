`timescale 1ns/1ps
module tb_apb_to_axi4_buf #(parameter int BUF_CYCS = 2);
  logic        clk = 0, rst_n = 0;
  logic        s_apb_psel = 0, s_apb_penable = 0, s_apb_pwrite = 0, s_apb_pready, s_apb_pslverr;
  logic [15:0] s_apb_paddr = 0;
  logic [31:0] s_apb_pwdata = 0, s_apb_prdata;
  logic [3:0]  s_apb_pstrb = 4'hF;
  logic [3:0]  m_axi_awid, m_axi_bid, m_axi_arid, m_axi_rid;
  logic [15:0] m_axi_awaddr, m_axi_araddr;
  logic [7:0]  m_axi_awlen, m_axi_arlen;
  logic [2:0]  m_axi_awsize, m_axi_arsize;
  logic [1:0]  m_axi_awburst, m_axi_arburst, m_axi_bresp, m_axi_rresp;
  logic        m_axi_awvalid, m_axi_awready, m_axi_wvalid, m_axi_wready, m_axi_wlast;
  logic        m_axi_bvalid, m_axi_bready, m_axi_arvalid, m_axi_arready, m_axi_rvalid, m_axi_rready, m_axi_rlast;
  logic [31:0] m_axi_wdata, m_axi_rdata;
  logic [3:0]  m_axi_wstrb;

  apb_to_axi4_buf_bridge #(.BUF_CYCS(BUF_CYCS)) dut (.*);
  axi4_full_slave u_mem (
    .aclk(clk), .aresetn(rst_n),
    .s_axi_awid(m_axi_awid), .s_axi_awaddr(m_axi_awaddr), .s_axi_awlen(m_axi_awlen),
    .s_axi_awsize(m_axi_awsize), .s_axi_awburst(m_axi_awburst), .s_axi_awvalid(m_axi_awvalid), .s_axi_awready(m_axi_awready),
    .s_axi_wdata(m_axi_wdata), .s_axi_wstrb(m_axi_wstrb), .s_axi_wlast(m_axi_wlast), .s_axi_wvalid(m_axi_wvalid), .s_axi_wready(m_axi_wready),
    .s_axi_bid(m_axi_bid), .s_axi_bresp(m_axi_bresp), .s_axi_bvalid(m_axi_bvalid), .s_axi_bready(m_axi_bready),
    .s_axi_arid(m_axi_arid), .s_axi_araddr(m_axi_araddr), .s_axi_arlen(m_axi_arlen),
    .s_axi_arsize(m_axi_arsize), .s_axi_arburst(m_axi_arburst), .s_axi_arvalid(m_axi_arvalid), .s_axi_arready(m_axi_arready),
    .s_axi_rid(m_axi_rid), .s_axi_rdata(m_axi_rdata), .s_axi_rresp(m_axi_rresp), .s_axi_rlast(m_axi_rlast), .s_axi_rvalid(m_axi_rvalid), .s_axi_rready(m_axi_rready)
  );
  always #5 clk = ~clk;

  byte unsigned golden [0:255];
  int errs = 0;

  task automatic apb_tx(input logic wr, input logic [15:0] addr, input logic [31:0] wdata, input logic [3:0] strb, output logic [31:0] rdata, output logic err, output int cycs);
    cycs = 0;
    @(posedge clk); #1;
    s_apb_psel = 1; s_apb_penable = 0; s_apb_pwrite = wr; s_apb_paddr = addr; s_apb_pwdata = wdata; s_apb_pstrb = strb;
    @(posedge clk); #1; s_apb_penable = 1;
    do begin @(posedge clk); #1; cycs++; end while (!s_apb_pready);
    rdata = s_apb_prdata; err = s_apb_pslverr;
    if (wr && !err && addr < 16'h0100) for (int b = 0; b < 4; b++) if (strb[b]) golden[addr[7:0] + 8'(b)] = wdata[b*8 +: 8];
    s_apb_psel = 0; s_apb_penable = 0;
  endtask

  initial begin
    logic [31:0] rd, exp;
    logic err;
    int cycs, bad = 0;
    for (int i = 0; i < 256; i++) golden[i] = 8'h00;
    repeat (3) @(posedge clk); rst_n = 1;

    // Full 256B write sweep (all 64 words) + scrambled partial-strobe overwrites
    for (int w = 0; w < 64; w++) begin
      apb_tx(1'b1, 16'(w * 4), 32'hA000_0000 ^ (32'h0103_070F * 32'(w + 1)), 4'hF, rd, err, cycs);
      if (err) bad++;
    end
    for (int w = 63; w >= 0; w -= 3) begin
      apb_tx(1'b1, 16'(w * 4), 32'h55AA_CC33 ^ (32'(w) << 8), (w[0] ? 4'b1010 : 4'b0101), rd, err, cycs);
      if (err) bad++;
    end
    // Full 256B readback audit vs golden[0:255]
    for (int w = 0; w < 64; w++) begin
      apb_tx(1'b0, 16'(w * 4), 32'h0, 4'hF, rd, err, cycs);
      exp = {golden[w*4+3], golden[w*4+2], golden[w*4+1], golden[w*4+0]};
      if (err || rd !== exp) bad++;
    end
    if (bad > 0 || cycs < BUF_CYCS) begin
      $display("[CHECK] id=APB2AXI_B%0d_001 | proto=APB-to-AXI4 | cat=full_256B_buf_sweep | tx=64W+partial_strb+64R (BUF_CYCS=%0d,lat=%0d) | exp=256/256B_match | got=bad=%0d | status=FAIL", BUF_CYCS, BUF_CYCS, cycs, bad); errs++;
    end else begin
      $display("[CHECK] id=APB2AXI_B%0d_001 | proto=APB-to-AXI4 | cat=full_256B_buf_sweep | tx=64W+partial_strb+64R (BUF_CYCS=%0d,lat=%0d) | exp=256/256B_match | got=256/256B_match | status=PASS", BUF_CYCS, BUF_CYCS, cycs);
    end

    // Out-of-range AXI4 SLVERR -> APB PSLVERR propagation
    apb_tx(1'b0, 16'h0200, 32'h0, 4'hF, rd, err, cycs);
    if (!err) begin
      $display("[CHECK] id=APB2AXI_B%0d_002 | proto=APB-to-AXI4 | cat=slverr_to_pslverr | tx=APB RD 0x0200 (BUF=%0d) | exp=pslverr=1 | got=pslverr=0 | status=FAIL", BUF_CYCS, BUF_CYCS); errs++;
    end else begin
      $display("[CHECK] id=APB2AXI_B%0d_002 | proto=APB-to-AXI4 | cat=slverr_to_pslverr | tx=APB RD 0x0200 (BUF=%0d) | exp=pslverr=1 | got=pslverr=1 | status=PASS", BUF_CYCS, BUF_CYCS);
    end

    if (errs == 0) begin $display("PASS: apb_to_axi4_buf(BUF=%0d) full 256B + PSLVERR passed", BUF_CYCS); $finish; end
    else begin $display("FAIL: apb_to_axi4_buf(BUF=%0d) %0d errors", BUF_CYCS, errs); $fatal(1); end
  end
endmodule
