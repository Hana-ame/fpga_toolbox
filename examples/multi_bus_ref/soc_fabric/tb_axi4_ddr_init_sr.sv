`timescale 1ns/1ps
module tb_axi4_ddr_init_sr;
  logic        clk = 0, por_n = 0, sr_req = 0, sr_ack, ddr_ready, ddr_cke, ddr_zqcl_done;
  logic [2:0]  init_state;
  logic [3:0]  s_axi_awid = 0, s_axi_bid, s_axi_arid = 0, s_axi_rid;
  logic [15:0] s_axi_awaddr = 0, s_axi_araddr = 0;
  logic [7:0]  s_axi_awlen = 0, s_axi_arlen = 0;
  logic [2:0]  s_axi_awsize = 3'd2, s_axi_arsize = 3'd2;
  logic [1:0]  s_axi_awburst = 2'b01, s_axi_arburst = 2'b01, s_axi_bresp, s_axi_rresp;
  logic        s_axi_awvalid = 0, s_axi_awready, s_axi_wvalid = 0, s_axi_wready, s_axi_wlast = 0;
  logic        s_axi_bvalid, s_axi_bready = 1, s_axi_arvalid = 0, s_axi_arready, s_axi_rvalid, s_axi_rready = 1, s_axi_rlast;
  logic [31:0] s_axi_wdata = 0, s_axi_rdata;
  logic [3:0]  s_axi_wstrb = 4'hF;
  logic        ddr4_act_n, ddr4_cs_n;
  logic [1:0]  ddr4_bg, ddr4_ba;
  logic [16:0] ddr4_adr;
  logic [63:0] ddr4_dq;
  int          errs = 0;

  axi4_ddr_init_sr_ctrl #(.IS_DDR4(1'b1)) dut (.*);
  always #5 clk = ~clk;

  task automatic axi_wr(input logic [15:0] addr, input logic [31:0] data, output logic [1:0] resp);
    @(posedge clk); #1;
    s_axi_awaddr = addr; s_axi_awvalid = 1;
    while (!s_axi_awready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_awvalid = 0;
    s_axi_wdata = data; s_axi_wstrb = 4'hF; s_axi_wlast = 1; s_axi_wvalid = 1;
    while (!s_axi_wready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_wvalid = 0; s_axi_wlast = 0;
    while (!(s_axi_bvalid && s_axi_bready)) begin @(posedge clk); #1; end
    resp = s_axi_bresp;
    @(posedge clk); #1;
  endtask

  task automatic axi_rd(input logic [15:0] addr, output logic [31:0] data, output logic [1:0] resp);
    @(posedge clk); #1;
    s_axi_araddr = addr; s_axi_arvalid = 1;
    while (!s_axi_arready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_arvalid = 0;
    while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge clk); #1; end
    data = s_axi_rdata; resp = s_axi_rresp;
    @(posedge clk); #1;
  endtask

  initial begin
    logic [31:0] rd;
    logic [1:0]  resp;
    repeat (2) @(posedge clk); #1;
    por_n = 1;

    // 1. Access during POR/CKE/MRS/ZQCL init -> MUST return SLVERR!
    axi_wr(16'h0010, 32'hBAD0_1111, resp);
    if (ddr_ready || resp !== 2'b10) begin
      $display("[CHECK] id=DDR_POR_001 | proto=AXI4-DDR4 | cat=por_init_guard | tx=AXI WR during POR/MRS/ZQCL | exp=ready=0,SLVERR(2) | got=ready=%0b,bresp=%0d | status=FAIL", ddr_ready, resp); errs++;
    end else begin
      $display("[CHECK] id=DDR_POR_001 | proto=AXI4-DDR4 | cat=por_init_guard | tx=AXI WR during POR/MRS/ZQCL | exp=ready=0,SLVERR(2) | got=ready=%0b,bresp=%0d | status=PASS", ddr_ready, resp);
    end

    // 2. Wait for POR->CKE->MRS->ZQCL->READY
    while (!ddr_ready) @(posedge clk);
    @(posedge clk); #1;
    if (!ddr_cke || !ddr_zqcl_done) begin
      $display("[CHECK] id=DDR_INIT_002 | proto=AXI4-DDR4 | cat=por_cke_mrs_zqcl | tx=Wait READY(4) | exp=cke=1,zqcl=1 | got=cke=%0b,zqcl=%0b | status=FAIL", ddr_cke, ddr_zqcl_done); errs++;
    end else begin
      $display("[CHECK] id=DDR_INIT_002 | proto=AXI4-DDR4 | cat=por_cke_mrs_zqcl | tx=Wait READY(4) | exp=cke=1,zqcl=1 | got=cke=%0b,zqcl=%0b | status=PASS", ddr_cke, ddr_zqcl_done);
    end

    // 3. Normal AXI4 -> DDR4 write & readback into built-in DRAM array
    axi_wr(16'h0040, 32'hA1B2_C3D4, resp);
    axi_rd(16'h0040, rd, resp);
    if (resp !== 2'b00 || rd !== 32'hA1B2_C3D4) begin
      $display("[CHECK] id=DDR_RW_003 | proto=AXI4-DDR4 | cat=axi_to_ddr4_mem | tx=AXI WR/RD 0x0040 | exp=0xA1B2C3D4 | got=0x%08h | status=FAIL", rd); errs++;
    end else begin
      $display("[CHECK] id=DDR_RW_003 | proto=AXI4-DDR4 | cat=axi_to_ddr4_mem | tx=AXI WR/RD 0x0040 | exp=0xA1B2C3D4 | got=0x%08h | status=PASS", rd);
    end

    // 4. Self-Refresh Sleep Handshake (sr_req=1 -> sr_ack=1, cke=0, AXI RD SLVERR) -> Wake Retention
    sr_req = 1;
    repeat (3) @(posedge clk); #1;
    axi_rd(16'h0040, rd, resp);
    if (!sr_ack || ddr_cke || resp !== 2'b10) begin
      $display("[CHECK] id=DDR_SR_004 | proto=AXI4-DDR4 | cat=self_refresh_sleep_wake | tx=sr_req=1 -> AXI RD | exp=ack=1,cke=0,SLVERR(2) | got=ack=%0b,cke=%0b,rresp=%0d | status=FAIL", sr_ack, ddr_cke, resp); errs++;
    end else begin
      sr_req = 0; repeat (3) @(posedge clk); #1;
      axi_rd(16'h0040, rd, resp);
      if (sr_ack || !ddr_cke || resp !== 2'b00 || rd !== 32'hA1B2_C3D4) begin
        $display("[CHECK] id=DDR_SR_004 | proto=AXI4-DDR4 | cat=self_refresh_sleep_wake | tx=SR exit retention | exp=0xA1B2C3D4 | got=0x%08h | status=FAIL", rd); errs++;
      end else begin
        $display("[CHECK] id=DDR_SR_004 | proto=AXI4-DDR4 | cat=self_refresh_sleep_wake | tx=SR(ack=1,cke=0,rej)->exit->RD | exp=0xA1B2C3D4 | got=0x%08h | status=PASS", rd);
      end
    end

    if (errs == 0) begin $display("PASS: axi4_ddr_init_sr all 4 POR/init/RW/Self-Refresh checks passed"); $finish; end
    else begin $display("FAIL: axi4_ddr_init_sr %0d errors", errs); $fatal(1); end
  end
endmodule
