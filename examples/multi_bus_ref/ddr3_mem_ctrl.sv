`timescale 1ns/1ps
module ddr3_mem_ctrl (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        ddr3_ck_p,
  input  logic        ddr3_ck_n,
  input  logic        ddr3_cke,
  input  logic        ddr3_cs_n,
  input  logic        ddr3_ras_n,
  input  logic        ddr3_cas_n,
  input  logic        ddr3_we_n,
  input  logic [2:0]  ddr3_ba,
  input  logic [13:0] ddr3_addr,
  input  logic [15:0] ddr3_dq_in,
  output logic [15:0] ddr3_dq_out,
  output logic        ddr3_rd_valid
);
  logic        row_open [0:7];
  logic [3:0]  open_row [0:7];
  logic [15:0] mem [0:255]; // {ba[2:0], row[1:0], col[2:0]}
  logic [15:0] rd_pipe0, rd_pipe1;
  logic        rv_pipe0, rv_pipe1;
  logic        _u0, _u1;
  assign _u0 = ddr3_ck_p ^ ddr3_ck_n;
  assign _u1 = ^ddr3_addr[13:4];

  wire [2:0] cmd = {ddr3_ras_n, ddr3_cas_n, ddr3_we_n};
  wire [7:0] idx = {ddr3_ba, open_row[ddr3_ba][1:0], ddr3_addr[2:0]};

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < 8; i++) begin row_open[i] <= 1'b0; open_row[i] <= '0; end
      rd_pipe0 <= '0; rd_pipe1 <= '0; rv_pipe0 <= 1'b0; rv_pipe1 <= 1'b0;
      ddr3_dq_out <= '0; ddr3_rd_valid <= 1'b0;
    end else begin
      // CL=2 read pipeline
      rv_pipe0 <= 1'b0;
      rd_pipe0 <= '0;
      if (ddr3_cke && !ddr3_cs_n) begin
        case (cmd)
          3'b011: begin // ACT
            row_open[ddr3_ba] <= 1'b1;
            open_row[ddr3_ba] <= ddr3_addr[3:0];
          end
          3'b100: begin // WR
            if (row_open[ddr3_ba]) mem[idx] <= ddr3_dq_in;
          end
          3'b101: begin // RD
            if (row_open[ddr3_ba]) begin
              rv_pipe0 <= 1'b1;
              rd_pipe0 <= mem[idx];
            end
          end
          3'b010: begin // PRE
            row_open[ddr3_ba] <= 1'b0;
          end
          default: ;
        endcase
      end
      rv_pipe1      <= rv_pipe0;
      rd_pipe1      <= rd_pipe0;
      ddr3_rd_valid <= rv_pipe1;
      ddr3_dq_out   <= rd_pipe1;
    end
  end
endmodule
