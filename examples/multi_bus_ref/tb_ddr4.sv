`timescale 1ns/1ps
module tb_ddr4;
  logic        clk = 0, rst_n = 0, ddr4_act_n = 1, ddr4_cs_n = 1, ddr4_odt = 0, ddr4_rd_valid;
  logic [1:0]  ddr4_bg = 0, ddr4_ba = 0;
  logic [16:0] ddr4_adr = 0;
  logic [63:0] ddr4_dq_in = 0, ddr4_dq_out;
  logic [7:0]  ddr4_dm_dbi_n = 8'hFF;
  int          errs = 0;

  ddr4_mem_ctrl dut (.clk(clk), .rst_n(rst_n), .ddr4_ck_t(clk), .ddr4_ck_c(~clk), .*);
  always #5 clk = ~clk;

  initial begin
    repeat (3) @(posedge clk);
    rst_n = 1;
    // ACT BG=2, BA=1
    @(posedge clk); #1; ddr4_cs_n = 0; ddr4_act_n = 0; ddr4_bg = 2; ddr4_ba = 1; ddr4_adr = 17'h0;
    @(posedge clk); #1; ddr4_cs_n = 1; ddr4_act_n = 1;
    // Full 64-bit WR col=1
    @(posedge clk); #1; ddr4_cs_n = 0; ddr4_adr = {3'b100, 12'd0, 2'd1}; ddr4_dm_dbi_n = 8'hFF; ddr4_dq_in = 64'hDEADBEEF_CAFE0123;
    @(posedge clk); #1; ddr4_cs_n = 1;
    // Read full 64-bit before masked write
    @(posedge clk); #1; ddr4_cs_n = 0; ddr4_adr = {3'b101, 12'd0, 2'd1};
    @(posedge clk); #1; ddr4_cs_n = 1;
    if (!ddr4_rd_valid || ddr4_dq_out !== 64'hDEADBEEF_CAFE0123) begin
      $display("[CHECK] id=DDR4_001 | proto=DDR4 | cat=bg_ba_full_rw | tx=ACT(BG=2,BA=1)+WR+RD 64b | exp=0xDEADBEEF_CAFE0123 | got=0x%016h(v=%0b) | status=FAIL", ddr4_dq_out, ddr4_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR4_001 | proto=DDR4 | cat=bg_ba_full_rw | tx=ACT(BG=2,BA=1)+WR+RD 64b | exp=0xDEADBEEF_CAFE0123 | got=0x%016h(v=%0b) | status=PASS", ddr4_dq_out, ddr4_rd_valid);
    end
    // Byte-masked WR (only low byte updated to 0x99 via DM_DBI_n=0x01)
    @(posedge clk); #1; ddr4_cs_n = 0; ddr4_adr = {3'b100, 12'd0, 2'd1}; ddr4_dm_dbi_n = 8'h01; ddr4_dq_in = 64'h00000000_00000099;
    @(posedge clk); #1; ddr4_cs_n = 1;
    @(posedge clk); #1; ddr4_cs_n = 0; ddr4_adr = {3'b101, 12'd0, 2'd1};
    @(posedge clk); #1; ddr4_cs_n = 1;
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
