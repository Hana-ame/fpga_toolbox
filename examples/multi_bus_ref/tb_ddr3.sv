`timescale 1ns/1ps
module tb_ddr3;
  logic        clk = 0, rst_n = 0, ddr3_cke = 1, ddr3_cs_n = 1, ddr3_ras_n = 1, ddr3_cas_n = 1, ddr3_we_n = 1, ddr3_rd_valid;
  logic [2:0]  ddr3_ba = 0;
  logic [13:0] ddr3_addr = 0;
  logic [15:0] ddr3_dq_in = 0, ddr3_dq_out;
  int          errs = 0;

  ddr3_mem_ctrl dut (.clk(clk), .rst_n(rst_n), .ddr3_ck_p(clk), .ddr3_ck_n(~clk), .*);
  always #5 clk = ~clk;

  task automatic ddr3_cmd(input logic [2:0] cmd, input logic [2:0] ba, input logic [13:0] addr, input logic [15:0] dq);
    @(posedge clk); #1;
    ddr3_cs_n = 0; {ddr3_ras_n, ddr3_cas_n, ddr3_we_n} = cmd; ddr3_ba = ba; ddr3_addr = addr; ddr3_dq_in = dq;
    @(posedge clk); #1;
    ddr3_cs_n = 1; {ddr3_ras_n, ddr3_cas_n, ddr3_we_n} = 3'b111;
  endtask

  initial begin
    repeat (3) @(posedge clk);
    rst_n = 1;
    ddr3_cmd(3'b011, 3'd2, 14'd1, 16'h0000); // ACT B2 R1
    ddr3_cmd(3'b100, 3'd2, 14'd3, 16'hBEEF); // WR B2 C3
    ddr3_cmd(3'b011, 3'd5, 14'd2, 16'h0000); // ACT B5 R2
    ddr3_cmd(3'b100, 3'd5, 14'd6, 16'h1234); // WR B5 C6
    // Read B2 C3 (CL=2)
    ddr3_cmd(3'b101, 3'd2, 14'd3, 16'h0000);
    repeat (2) @(posedge clk); #1;
    if (!ddr3_rd_valid || ddr3_dq_out !== 16'hBEEF) begin
      $display("[CHECK] id=DDR3_001 | proto=DDR3 | cat=act_wr_rd_cl2 | tx=ACT/WR/RD bank=2 col=3 CL=2 | exp=0xBEEF(valid=1) | got=0x%04h(valid=%0b) | status=FAIL", ddr3_dq_out, ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_001 | proto=DDR3 | cat=act_wr_rd_cl2 | tx=ACT/WR/RD bank=2 col=3 CL=2 | exp=0xBEEF(valid=1) | got=0x%04h(valid=%0b) | status=PASS", ddr3_dq_out, ddr3_rd_valid);
    end
    // Read B5 C6 (CL=2)
    ddr3_cmd(3'b101, 3'd5, 14'd6, 16'h0000);
    repeat (2) @(posedge clk); #1;
    if (!ddr3_rd_valid || ddr3_dq_out !== 16'h1234) begin
      $display("[CHECK] id=DDR3_002 | proto=DDR3 | cat=multi_bank_isolation | tx=ACT/WR/RD bank=5 col=6 CL=2 | exp=0x1234(valid=1) | got=0x%04h(valid=%0b) | status=FAIL", ddr3_dq_out, ddr3_rd_valid);
      errs++;
    end else begin
      $display("[CHECK] id=DDR3_002 | proto=DDR3 | cat=multi_bank_isolation | tx=ACT/WR/RD bank=5 col=6 CL=2 | exp=0x1234(valid=1) | got=0x%04h(valid=%0b) | status=PASS", ddr3_dq_out, ddr3_rd_valid);
    end
    // Precharge Bank 2 and confirm read on closed bank produces rd_valid=0
    ddr3_cmd(3'b010, 3'd2, 14'd0, 16'h0000);
    ddr3_cmd(3'b101, 3'd2, 14'd3, 16'h0000);
    repeat (2) @(posedge clk); #1;
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
