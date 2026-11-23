`timescale 1ns/1ps
module hbm_pc_ctrl #(
  parameter int IDW = 6,
  parameter int AW  = 28,
  parameter int DW  = 256
) (
  input  logic           clk,
  input  logic           rst_n,
  input  logic [IDW-1:0] hbm_awid,
  input  logic [AW-1:0]  hbm_awaddr,
  input  logic           hbm_awvalid,
  output logic           hbm_awready,
  input  logic [DW-1:0]  hbm_wdata,
  input  logic           hbm_wvalid,
  output logic           hbm_wready,
  output logic [IDW-1:0] hbm_bid,
  output logic           hbm_bvalid,
  input  logic           hbm_bready,
  input  logic [IDW-1:0] hbm_arid,
  input  logic [AW-1:0]  hbm_araddr,
  input  logic           hbm_arvalid,
  output logic           hbm_arready,
  output logic [IDW-1:0] hbm_rid,
  output logic [DW-1:0]  hbm_rdata,
  output logic           hbm_rvalid,
  input  logic           hbm_rready
);
  logic [DW-1:0] mem [0:15];
  logic          _ua;
  assign _ua = (^hbm_awaddr[AW-1:9]) ^ (^hbm_araddr[AW-1:9]);
  assign hbm_awready = !hbm_bvalid && hbm_wvalid;
  assign hbm_wready  = !hbm_bvalid && hbm_awvalid;
  assign hbm_arready = !hbm_rvalid;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      hbm_bvalid <= 1'b0; hbm_bid <= '0;
      hbm_rvalid <= 1'b0; hbm_rid <= '0; hbm_rdata <= '0;
    end else begin
      if (hbm_bvalid && hbm_bready) hbm_bvalid <= 1'b0;
      if (hbm_awvalid && hbm_awready && hbm_wvalid && hbm_wready) begin
        mem[hbm_awaddr[8:5]] <= hbm_wdata;
        hbm_bid    <= hbm_awid;
        hbm_bvalid <= 1'b1;
      end
      if (hbm_rvalid && hbm_rready) hbm_rvalid <= 1'b0;
      if (hbm_arvalid && hbm_arready) begin
        hbm_rdata  <= mem[hbm_araddr[8:5]];
        hbm_rid    <= hbm_arid;
        hbm_rvalid <= 1'b1;
      end
    end
  end
endmodule
