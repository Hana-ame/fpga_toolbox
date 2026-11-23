`timescale 1ns/1ps
module ddr4_mem_ctrl (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        ddr4_ck_t,
  input  logic        ddr4_ck_c,
  input  logic        ddr4_act_n,
  input  logic [1:0]  ddr4_bg,
  input  logic [1:0]  ddr4_ba,
  input  logic [16:0] ddr4_adr,
  input  logic        ddr4_cs_n,
  input  logic        ddr4_odt,
  input  logic [63:0] ddr4_dq_in,
  output logic [63:0] ddr4_dq_out,
  input  logic [7:0]  ddr4_dm_dbi_n,
  output logic        ddr4_rd_valid
);
  logic        row_open [0:15];
  logic [63:0] mem [0:63]; // {bg[1:0], ba[1:0], col[1:0]}
  logic        _u;
  assign _u = ddr4_ck_t ^ ddr4_ck_c ^ ddr4_odt ^ (^ddr4_adr[13:2]);

  wire [3:0] bidx = {ddr4_bg, ddr4_ba};
  wire [5:0] midx = {ddr4_bg, ddr4_ba, ddr4_adr[1:0]};
  // DDR4: ACT_n=0 -> ACT; ACT_n=1 & ADR[16:14]={RAS,CAS,WE}: 100=WR, 101=RD, 010=PRE
  wire [2:0] cmd = ddr4_adr[16:14];

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < 16; i++) row_open[i] <= 1'b0;
      ddr4_dq_out   <= '0;
      ddr4_rd_valid <= 1'b0;
    end else begin
      ddr4_rd_valid <= 1'b0;
      if (!ddr4_cs_n) begin
        if (!ddr4_act_n) begin
          row_open[bidx] <= 1'b1;
        end else if (cmd == 3'b100 && row_open[bidx]) begin
          // DM_DBI_n active-low mask: bit=1 means write byte, bit=0 means mask byte
          for (int b = 0; b < 8; b++) begin
            if (ddr4_dm_dbi_n[b]) mem[midx][b*8 +: 8] <= ddr4_dq_in[b*8 +: 8];
          end
        end else if (cmd == 3'b101 && row_open[bidx]) begin
          ddr4_dq_out   <= mem[midx];
          ddr4_rd_valid <= 1'b1;
        end else if (cmd == 3'b010) begin
          row_open[bidx] <= 1'b0;
        end
      end
    end
  end
endmodule
