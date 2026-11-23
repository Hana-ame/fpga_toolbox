`timescale 1ns/1ps
module tb_axi4_64_to_apb_32;
  logic        aclk = 0, aresetn = 0;
  logic [3:0]  s_axi_awid = 0, s_axi_bid, s_axi_arid = 0, s_axi_rid;
  logic [15:0] s_axi_awaddr = 0, s_axi_araddr = 0;
  logic [7:0]  s_axi_awlen = 0, s_axi_arlen = 0;
  logic [2:0]  s_axi_awsize = 3'd3, s_axi_arsize = 3'd3;
  logic [1:0]  s_axi_awburst = 2'b01, s_axi_arburst = 2'b01, s_axi_bresp, s_axi_rresp;
  logic        s_axi_awvalid = 0, s_axi_awready, s_axi_wvalid = 0, s_axi_wready, s_axi_wlast = 0;
  logic        s_axi_bvalid, s_axi_bready = 1, s_axi_arvalid = 0, s_axi_arready, s_axi_rvalid, s_axi_rready = 1, s_axi_rlast;
  logic [63:0] s_axi_wdata = 0, s_axi_rdata;
  logic [7:0]  s_axi_wstrb = 8'hFF;

  // Downstream APB4 32-bit bus
  logic        m_apb_psel, m_apb_penable, m_apb_pwrite, m_apb_pready, m_apb_pslverr;
  logic [15:0] m_apb_paddr;
  logic [31:0] m_apb_pwdata, m_apb_prdata;
  logic [3:0]  m_apb_pstrb;

  // Downstream APB4 32-bit RAM slave (256 bytes = 64 words) with 1-cycle wait state
  logic [31:0] apb_ram [0:63];
  logic        apb_wait;
  int          apb_tx_cnt = 0;
  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      for (int i = 0; i < 64; i++) apb_ram[i] <= 32'h0;
      apb_wait <= 1'b0; m_apb_pready <= 1'b0; m_apb_prdata <= '0; m_apb_pslverr <= 1'b0;
    end else begin
      m_apb_pready <= 1'b0; m_apb_pslverr <= 1'b0;
      if (m_apb_psel && m_apb_penable && !apb_wait) begin
        apb_wait <= 1'b1; m_apb_pready <= 1'b1; apb_tx_cnt++;
        if (m_apb_paddr >= 16'h0100) begin
          m_apb_pslverr <= 1'b1; m_apb_prdata <= 32'h0;
        end else if (m_apb_pwrite) begin
          for (int b = 0; b < 4; b++)
            if (m_apb_pstrb[b]) apb_ram[m_apb_paddr[7:2]][b*8 +: 8] <= m_apb_pwdata[b*8 +: 8];
        end else begin
          m_apb_prdata <= apb_ram[m_apb_paddr[7:2]];
        end
      end else begin
        apb_wait <= 1'b0;
      end
    end
  end

  axi4_64_to_apb_32_bridge dut (.*);
  always #5 aclk = ~aclk;

  // Byte-addressable Golden Scoreboard (256 bytes)
  byte unsigned golden_mem [0:255];
  int errs = 0;

  task automatic golden_write64(input logic [15:0] addr, input logic [63:0] data, input logic [7:0] strb);
    for (int b = 0; b < 8; b++)
      if (strb[b]) golden_mem[addr[7:0] + 8'(b)] = data[b*8 +: 8];
  endtask

  function automatic logic [63:0] golden_read64(input logic [15:0] addr);
    logic [63:0] v;
    for (int b = 0; b < 8; b++) v[b*8 +: 8] = golden_mem[addr[7:0] + 8'(b)];
    return v;
  endfunction

  task automatic axi64_wr_burst(input logic [3:0] id, input logic [15:0] start_addr, input int beats, input logic [1:0] burst, input logic [63:0] seed, input logic [7:0] strb_mask);
    logic [15:0] cur = start_addr;
    logic [63:0] wval;
    @(posedge aclk); #1;
    s_axi_awid = id; s_axi_awaddr = start_addr; s_axi_awlen = 8'(beats - 1); s_axi_awburst = burst; s_axi_awvalid = 1;
    while (!s_axi_awready) begin @(posedge aclk); #1; end
    @(posedge aclk); #1;
    s_axi_awvalid = 0;
    for (int i = 0; i < beats; i++) begin
      wval = seed ^ (64'h01020304_05060708 * 64'(i + 1));
      s_axi_wdata = wval; s_axi_wstrb = strb_mask; s_axi_wlast = (i == beats - 1); s_axi_wvalid = 1;
      while (!s_axi_wready) begin @(posedge aclk); #1; end
      golden_write64(cur, wval, strb_mask);
      @(posedge aclk); #1;
      if (burst == 2'b01) cur = cur + 16'd8;
      else if (burst == 2'b10) cur = {cur[15:5], (cur[4:3] + 2'd1), 3'b000};
    end
    s_axi_wvalid = 0; s_axi_wlast = 0;
    while (!(s_axi_bvalid && s_axi_bready)) begin @(posedge aclk); #1; end
    @(posedge aclk); #1;
  endtask

  task automatic axi64_rd_check(input string id_s, input string cat, input logic [3:0] id, input logic [15:0] start_addr, input int beats, input logic [1:0] burst);
    logic [15:0] cur = start_addr;
    int bad = 0;
    logic [63:0] last_exp = 0, last_got = 0;
    @(posedge aclk); #1;
    s_axi_arid = id; s_axi_araddr = start_addr; s_axi_arlen = 8'(beats - 1); s_axi_arburst = burst; s_axi_arvalid = 1;
    while (!s_axi_arready) begin @(posedge aclk); #1; end
    @(posedge aclk); #1;
    s_axi_arvalid = 0;
    for (int i = 0; i < beats; i++) begin
      while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge aclk); #1; end
      last_exp = golden_read64(cur);
      last_got = s_axi_rdata;
      if (s_axi_rid !== id || s_axi_rresp !== 2'b00 || s_axi_rdata !== last_exp || s_axi_rlast !== (i == beats - 1)) bad++;
      @(posedge aclk); #1;
      if (burst == 2'b01) cur = cur + 16'd8;
      else if (burst == 2'b10) cur = {cur[15:5], (cur[4:3] + 2'd1), 3'b000};
    end
    if (bad > 0) begin
      $display("[CHECK] id=%s | proto=AXI4-64_to_APB-32 | cat=%s | tx=burst=%0d beats=%0d addr=0x%04h apb_tx=%0d | exp=0x%016h | got=0x%016h(bad=%0d) | status=FAIL",
               id_s, cat, burst, beats, start_addr, apb_tx_cnt, last_exp, last_got, bad);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AXI4-64_to_APB-32 | cat=%s | tx=burst=%0d beats=%0d addr=0x%04h apb_tx=%0d | exp=0x%016h | got=0x%016h(0_err) | status=PASS",
               id_s, cat, burst, beats, start_addr, apb_tx_cnt, last_exp, last_got);
    end
  endtask

  initial begin
    for (int i = 0; i < 256; i++) golden_mem[i] = 8'h00;
    repeat (3) @(posedge aclk);
    aresetn = 1;

    // 1. INCR4 64-bit write -> translated to 8x 32-bit APB writes
    axi64_wr_burst(4'd1, 16'h0020, 4, 2'b01, 64'hCAFEBABE_11223344, 8'hFF);
    axi64_rd_check("XLT_001", "incr4_64_to_32_repack", 4'd1, 16'h0020, 4, 2'b01);

    // 2. WRAP4 64-bit write starting at 0x0018 (wraps 0x18->0x00->0x08->0x10)
    axi64_wr_burst(4'd2, 16'h0018, 4, 2'b10, 64'hDEAD0000_BEEF55AA, 8'hFF);
    axi64_rd_check("XLT_002", "wrap4_boundary_repack", 4'd2, 16'h0018, 4, 2'b10);

    // 3. Scrambled random-order partial-WSTRB overwrite writes (12 transactions)
    begin
      logic [15:0] addrs [0:5] = '{16'h0000, 16'h0038, 16'h0010, 16'h0078, 16'h0028, 16'h0008};
      logic [7:0]  strbs [0:5] = '{8'b1010_0101, 8'b0011_1100, 8'b1100_0011, 8'b0101_1010, 8'b1111_0001, 8'b0000_1111};
      for (int k = 0; k < 6; k++) begin
        axi64_wr_burst(4'(k + 3), addrs[k], 1, 2'b01, 64'hA5A5_0000_5A5A_0000 ^ (64'h1357_9BDF_2468_ACE0 * 64'(k + 1)), strbs[k]);
      end
    end

    // 4. Full 256-byte address-by-address sweep via AXI4-64 readback (32 x 64-bit = 256 bytes)
    axi64_rd_check("XLT_003", "full_256B_addr_sweep", 4'd15, 16'h0000, 32, 2'b01);

    // 5. Direct physical APB RAM byte-by-byte verification against golden_mem[0:255]
    begin
      int ram_bad = 0;
      for (int w = 0; w < 64; w++) begin
        for (int b = 0; b < 4; b++) begin
          if (apb_ram[w][b*8 +: 8] !== golden_mem[w*4 + b]) ram_bad++;
        end
      end
      if (ram_bad > 0) begin
        $display("[CHECK] id=XLT_004 | proto=AXI4-64_to_APB-32 | cat=golden_ram_byte_audit | tx=256B physical APB RAM compare | exp=0_mismatch | got=%0d_mismatch | status=FAIL", ram_bad);
        errs++;
      end else begin
        $display("[CHECK] id=XLT_004 | proto=AXI4-64_to_APB-32 | cat=golden_ram_byte_audit | tx=256B physical APB RAM compare | exp=256/256_bytes_match | got=256/256_bytes_match | status=PASS");
      end
    end

    if (errs == 0) begin $display("PASS: axi4_64_to_apb_32 all 4 dual-IF translation/repack/golden checks passed"); $finish; end
    else begin $display("FAIL: axi4_64_to_apb_32 %0d errors", errs); $fatal(1); end
  end
endmodule
