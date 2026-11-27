`timescale 1ns/1ps
// 最小真实 SPI master：Mode0，每帧 8 位，连续发 3 字节
module spi_master_real (
  input  logic       clk,
  input  logic       rst_n,
  output logic       spi_sclk,
  output logic       spi_cs_n,
  output logic       spi_mosi,
  input  logic       spi_miso
);
  localparam int DIV = 4;
  logic [2:0] div;
  logic [3:0] bitn;
  logic [7:0] tx;
  logic [1:0] byte_i;
  logic       busy;
  logic [7:0] tx_bytes [0:2];

  initial begin
    tx_bytes[0] = 8'hA5; tx_bytes[1] = 8'h3C; tx_bytes[2] = 8'h5A;
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      div <= 0; bitn <= 0; spi_sclk <= 0; spi_cs_n <= 1; spi_mosi <= 0;
      byte_i <= 0; busy <= 0; tx <= 0;
    end else begin
      div <= div + 1;
      if (!busy) begin
        if (byte_i < 3) begin
          busy <= 1; tx <= tx_bytes[byte_i]; bitn <= 0; spi_cs_n <= 0; div <= 0;
        end
      end else if (div == 3'(DIV-1)) begin
        div <= 0;
        spi_sclk <= ~spi_sclk;
        if (spi_sclk == 0) begin
          spi_mosi <= tx[7];
          tx <= {tx[6:0], 1'b0};
        end else begin
          if (bitn == 7) begin
            busy <= 0; spi_cs_n <= 1; bitn <= 0; byte_i <= byte_i + 1; spi_sclk <= 0;
          end else bitn <= bitn + 1;
        end
      end
    end
  end
endmodule
