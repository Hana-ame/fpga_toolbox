`timescale 1ns/1ps
module tb_hbm;
  logic         clk = 0, rst_n = 0;
  logic [5:0]   hbm_awid = 0, hbm_bid, hbm_arid = 0, hbm_rid;
  logic [27:0]  hbm_awaddr = 0, hbm_araddr = 0;
  logic [255:0] hbm_wdata = 0, hbm_rdata;
  logic         hbm_awvalid = 0, hbm_awready, hbm_wvalid = 0, hbm_wready, hbm_bvalid, hbm_bready = 1;
  logic         hbm_arvalid = 0, hbm_arready, hbm_rvalid, hbm_rready = 1;
  int           errs = 0;

  hbm_pc_ctrl dut (.*);
  always #5 clk = ~clk;

  initial begin
    repeat (3) @(posedge clk);
    rst_n = 1;
    // 256-bit write with ID=6'h2A
    @(posedge clk); #1;
    hbm_awid = 6'h2A; hbm_awaddr = 28'h000_0060; hbm_awvalid = 1;
    hbm_wdata = {4{64'h01234567_89ABCDEF}}; hbm_wvalid = 1;
    @(posedge clk); #1;
    hbm_awvalid = 0; hbm_wvalid = 0;
    if (!hbm_bvalid || hbm_bid !== 6'h2A) begin
      $display("[CHECK] id=HBM_001 | proto=HBM | cat=pc256_write_id | tx=WR256 id=0x2A addr=0x0000060 | exp=bvalid=1,bid=0x2A | got=bvalid=%0b,bid=0x%02h | status=FAIL", hbm_bvalid, hbm_bid);
      errs++;
    end else begin
      $display("[CHECK] id=HBM_001 | proto=HBM | cat=pc256_write_id | tx=WR256 id=0x2A addr=0x0000060 | exp=bvalid=1,bid=0x2A | got=bvalid=%0b,bid=0x%02h | status=PASS", hbm_bvalid, hbm_bid);
    end
    // 256-bit read with ID=6'h15 + backpressure (rready=0 for 1 cycle)
    @(posedge clk); #1;
    hbm_arid = 6'h15; hbm_araddr = 28'h000_0060; hbm_arvalid = 1; hbm_rready = 0;
    @(posedge clk); #1;
    hbm_arvalid = 0;
    @(posedge clk); #1;
    if (!hbm_rvalid || hbm_rid !== 6'h15 || hbm_rdata !== {4{64'h01234567_89ABCDEF}}) begin
      $display("[CHECK] id=HBM_002 | proto=HBM | cat=pc256_read_backpressure | tx=RD256 id=0x15 rready=0 hold | exp=rvalid=1,rid=0x15,lo64=0x0123456789ABCDEF | got=rvalid=%0b,rid=0x%02h,lo64=0x%016h | status=FAIL", hbm_rvalid, hbm_rid, hbm_rdata[63:0]);
      errs++;
    end else begin
      $display("[CHECK] id=HBM_002 | proto=HBM | cat=pc256_read_backpressure | tx=RD256 id=0x15 rready=0 hold | exp=rvalid=1,rid=0x15,lo64=0x0123456789ABCDEF | got=rvalid=%0b,rid=0x%02h,lo64=0x%016h | status=PASS", hbm_rvalid, hbm_rid, hbm_rdata[63:0]);
    end
    hbm_rready = 1;
    @(posedge clk);
    if (errs == 0) begin $display("PASS: hbm 256-bit pseudo-channel ID+data+backpressure passed"); $finish; end
    else begin $display("FAIL: hbm %0d errors", errs); $fatal(1); end
  end
endmodule
