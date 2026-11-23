`timescale 1ns/1ps
module tb_apb;
  logic        pclk = 0, presetn = 0, psel = 0, penable = 0, pwrite = 0, pready, pslverr;
  logic [15:0] paddr = 0;
  logic [31:0] pwdata = 0, prdata;
  logic [3:0]  pstrb = 4'hF;
  int          errs = 0;

  apb_slave dut (.*);
  always #5 pclk = ~pclk;

  task automatic apb_tx(
    input string id, input string cat, input logic wr,
    input logic [15:0] addr, input logic [31:0] wdata, input logic [3:0] strb,
    input logic [31:0] exp_data, input logic exp_err
  );
    int waits = 0;
    @(posedge pclk); #1;
    psel = 1; penable = 0; pwrite = wr; paddr = addr; pwdata = wdata; pstrb = strb;
    @(posedge pclk); #1;
    penable = 1;
    do begin @(posedge pclk); #1; waits++; end while (!pready);
    if (pslverr !== exp_err || (!wr && !exp_err && prdata !== exp_data)) begin
      $display("[CHECK] id=%s | proto=APB | cat=%s | tx=%s addr=0x%04h wdata=0x%08h strb=0x%1h waits=%0d | exp=0x%08h(err=%0b) | got=0x%08h(err=%0b) | status=FAIL",
               id, cat, wr ? "WR" : "RD", addr, wdata, strb, waits, exp_data, exp_err, prdata, pslverr);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=APB | cat=%s | tx=%s addr=0x%04h wdata=0x%08h strb=0x%1h waits=%0d | exp=0x%08h(err=%0b) | got=0x%08h(err=%0b) | status=PASS",
               id, cat, wr ? "WR" : "RD", addr, wdata, strb, waits, exp_data, exp_err, wr ? wdata : prdata, pslverr);
    end
    psel = 0; penable = 0;
  endtask

  initial begin
    repeat (3) @(posedge pclk);
    presetn = 1;
    apb_tx("APB_001", "reset_read",  1'b0, 16'h0000, 32'h0,        4'hF, 32'h12345678, 1'b0);
    apb_tx("APB_002", "pstrb_write", 1'b1, 16'h0000, 32'hAA00BB00, 4'b1010, 32'hAA00BB00, 1'b0);
    apb_tx("APB_003", "pstrb_read",  1'b0, 16'h0000, 32'h0,        4'hF, 32'hAA34BB78, 1'b0);
    apb_tx("APB_004", "ro_protect",  1'b1, 16'h0004, 32'hFFFFFFFF, 4'hF, 32'hFFFFFFFF, 1'b0);
    apb_tx("APB_005", "ro_verify",   1'b0, 16'h0004, 32'h0,        4'hF, 32'hDEAD0001, 1'b0);
    apb_tx("APB_006", "w1c_clear",   1'b1, 16'h0008, 32'h0000000F, 4'hF, 32'h0000000F, 1'b0);
    apb_tx("APB_007", "w1c_verify",  1'b0, 16'h0008, 32'h0,        4'hF, 32'h000000F0, 1'b0);
    apb_tx("APB_008", "pslverr_ill", 1'b0, 16'h0040, 32'h0,        4'hF, 32'h00000000, 1'b1);
    if (errs == 0) begin $display("PASS: apb all 8 checks passed"); $finish; end
    else begin $display("FAIL: apb %0d errors", errs); $fatal(1); end
  end
endmodule
