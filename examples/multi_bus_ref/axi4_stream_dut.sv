`timescale 1ns/1ps
module axs_fifo #(
  parameter int DW = 32
) (
  input  logic          aclk,
  input  logic          aresetn,
  input  logic [DW-1:0] s_axis_tdata,
  input  logic          s_axis_tvalid,
  output logic          s_axis_tready,
  input  logic          s_axis_tlast,
  input  logic [DW/8-1:0] s_axis_tkeep,
  input  logic [3:0]    s_axis_tid,
  input  logic [3:0]    s_axis_tdest,
  output logic [DW-1:0] m_axis_tdata,
  output logic          m_axis_tvalid,
  input  logic          m_axis_tready,
  output logic          m_axis_tlast,
  output logic [DW/8-1:0] m_axis_tkeep,
  output logic [3:0]    m_axis_tid,
  output logic [3:0]    m_axis_tdest
);
  logic [DW-1:0] mem [0:15];
  logic [DW/8-1:0] kee [0:15];
  logic [3:0] id_mem [0:15], de_mem [0:15];
  logic [3:0] wr, rd;
  logic [4:0] cnt;
  logic [DW-1:0] tdata_r;
  logic [DW/8-1:0] tkeep_r;
  logic [3:0] tid_r, tdest_r;
  logic tlast_r, have;

  assign s_axis_tready = (cnt < 16);
  assign m_axis_tvalid = have;
  assign m_axis_tdata  = tdata_r;
  assign m_axis_tlast  = tlast_r;
  assign m_axis_tkeep  = tkeep_r;
  assign m_axis_tid    = tid_r;
  assign m_axis_tdest  = tdest_r;

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      wr <= 0; rd <= 0; cnt <= 0; have <= 0;
      tdata_r <= 0; tkeep_r <= 0; tid_r <= 0; tdest_r <= 0; tlast_r <= 0;
    end else begin
      if (s_axis_tvalid && s_axis_tready) begin
        mem[wr[3:0]] <= s_axis_tdata;
        kee[wr[3:0]] <= s_axis_tkeep;
        id_mem[wr[3:0]] <= s_axis_tid;
        de_mem[wr[3:0]] <= s_axis_tdest;
        wr <= wr + 1;
      end
      if (m_axis_tvalid && m_axis_tready) begin
        rd <= rd + 1;
        have <= 1; tdata_r <= mem[(rd+1) % 16]; tkeep_r <= kee[(rd+1) % 16];
        tid_r <= id_mem[(rd+1) % 16]; tdest_r <= de_mem[(rd+1) % 16];
        tlast_r <= ((rd+1) % 4) == 3;
      end else if (s_axis_tvalid && s_axis_tready) begin
        have <= 1;
        tdata_r <= s_axis_tdata; tkeep_r <= s_axis_tkeep;
        tid_r <= s_axis_tid; tdest_r <= s_axis_tdest;
        tlast_r <= s_axis_tlast;
        rd <= rd;
      end
      if (s_axis_tvalid && s_axis_tready && !(m_axis_tvalid && m_axis_tready)) cnt <= cnt + 1;
      else if (!(s_axis_tvalid && s_axis_tready) && (m_axis_tvalid && m_axis_tready)) cnt <= cnt - 1;
    end
  end
endmodule
