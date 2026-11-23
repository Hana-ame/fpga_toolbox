`timescale 1ns/1ps
module axi4_to_ddr3_buf_ctrl #(
  parameter int BUF_CYCS = 2
) (
  input  logic        clk,
  input  logic        por_n,
  input  logic        sr_req,
  output logic        sr_ack,
  output logic        ddr3_ready,
  output logic        ddr3_zqcl_done,
  // Interface 1: AXI4 Slave
  input  logic [3:0]  s_axi_awid,
  input  logic [15:0] s_axi_awaddr,
  input  logic [7:0]  s_axi_awlen,
  input  logic [2:0]  s_axi_awsize,
  input  logic [1:0]  s_axi_awburst,
  input  logic        s_axi_awvalid,
  output logic        s_axi_awready,
  input  logic [31:0] s_axi_wdata,
  input  logic [3:0]  s_axi_wstrb,
  input  logic        s_axi_wlast,
  input  logic        s_axi_wvalid,
  output logic        s_axi_wready,
  output logic [3:0]  s_axi_bid,
  output logic [1:0]  s_axi_bresp,
  output logic        s_axi_bvalid,
  input  logic        s_axi_bready,
  input  logic [3:0]  s_axi_arid,
  input  logic [15:0] s_axi_araddr,
  input  logic [7:0]  s_axi_arlen,
  input  logic [2:0]  s_axi_arsize,
  input  logic [1:0]  s_axi_arburst,
  input  logic        s_axi_arvalid,
  output logic        s_axi_arready,
  output logic [3:0]  s_axi_rid,
  output logic [31:0] s_axi_rdata,
  output logic [1:0]  s_axi_rresp,
  output logic        s_axi_rlast,
  output logic        s_axi_rvalid,
  input  logic        s_axi_rready,
  // Interface 2: DDR3 PHY Command Bus
  output logic        ddr3_cke,
  output logic        ddr3_cs_n,
  output logic        ddr3_ras_n,
  output logic        ddr3_cas_n,
  output logic        ddr3_we_n,
  output logic [2:0]  ddr3_ba,
  output logic [13:0] ddr3_addr,
  output logic [15:0] ddr3_dq
);
  localparam logic [2:0] I_POR = 0, I_CKE = 1, I_MRS = 2, I_ZQCL = 3, I_READY = 4, I_SR = 5;
  logic [2:0]  ist, icnt;
  logic [31:0] dram [0:63];
  logic        wr_act, wr_buf, rd_buf;
  logic [3:0]  buf_cnt, wr_id, rd_id, wr_strb_r;
  logic [15:0] wr_addr_r, rd_addr_r;
  logic [31:0] wr_data_r;
  logic        _u;
  assign _u = (^s_axi_awlen) ^ (^s_axi_awsize) ^ (^s_axi_awburst) ^ (^s_axi_arlen) ^ (^s_axi_arsize) ^ (^s_axi_arburst) ^ s_axi_wlast;

  assign ddr3_ready     = (ist == I_READY);
  assign sr_ack         = (ist == I_SR);
  assign ddr3_cke       = (ist != I_POR && ist != I_SR);
  assign ddr3_zqcl_done = (ist == I_READY || ist == I_SR);
  assign s_axi_awready  = !wr_act && !wr_buf && !rd_buf && !s_axi_bvalid;
  assign s_axi_wready   = wr_act;
  assign s_axi_arready  = !wr_act && !wr_buf && !rd_buf && !s_axi_rvalid;

  always_ff @(posedge clk) begin
    if (!por_n) begin
      for (int i = 0; i < 64; i++) dram[i] <= 32'h0;
      ist <= I_POR; icnt <= 0; wr_act <= 0; wr_buf <= 0; rd_buf <= 0; buf_cnt <= 0;
      wr_id <= 0; rd_id <= 0; wr_addr_r <= 0; rd_addr_r <= 0; wr_data_r <= 0; wr_strb_r <= 0;
      s_axi_bvalid <= 0; s_axi_bresp <= 0; s_axi_bid <= 0;
      s_axi_rvalid <= 0; s_axi_rresp <= 0; s_axi_rid <= 0; s_axi_rdata <= 0; s_axi_rlast <= 0;
      ddr3_cs_n <= 1; ddr3_ras_n <= 1; ddr3_cas_n <= 1; ddr3_we_n <= 1; ddr3_ba <= 0; ddr3_addr <= 0; ddr3_dq <= 0;
    end else begin
      ddr3_cs_n <= 1; ddr3_ras_n <= 1; ddr3_cas_n <= 1; ddr3_we_n <= 1;
      case (ist)
        I_POR:   if (icnt == 2) begin ist <= I_CKE; icnt <= 0; end else icnt <= icnt + 1;
        I_CKE:   if (icnt == 2) begin ist <= I_MRS; icnt <= 0; end else icnt <= icnt + 1;
        I_MRS:   begin ddr3_cs_n <= 0; ddr3_ras_n <= 0; ddr3_cas_n <= 0; ddr3_we_n <= 0; ist <= I_ZQCL; end
        I_ZQCL:  begin ddr3_cs_n <= 0; ddr3_we_n <= 0; ddr3_addr <= 14'h0400; ist <= I_READY; end
        I_READY: if (sr_req && !wr_act && !wr_buf && !rd_buf && !s_axi_bvalid && !s_axi_rvalid) ist <= I_SR;
        I_SR:    if (!sr_req) ist <= I_READY;
        default: ist <= I_POR;
      endcase

      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
      if (s_axi_rvalid && s_axi_rready) begin s_axi_rvalid <= 0; s_axi_rlast <= 0; end

      if (s_axi_awvalid && s_axi_awready) begin wr_act <= 1; wr_id <= s_axi_awid; wr_addr_r <= s_axi_awaddr; end
      else if (wr_act && s_axi_wvalid && s_axi_wready) begin
        wr_act <= 0; wr_data_r <= s_axi_wdata; wr_strb_r <= s_axi_wstrb;
        if (BUF_CYCS == 0) begin
          s_axi_bvalid <= 1; s_axi_bid <= wr_id;
          if (!ddr3_ready || wr_addr_r >= 16'h0100) s_axi_bresp <= 2'b10;
          else begin
            for (int b = 0; b < 4; b++) if (s_axi_wstrb[b]) dram[wr_addr_r[7:2]][b*8 +: 8] <= s_axi_wdata[b*8 +: 8];
            ddr3_cs_n <= 0; ddr3_cas_n <= 0; ddr3_we_n <= 0; ddr3_ba <= wr_addr_r[7:5]; ddr3_addr <= {9'd0, wr_addr_r[4:0]}; ddr3_dq <= s_axi_wdata[15:0];
            s_axi_bresp <= 2'b00;
          end
        end else begin wr_buf <= 1; buf_cnt <= 4'(BUF_CYCS); end
      end else if (wr_buf) begin
        if (buf_cnt <= 1) begin
          wr_buf <= 0; s_axi_bvalid <= 1; s_axi_bid <= wr_id;
          if (!ddr3_ready || wr_addr_r >= 16'h0100) s_axi_bresp <= 2'b10;
          else begin
            for (int b = 0; b < 4; b++) if (wr_strb_r[b]) dram[wr_addr_r[7:2]][b*8 +: 8] <= wr_data_r[b*8 +: 8];
            ddr3_cs_n <= 0; ddr3_cas_n <= 0; ddr3_we_n <= 0; ddr3_ba <= wr_addr_r[7:5]; ddr3_addr <= {9'd0, wr_addr_r[4:0]}; ddr3_dq <= wr_data_r[15:0];
            s_axi_bresp <= 2'b00;
          end
        end else buf_cnt <= buf_cnt - 1;
      end

      if (s_axi_arvalid && s_axi_arready) begin
        rd_id <= s_axi_arid; rd_addr_r <= s_axi_araddr;
        if (BUF_CYCS == 0) begin
          s_axi_rvalid <= 1; s_axi_rid <= s_axi_arid; s_axi_rlast <= 1;
          if (!ddr3_ready || s_axi_araddr >= 16'h0100) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
          else begin s_axi_rdata <= dram[s_axi_araddr[7:2]]; s_axi_rresp <= 2'b00; end
        end else begin rd_buf <= 1; buf_cnt <= 4'(BUF_CYCS); end
      end else if (rd_buf) begin
        if (buf_cnt <= 1) begin
          rd_buf <= 0; s_axi_rvalid <= 1; s_axi_rid <= rd_id; s_axi_rlast <= 1;
          if (!ddr3_ready || rd_addr_r >= 16'h0100) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
          else begin s_axi_rdata <= dram[rd_addr_r[7:2]]; s_axi_rresp <= 2'b00; end
        end else buf_cnt <= buf_cnt - 1;
      end
    end
  end
endmodule
