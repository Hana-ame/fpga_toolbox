`timescale 1ns/1ps
// 最小但真实的 DDR5 控制器行为模型：初始化为 WR/RD 交替的简单序列
module ddr5_ctrl_real (
  input  logic         clk,
  input  logic         rst_n,
  output logic         ddr5_ck_t,
  output logic         ddr5_ck_c,
  output logic         ddr5_cs_n,
  output logic         ddr5_act_n,
  output logic [6:0]   ddr5_ca,
  output logic [1:0]   ddr5_ba,
  output logic [1:0]   ddr5_bg,
  inout  wire  [15:0]  ddr5_dq,
  output logic         ddr5_dqs_t,
  output logic         ddr5_dqs_c,
  output logic         ddr5_rd_valid
);
  logic [7:0] cyc;
  logic [15:0] rd_data;
  assign ddr5_ck_t = clk;
  assign ddr5_ck_c = ~clk;
  assign ddr5_dqs_t = 1'b1;
  assign ddr5_dqs_c = 1'b0;
  assign ddr5_dq = ddr5_rd_valid ? rd_data : 16'hzzzz;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      cyc <= 0; ddr5_cs_n <= 1'b1; ddr5_act_n <= 1'b1;
      ddr5_ca <= 7'h00; ddr5_ba <= 0; ddr5_bg <= 0;
      ddr5_rd_valid <= 1'b0; rd_data <= 16'h1234;
    end else begin
      cyc <= cyc + 1;
      case (cyc)
        8'd10: begin ddr5_cs_n <= 1'b0; ddr5_act_n <= 1'b0; ddr5_ca <= 7'h11; end // ACT
        8'd11: begin ddr5_cs_n <= 1'b1; ddr5_act_n <= 1'b1; end
        8'd20: begin ddr5_cs_n <= 1'b0; ddr5_act_n <= 1'b1; ddr5_ca <= 7'h20; end // WR
        8'd21: begin ddr5_cs_n <= 1'b1; end
        8'd30: begin ddr5_cs_n <= 1'b0; ddr5_act_n <= 1'b1; ddr5_ca <= 7'h21; end // RD
        8'd31: begin ddr5_cs_n <= 1'b1; end
        8'd40: begin ddr5_rd_valid <= 1'b1; end
        8'd41: begin ddr5_rd_valid <= 1'b0; end
        default: ;
      endcase
    end
  end
endmodule
