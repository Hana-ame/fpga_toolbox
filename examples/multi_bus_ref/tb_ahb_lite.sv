`timescale 1ns/1ps
module tb_ahb_lite;
  logic        HCLK = 0, HRESETn = 0, HSEL = 0, HWRITE = 0, HREADYOUT, HRESP;
  logic [15:0] HADDR = 0;
  logic [1:0]  HTRANS = 0;
  logic [2:0]  HSIZE = 3'd2;
  logic [31:0] HWDATA = 0, HRDATA;
  int          errs = 0;

  ahb_lite_slave dut (.*);
  always #5 HCLK = ~HCLK;

  task automatic ahb_write(input string id, input string cat, input logic [15:0] addr, input logic [31:0] data, input logic exp_err);
    @(posedge HCLK); #1;
    HSEL = 1; HTRANS = 2'b10; HWRITE = 1; HADDR = addr;
    @(posedge HCLK); #1;
    HSEL = 0; HTRANS = 2'b00; HWDATA = data;
    do begin @(posedge HCLK); #1; end while (!HREADYOUT);
    if (HRESP !== exp_err) begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=WR addr=0x%04h data=0x%08h | exp=HRESP:%0b | got=HRESP:%0b | status=FAIL", id, cat, addr, data, exp_err, HRESP);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=WR addr=0x%04h data=0x%08h | exp=HRESP:%0b | got=HRESP:%0b | status=PASS", id, cat, addr, data, exp_err, HRESP);
    end
  endtask

  task automatic ahb_read(input string id, input string cat, input logic [15:0] addr, input logic [31:0] exp_data, input logic exp_err);
    @(posedge HCLK); #1;
    HSEL = 1; HTRANS = 2'b10; HWRITE = 0; HADDR = addr;
    @(posedge HCLK); #1;
    HSEL = 0; HTRANS = 2'b00;
    do begin @(posedge HCLK); #1; end while (!HREADYOUT);
    if (HRESP !== exp_err || (!exp_err && HRDATA !== exp_data)) begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=RD addr=0x%04h | exp=0x%08h(HRESP=%0b) | got=0x%08h(HRESP=%0b) | status=FAIL", id, cat, addr, exp_data, exp_err, HRDATA, HRESP);
      errs++;
    end else begin
      $display("[CHECK] id=%s | proto=AHB-Lite | cat=%s | tx=RD addr=0x%04h | exp=0x%08h(HRESP=%0b) | got=0x%08h(HRESP=%0b) | status=PASS", id, cat, addr, exp_data, exp_err, HRDATA, HRESP);
    end
  endtask

  initial begin
    repeat (3) @(posedge HCLK);
    HRESETn = 1;
    ahb_read ("AHB_001", "reset_read",  16'h0000, 32'h11223344, 1'b0);
    ahb_write("AHB_002", "rw_write",    16'h0000, 32'hA5A55A5A, 1'b0);
    ahb_read ("AHB_003", "rw_readback", 16'h0000, 32'hA5A55A5A, 1'b0);
    ahb_write("AHB_004", "ro_write",    16'h0004, 32'hFFFFFFFF, 1'b0);
    ahb_read ("AHB_005", "ro_protect",  16'h0004, 32'hCAFEBABE, 1'b0);
    ahb_read ("AHB_006", "hresp_error", 16'h0040, 32'h00000000, 1'b1);
    if (errs == 0) begin $display("PASS: ahb_lite all 6 checks passed"); $finish; end
    else begin $display("FAIL: ahb_lite %0d errors", errs); $fatal(1); end
  end
endmodule
