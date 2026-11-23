`timescale 1ns/1ps
module pcie_tlp_ep #(
  parameter int GEN   = 4,   // 3, 4, or 5
  parameter int LANES = 4,   // 1, 4, or 16
  parameter int DW    = (LANES == 16) ? 512 : ((LANES == 4) ? 128 : 64)
) (
  input  logic          clk,
  input  logic          rst_n,
  input  logic          pcie_rx_tvalid,
  output logic          pcie_rx_tready,
  input  logic [DW-1:0] pcie_rx_tdata,
  input  logic          pcie_rx_tlast,
  output logic          pcie_tx_tvalid,
  input  logic          pcie_tx_tready,
  output logic [DW-1:0] pcie_tx_tdata,
  output logic          pcie_tx_tlast,
  output logic          pcie_link_up,
  output logic [3:0]    pcie_cfg_speed,
  output logic [5:0]    pcie_cfg_lanes
);
  // TLP layout in low 64 bits: [63:56]=cmd (8'h40=MWr, 8'h00=MRd, 8'h4A=CplD), [55:48]=tag, [47:32]=addr, [31:0]=data
  logic [31:0] bar_mem [0:15];
  logic        _ulast;
  assign _ulast         = pcie_rx_tlast;
  assign pcie_link_up   = rst_n;
  assign pcie_cfg_speed = 4'(GEN);
  assign pcie_cfg_lanes = 6'(LANES);
  assign pcie_rx_tready = !pcie_tx_tvalid;

  wire [7:0]  rx_cmd  = pcie_rx_tdata[63:56];
  wire [7:0]  rx_tag  = pcie_rx_tdata[55:48];
  wire [15:0] rx_addr = pcie_rx_tdata[47:32];
  wire [31:0] rx_data = pcie_rx_tdata[31:0];

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      pcie_tx_tvalid <= 1'b0;
      pcie_tx_tdata  <= '0;
      pcie_tx_tlast  <= 1'b0;
    end else begin
      if (pcie_tx_tvalid && pcie_tx_tready) begin
        pcie_tx_tvalid <= 1'b0;
        pcie_tx_tlast  <= 1'b0;
      end
      if (pcie_rx_tvalid && pcie_rx_tready) begin
        if (rx_cmd == 8'h40) begin
          bar_mem[rx_addr[5:2]] <= rx_data;
        end else if (rx_cmd == 8'h00) begin
          pcie_tx_tvalid      <= 1'b1;
          pcie_tx_tlast       <= 1'b1;
          pcie_tx_tdata       <= '0;
          pcie_tx_tdata[63:0] <= {8'h4A, rx_tag, rx_addr, bar_mem[rx_addr[5:2]]};
        end
      end
    end
  end
endmodule
