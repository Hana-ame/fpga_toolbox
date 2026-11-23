`timescale 1ns/1ps
module tb_pcie_repack_loopback;
  logic         clk = 0, rst_n = 0;
  logic         pcie_rx_tvalid = 0, pcie_rx_tready, pcie_rx_tlast = 0;
  logic [127:0] pcie_rx_tdata = '0, pcie_tx_tdata;
  logic         pcie_tx_tvalid, pcie_tx_tready = 1, pcie_tx_tlast;
  logic         pcie_link_up;
  logic [3:0]   pcie_cfg_speed;
  logic [5:0]   pcie_cfg_lanes;

  pcie_tlp_repack_loopback #(.GEN(4), .LANES(4), .DW(128)) dut (.*);
  always #5 clk = ~clk;

  byte unsigned golden_mem [0:255];
  int errs = 0;

  task automatic golden_wr32(input logic [31:0] addr, input logic [31:0] data, input logic [3:0] be);
    for (int b = 0; b < 4; b++) if (be[b]) golden_mem[addr[7:0] + 8'(b)] = data[b*8 +: 8];
  endtask

  function automatic logic [31:0] golden_rd32(input logic [31:0] addr);
    logic [31:0] v;
    for (int b = 0; b < 4; b++) v[b*8 +: 8] = golden_mem[addr[7:0] + 8'(b)];
    return v;
  endfunction

  task automatic send_beat(input logic [127:0] data, input logic last);
    pcie_rx_tvalid = 1; pcie_rx_tdata = data; pcie_rx_tlast = last;
    while (!pcie_rx_tready) begin @(posedge clk); #1; end
    @(posedge clk); #1;
    pcie_rx_tvalid = 0; pcie_rx_tlast = 0;
  endtask

  task automatic mwr_3dw(input logic [7:0] tag, input logic [31:0] addr, input logic [3:0] fbe, input logic [31:0] d0, input logic two_dw, input logic [3:0] lbe, input logic [31:0] d1);
    logic [9:0] len = two_dw ? 10'd2 : 10'd1;
    send_beat({{3'b010, 5'd0, 14'd0, len}, {8'd0, tag, 8'd0, lbe, fbe}, addr, d0}, !two_dw);
    golden_wr32(addr, d0, fbe);
    if (two_dw) begin
      send_beat({96'd0, d1}, 1'b1);
      golden_wr32(addr + 32'd4, d1, lbe);
    end
  endtask

  task automatic mwr_4dw(input logic [7:0] tag, input logic [31:0] addr_lo, input logic [3:0] fbe, input logic [31:0] d0, input logic two_dw, input logic [3:0] lbe, input logic [31:0] d1);
    logic [9:0] len = two_dw ? 10'd2 : 10'd1;
    send_beat({{3'b011, 5'd0, 14'd0, len}, {8'd0, tag, 8'd0, lbe, fbe}, 32'h0000_0001, addr_lo}, 1'b0);
    send_beat({64'd0, d0, d1}, 1'b1);
    golden_wr32(addr_lo, d0, fbe);
    if (two_dw) golden_wr32(addr_lo + 32'd4, d1, lbe);
  endtask

  task automatic mrd_and_check(input string id_s, input string cat, input logic is_4dw, input logic [7:0] tag, input logic [31:0] addr, input logic two_dw);
    logic [9:0] len = two_dw ? 10'd2 : 10'd1;
    int pkts = two_dw ? 2 : 1;
    int bad = 0;
    logic [31:0] last_got = 0, last_exp = 0;
    if (is_4dw)
      send_beat({{3'b001, 5'd0, 14'd0, len}, {8'd0, tag, 8'd0, 4'hF, 4'hF}, 32'h0000_0001, addr}, 1'b1);
    else
      send_beat({{3'b000, 5'd0, 14'd0, len}, {8'd0, tag, 8'd0, 4'hF, 4'hF}, addr, 32'd0}, 1'b1);

    for (int p = 0; p < pkts; p++) begin
      while (!(pcie_tx_tvalid && pcie_tx_tready)) begin @(posedge clk); #1; end
      last_exp = golden_rd32(addr + 32'(p * 4));
      last_got = pcie_tx_tdata[31:0];
      if (pcie_tx_tdata[127:120] !== 8'h4A || pcie_tx_tdata[87:80] !== tag ||
          pcie_tx_tdata[63:32] !== (addr + 32'(p * 4)) || last_got !== last_exp) bad++;
      @(posedge clk); #1;
    end
    if (bad > 0) begin
      $display("[CHECK] id=%s | proto=PCIe-Repack | cat=%s | tx=%s_MRd len=%0d tag=0x%02h addr=0x%04h | exp=0x%08h | got=0x%08h(bad=%0d) | status=FAIL",
               id_s, cat, is_4dw ? "4DW" : "3DW", pkts, tag, addr[15:0], last_exp, last_got, bad);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=PCIe-Repack | cat=%s | tx=%s_MRd len=%0d tag=0x%02h addr=0x%04h | exp=0x%08h | got=0x%08h(split=%0d) | status=PASS",
               id_s, cat, is_4dw ? "4DW" : "3DW", pkts, tag, addr[15:0], last_exp, last_got, pkts);
    end
  endtask

  initial begin
    for (int i = 0; i < 256; i++) golden_mem[i] = 8'h00;
    repeat (3) @(posedge clk);
    rst_n = 1;
    @(posedge clk); #1;

    // 1. 3DW 2-DW Burst MWr at 0x0010..0x0017 -> read back via 4DW Split-CplD (cross-format!)
    mwr_3dw(8'h11, 32'h0000_0010, 4'hF, 32'h1122_3344, 1'b1, 4'hF, 32'h5566_7788);
    mrd_and_check("PCIE_RP_001", "3dw_wr_to_4dw_split_cpld", 1'b1, 8'hA1, 32'h0000_0010, 1'b1);

    // 2. 4DW 2-DW Partial-BE Overwrite on SAME address 0x0010..0x0017 (fbe=0101, lbe=1010)
    mwr_4dw(8'h22, 32'h0000_0010, 4'b0101, 32'h00AA_00BB, 1'b1, 4'b1010, 32'hCC00_DD00);
    mrd_and_check("PCIE_RP_002", "4dw_partial_be_overwrite", 1'b0, 8'hB2, 32'h0000_0010, 1'b1);

    // 3. Scrambled multi-format writes across scattered addresses (0x0040, 0x0024, 0x0060, 0x0004)
    mwr_4dw(8'h33, 32'h0000_0040, 4'hF, 32'hDEAD_BEEF, 1'b1, 4'b0011, 32'hFFFF_CAFE);
    mwr_3dw(8'h34, 32'h0000_0024, 4'b1100, 32'hABCD_0000, 1'b0, 4'h0, 32'h0);
    mwr_3dw(8'h35, 32'h0000_0060, 4'hF, 32'h1234_5678, 1'b1, 4'hF, 32'h9ABC_DEF0);
    mwr_4dw(8'h36, 32'h0000_0004, 4'b1001, 32'hEE00_0077, 1'b0, 4'h0, 32'h0);

    // 4. Shuffled-tag cross-format loopback readback of every touched address
    mrd_and_check("PCIE_RP_003", "shuffled_tag_4dw_split", 1'b1, 8'hF3, 32'h0000_0060, 1'b1);
    mrd_and_check("PCIE_RP_004", "shuffled_tag_3dw_1dw",   1'b0, 8'hC4, 32'h0000_0024, 1'b0);
    mrd_and_check("PCIE_RP_005", "shuffled_tag_4dw_partial", 1'b1, 8'hD5, 32'h0000_0040, 1'b1);
    mrd_and_check("PCIE_RP_006", "shuffled_tag_3dw_be_mask", 1'b0, 8'hE6, 32'h0000_0004, 1'b0);

    if (errs == 0) begin $display("PASS: pcie_repack_loopback all 6 3DW/4DW/Split-CplD/BE checks passed"); $finish; end
    else begin $display("FAIL: pcie_repack_loopback %0d errors", errs); $fatal(1); end
  end
endmodule
