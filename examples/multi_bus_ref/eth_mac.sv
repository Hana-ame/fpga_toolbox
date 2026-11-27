`timescale 1ns/1ps
// 最小真实以太网 MAC：把 rx 收到的帧原样转发到 tx（回环转发）
module eth_mac_real (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        gmii_rx_dv,
  input  logic [7:0]  gmii_rx_data,
  output logic        gmii_tx_en,
  output logic [7:0]  gmii_tx_data
);
  assign gmii_tx_en   = gmii_rx_dv;
  assign gmii_tx_data = gmii_rx_data;
endmodule
