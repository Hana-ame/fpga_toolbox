`timescale 1ns/1ps
module tb_axi4_ooo_reorder;
  logic        aclk = 0, aresetn = 0;
  // Upstream AXI4
  logic [3:0]  s_axi_awid = 0, s_axi_bid, s_axi_arid = 0, s_axi_rid;
  logic [15:0] s_axi_awaddr = 0, s_axi_araddr = 0;
  logic [7:0]  s_axi_awlen = 0, s_axi_arlen = 0;
  logic [2:0]  s_axi_awsize = 3'd2, s_axi_arsize = 3'd2;
  logic [1:0]  s_axi_awburst = 2'b01, s_axi_arburst = 2'b01, s_axi_bresp, s_axi_rresp;
  logic        s_axi_awvalid = 0, s_axi_awready, s_axi_wvalid = 0, s_axi_wready, s_axi_wlast = 0;
  logic        s_axi_bvalid, s_axi_bready = 1, s_axi_arvalid = 0, s_axi_arready, s_axi_rvalid, s_axi_rready = 1, s_axi_rlast;
  logic [31:0] s_axi_wdata = 0, s_axi_rdata;
  logic [3:0]  s_axi_wstrb = 4'hF;
  // Downstream AXI4 (between DUT and real axi4_full_slave backing memory)
  logic [3:0]  m_axi_awid, m_axi_bid, m_axi_arid, m_axi_rid;
  logic [15:0] m_axi_awaddr, m_axi_araddr;
  logic [7:0]  m_axi_awlen, m_axi_arlen;
  logic [2:0]  m_axi_awsize, m_axi_arsize;
  logic [1:0]  m_axi_awburst, m_axi_arburst, m_axi_bresp, m_axi_rresp;
  logic        m_axi_awvalid, m_axi_awready, m_axi_wvalid, m_axi_wready, m_axi_wlast;
  logic        m_axi_bvalid, m_axi_bready, m_axi_arvalid, m_axi_arready, m_axi_rvalid, m_axi_rready, m_axi_rlast;
  logic [31:0] m_axi_wdata, m_axi_rdata;
  logic [3:0]  m_axi_wstrb;

  axi4_ooo_reorder_fabric dut (.*);
  axi4_full_slave u_mem (
    .aclk(aclk), .aresetn(aresetn),
    .s_axi_awid(m_axi_awid), .s_axi_awaddr(m_axi_awaddr), .s_axi_awlen(m_axi_awlen),
    .s_axi_awsize(m_axi_awsize), .s_axi_awburst(m_axi_awburst), .s_axi_awvalid(m_axi_awvalid), .s_axi_awready(m_axi_awready),
    .s_axi_wdata(m_axi_wdata), .s_axi_wstrb(m_axi_wstrb), .s_axi_wlast(m_axi_wlast), .s_axi_wvalid(m_axi_wvalid), .s_axi_wready(m_axi_wready),
    .s_axi_bid(m_axi_bid), .s_axi_bresp(m_axi_bresp), .s_axi_bvalid(m_axi_bvalid), .s_axi_bready(m_axi_bready),
    .s_axi_arid(m_axi_arid), .s_axi_araddr(m_axi_araddr), .s_axi_arlen(m_axi_arlen),
    .s_axi_arsize(m_axi_arsize), .s_axi_arburst(m_axi_arburst), .s_axi_arvalid(m_axi_arvalid), .s_axi_arready(m_axi_arready),
    .s_axi_rid(m_axi_rid), .s_axi_rdata(m_axi_rdata), .s_axi_rresp(m_axi_rresp), .s_axi_rlast(m_axi_rlast), .s_axi_rvalid(m_axi_rvalid), .s_axi_rready(m_axi_rready)
  );
  always #5 aclk = ~aclk;

  byte unsigned golden_mem [0:255];
  int errs = 0;

  task automatic axi_wr(input logic [3:0] id, input logic [15:0] addr, input logic [31:0] data, input logic [3:0] strb);
    @(posedge aclk); #1;
    s_axi_awid = id; s_axi_awaddr = addr; s_axi_awlen = 0; s_axi_awvalid = 1;
    while (!s_axi_awready) begin @(posedge aclk); #1; end
    @(posedge aclk); #1; s_axi_awvalid = 0;
    s_axi_wdata = data; s_axi_wstrb = strb; s_axi_wlast = 1; s_axi_wvalid = 1;
    while (!s_axi_wready) begin @(posedge aclk); #1; end
    for (int b = 0; b < 4; b++) if (strb[b]) golden_mem[addr[7:0] + 8'(b)] = data[b*8 +: 8];
    @(posedge aclk); #1; s_axi_wvalid = 0; s_axi_wlast = 0;
    while (!(s_axi_bvalid && s_axi_bready)) begin @(posedge aclk); #1; end
    @(posedge aclk); #1;
  endtask

  function automatic logic [31:0] golden_rd(input logic [15:0] addr);
    logic [31:0] v;
    for (int b = 0; b < 4; b++) v[b*8 +: 8] = golden_mem[addr[7:0] + 8'(b)];
    return v;
  endfunction

  task automatic issue_ar(input logic [3:0] id, input logic [15:0] addr);
    s_axi_arid = id; s_axi_araddr = addr; s_axi_arlen = 0; s_axi_arvalid = 1;
    while (!s_axi_arready) begin @(posedge aclk); #1; end
    @(posedge aclk); #1;
    s_axi_arvalid = 0;
  endtask

  initial begin
    for (int i = 0; i < 256; i++) golden_mem[i] = 8'h00;
    repeat (3) @(posedge aclk);
    aresetn = 1;

    // 1. Scrambled writes + partial WSTRB overwrites across 16 words
    begin
      logic [15:0] scram_addr [0:7] = '{16'h0010, 16'h0014, 16'h0020, 16'h0024, 16'h0008, 16'h003C, 16'h0010, 16'h0014};
      logic [31:0] scram_data [0:7] = '{32'h11223344, 32'h55667788, 32'hAABBCCDD, 32'hEEFF0011, 32'hCAFEBABE, 32'h13572468, 32'hFF00EE00, 32'h00DD00CC};
      logic [3:0]  scram_strb [0:7] = '{4'hF, 4'hF, 4'hF, 4'hF, 4'hF, 4'hF, 4'b1010, 4'b0101};
      for (int i = 0; i < 8; i++) axi_wr(4'(i), scram_addr[i], scram_data[i], scram_strb[i]);
    end

    // 2. True Out-of-Order (OoO) Read: issue slow even addr 0x0010 (ID=2), then fast odd addr 0x0014 (ID=9)
    //    Expect ID=9 to overtake and return FIRST, then ID=2 SECOND!
    begin
      logic [3:0]  r_ids [0:1];
      logic [31:0] r_dat [0:1];
      int          got_cnt = 0;
      @(posedge aclk); #1;
      issue_ar(4'd2, 16'h0010); // slow even bank
      issue_ar(4'd9, 16'h0014); // fast odd bank -> overtakes!
      while (got_cnt < 2) begin
        while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge aclk); #1; end
        r_ids[got_cnt] = s_axi_rid;
        r_dat[got_cnt] = s_axi_rdata;
        got_cnt++;
        @(posedge aclk); #1;
      end
      if (r_ids[0] !== 4'd9 || r_ids[1] !== 4'd2 || r_dat[0] !== golden_rd(16'h0014) || r_dat[1] !== golden_rd(16'h0010)) begin
        $display("[CHECK] id=OOO_001 | proto=AXI4-OoO | cat=diff_id_out_of_order | tx=AR(id=2,0x10)->AR(id=9,0x14) | exp=order[9,2],0x%08h,0x%08h | got=order[%0d,%0d],0x%08h,0x%08h | status=FAIL",
                 golden_rd(16'h0014), golden_rd(16'h0010), r_ids[0], r_ids[1], r_dat[0], r_dat[1]);
        errs++;
      end else begin
        $display("[CHECK] id=OOO_001 | proto=AXI4-OoO | cat=diff_id_out_of_order | tx=AR(id=2,0x10)->AR(id=9,0x14) | exp=order[9,2],0x%08h,0x%08h | got=order[%0d,%0d],0x%08h,0x%08h | status=PASS",
                 golden_rd(16'h0014), golden_rd(16'h0010), r_ids[0], r_ids[1], r_dat[0], r_dat[1]);
      end
    end

    // 3. Same-ID In-Order Hazard Rule: issue slow even 0x0020 (ID=5) then fast odd 0x0024 (SAME ID=5)
    //    DUT MUST stall second AR until first returns -> expect 0x0020 FIRST, 0x0024 SECOND!
    begin
      logic [31:0] first_d = 0, second_d = 0;
      fork
        begin
          @(posedge aclk); #1;
          issue_ar(4'd5, 16'h0020);
          issue_ar(4'd5, 16'h0024);
        end
        begin
          while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge aclk); #1; end
          first_d = s_axi_rdata;
          @(posedge aclk); #1;
          while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge aclk); #1; end
          second_d = s_axi_rdata;
          @(posedge aclk); #1;
        end
      join
      if (first_d !== golden_rd(16'h0020) || second_d !== golden_rd(16'h0024)) begin
        $display("[CHECK] id=OOO_002 | proto=AXI4-OoO | cat=same_id_in_order | tx=AR(id=5,0x20)->AR(id=5,0x24) | exp=0x%08h,0x%08h | got=0x%08h,0x%08h | status=FAIL",
                 golden_rd(16'h0020), golden_rd(16'h0024), first_d, second_d);
        errs++;
      end else begin
        $display("[CHECK] id=OOO_002 | proto=AXI4-OoO | cat=same_id_in_order | tx=AR(id=5,0x20)->AR(id=5,0x24) | exp=0x%08h,0x%08h | got=0x%08h,0x%08h | status=PASS",
                 golden_rd(16'h0020), golden_rd(16'h0024), first_d, second_d);
      end
    end

    // 4. Shuffled full-address readback audit against golden_mem
    begin
      logic [15:0] audit_addrs [0:5] = '{16'h003C, 16'h0010, 16'h0024, 16'h0008, 16'h0014, 16'h0020};
      int bad = 0;
      for (int i = 0; i < 6; i++) begin
        @(posedge aclk); #1;
        issue_ar(4'(i + 1), audit_addrs[i]);
        while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge aclk); #1; end
        if (s_axi_rdata !== golden_rd(audit_addrs[i])) bad++;
        @(posedge aclk); #1;
      end
      if (bad > 0) begin
        $display("[CHECK] id=OOO_003 | proto=AXI4-OoO | cat=scrambled_addr_audit | tx=6 shuffled addr reads vs golden_mem | exp=6/6_match | got=%0d_mismatch | status=FAIL", bad);
        errs++;
      end else begin
        $display("[CHECK] id=OOO_003 | proto=AXI4-OoO | cat=scrambled_addr_audit | tx=6 shuffled addr reads vs golden_mem | exp=6/6_match | got=6/6_match | status=PASS");
      end
    end

    if (errs == 0) begin $display("PASS: axi4_ooo_reorder all 3 OoO/same-ID/golden checks passed"); $finish; end
    else begin $display("FAIL: axi4_ooo_reorder %0d errors", errs); $fatal(1); end
  end
endmodule
