`timescale 1ns/1ps
module axi4_full_slave #(
  parameter int IDW = 4,
  parameter int AW  = 16,
  parameter int DW  = 32
) (
  input  logic           aclk,
  input  logic           aresetn,
  input  logic [IDW-1:0] s_axi_awid,
  input  logic [AW-1:0]  s_axi_awaddr,
  input  logic [7:0]     s_axi_awlen,
  input  logic [2:0]     s_axi_awsize,
  input  logic [1:0]     s_axi_awburst,
  input  logic           s_axi_awvalid,
  output logic           s_axi_awready,
  input  logic [DW-1:0]  s_axi_wdata,
  input  logic [3:0]     s_axi_wstrb,
  input  logic           s_axi_wlast,
  input  logic           s_axi_wvalid,
  output logic           s_axi_wready,
  output logic [IDW-1:0] s_axi_bid,
  output logic [1:0]     s_axi_bresp,
  output logic           s_axi_bvalid,
  input  logic           s_axi_bready,
  input  logic [IDW-1:0] s_axi_arid,
  input  logic [AW-1:0]  s_axi_araddr,
  input  logic [7:0]     s_axi_arlen,
  input  logic [2:0]     s_axi_arsize,
  input  logic [1:0]     s_axi_arburst,
  input  logic           s_axi_arvalid,
  output logic           s_axi_arready,
  output logic [IDW-1:0] s_axi_rid,
  output logic [DW-1:0]  s_axi_rdata,
  output logic [1:0]     s_axi_rresp,
  output logic           s_axi_rlast,
  output logic           s_axi_rvalid,
  input  logic           s_axi_rready
);
  logic [DW-1:0] mem [0:63];
  logic          wr_active, rd_active;
  logic [IDW-1:0] wr_id, rd_id;
  logic [AW-1:0]  wr_addr, rd_addr;
  logic [7:0]     wr_rem, rd_rem;
  logic [1:0]     wr_burst, rd_burst;
  logic           wr_err, rd_err;
  logic           _u;
  assign _u = (^s_axi_awsize) ^ (^s_axi_arsize);

  assign s_axi_awready = !wr_active && !s_axi_bvalid;
  assign s_axi_wready  = wr_active;
  assign s_axi_arready = !rd_active && !s_axi_rvalid;

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      for (int i = 0; i < 64; i++) mem[i] <= 32'h0;
      wr_active <= 1'b0; rd_active <= 1'b0;
      s_axi_bvalid <= 1'b0; s_axi_bresp <= 2'b00; s_axi_bid <= '0;
      s_axi_rvalid <= 1'b0; s_axi_rresp <= 2'b00; s_axi_rid <= '0;
      s_axi_rdata  <= '0;   s_axi_rlast <= 1'b0;
    end else begin
      // Write path
      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
      if (s_axi_awvalid && s_axi_awready) begin
        wr_active <= 1'b1;
        wr_id     <= s_axi_awid;
        wr_addr   <= s_axi_awaddr;
        wr_rem    <= s_axi_awlen;
        wr_burst  <= s_axi_awburst;
        wr_err    <= (s_axi_awaddr >= 16'h0100);
      end else if (wr_active && s_axi_wvalid && s_axi_wready) begin
        if (!wr_err) begin
          for (int b = 0; b < 4; b++)
            if (s_axi_wstrb[b]) mem[wr_addr[7:2]][b*8 +: 8] <= s_axi_wdata[b*8 +: 8];
        end
        if (wr_burst == 2'b01) wr_addr <= wr_addr + 16'd4; // INCR
        if (s_axi_wlast || wr_rem == 8'd0) begin
          wr_active    <= 1'b0;
          s_axi_bvalid <= 1'b1;
          s_axi_bid    <= wr_id;
          s_axi_bresp  <= wr_err ? 2'b10 : 2'b00; // SLVERR or OKAY
        end else begin
          wr_rem <= wr_rem - 8'd1;
        end
      end

      // Read path
      if (s_axi_arvalid && s_axi_arready) begin
        rd_active    <= 1'b1;
        rd_id        <= s_axi_arid;
        rd_addr      <= (s_axi_arburst == 2'b01) ? (s_axi_araddr + 16'd4) : s_axi_araddr;
        rd_rem       <= s_axi_arlen;
        rd_burst     <= s_axi_arburst;
        rd_err       <= (s_axi_araddr >= 16'h0100);
        s_axi_rvalid <= 1'b1;
        s_axi_rid    <= s_axi_arid;
        s_axi_rdata  <= (s_axi_araddr >= 16'h0100) ? 32'h0 : mem[s_axi_araddr[7:2]];
        s_axi_rresp  <= (s_axi_araddr >= 16'h0100) ? 2'b10 : 2'b00;
        s_axi_rlast  <= (s_axi_arlen == 8'd0);
      end else if (s_axi_rvalid && s_axi_rready) begin
        if (s_axi_rlast) begin
          s_axi_rvalid <= 1'b0;
          s_axi_rlast  <= 1'b0;
          rd_active    <= 1'b0;
        end else begin
          rd_rem      <= rd_rem - 8'd1;
          s_axi_rdata <= rd_err ? 32'h0 : mem[rd_addr[7:2]];
          s_axi_rlast <= (rd_rem == 8'd1);
          if (rd_burst == 2'b01) rd_addr <= rd_addr + 16'd4;
        end
      end
    end
  end
endmodule
