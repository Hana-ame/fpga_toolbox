`timescale 1ns/1ps
module axi4_ddr_init_sr_ctrl #(
  parameter bit IS_DDR4 = 1'b1
) (
  input  logic        clk,
  input  logic        por_n,
  input  logic        sr_req,           // Self-Refresh low-power sleep request
  output logic        sr_ack,           // Self-Refresh sleep acknowledge
  output logic [2:0]  init_state,       // 0=POR,1=CKE_WAIT,2=MRS,3=ZQCL,4=READY,5=SELF_REF
  output logic        ddr_ready,
  output logic        ddr_cke,
  output logic        ddr_zqcl_done,
  // Interface 1: Upstream AXI4 Slave
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
  // Interface 2: Downstream DDR PHY Command Observability (DDR3/DDR4)
  output logic        ddr4_act_n,
  output logic        ddr4_cs_n,
  output logic [1:0]  ddr4_bg,
  output logic [1:0]  ddr4_ba,
  output logic [16:0] ddr4_adr,
  output logic [63:0] ddr4_dq
);
  localparam logic [2:0] I_POR      = 3'd0;
  localparam logic [2:0] I_CKE_WAIT = 3'd1;
  localparam logic [2:0] I_MRS      = 3'd2;
  localparam logic [2:0] I_ZQCL     = 3'd3;
  localparam logic [2:0] I_READY    = 3'd4;
  localparam logic [2:0] I_SELF_REF = 3'd5;

  logic [31:0] dram_mem [0:63]; // Built-in 256B DRAM array
  logic [2:0]  icnt;
  logic        wr_act;
  logic [3:0]  wr_id;
  logic [15:0] wr_addr;
  logic        _u;
  assign _u = IS_DDR4 ^ (^s_axi_awlen) ^ (^s_axi_awsize) ^ (^s_axi_awburst) ^ (^s_axi_arlen) ^ (^s_axi_arsize) ^ (^s_axi_arburst) ^ s_axi_wlast;

  assign ddr_ready     = (init_state == I_READY);
  assign sr_ack        = (init_state == I_SELF_REF);
  assign ddr_cke       = (init_state != I_POR && init_state != I_SELF_REF);
  assign ddr_zqcl_done = (init_state == I_READY || init_state == I_SELF_REF);

  assign s_axi_awready = !wr_act && !s_axi_bvalid;
  assign s_axi_wready  = wr_act;
  assign s_axi_arready = !s_axi_rvalid;

  always_ff @(posedge clk) begin
    if (!por_n) begin
      for (int i = 0; i < 64; i++) dram_mem[i] <= 32'h0;
      init_state <= I_POR; icnt <= 3'd0;
      wr_act <= 1'b0; wr_id <= '0; wr_addr <= '0;
      s_axi_bvalid <= 1'b0; s_axi_bresp <= 2'b00; s_axi_bid <= '0;
      s_axi_rvalid <= 1'b0; s_axi_rresp <= 2'b00; s_axi_rid <= '0;
      s_axi_rdata  <= 32'h0; s_axi_rlast <= 1'b0;
      ddr4_act_n <= 1'b1; ddr4_cs_n <= 1'b1; ddr4_bg <= '0; ddr4_ba <= '0; ddr4_adr <= '0; ddr4_dq <= '0;
    end else begin
      ddr4_cs_n <= 1'b1; ddr4_act_n <= 1'b1;

      // ---- Power-On Initialization + Self-Refresh FSM ----
      case (init_state)
        I_POR:      if (icnt == 3'd2) begin init_state <= I_CKE_WAIT; icnt <= 3'd0; end else icnt <= icnt + 3'd1;
        I_CKE_WAIT: if (icnt == 3'd2) begin init_state <= I_MRS;      icnt <= 3'd0; end else icnt <= icnt + 3'd1;
        I_MRS:      begin ddr4_cs_n <= 1'b0; ddr4_adr <= 17'h0010; init_state <= I_ZQCL; end
        I_ZQCL:     begin ddr4_cs_n <= 1'b0; ddr4_adr <= 17'h0400; init_state <= I_READY; end
        I_READY:    if (sr_req && !wr_act && !s_axi_bvalid && !s_axi_rvalid) init_state <= I_SELF_REF;
        I_SELF_REF: if (!sr_req) init_state <= I_READY;
        default:    init_state <= I_POR;
      endcase

      // ---- AXI4 Write (returns SLVERR if DRAM not READY — e.g., during POR init or Self-Refresh!) ----
      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
      if (s_axi_awvalid && s_axi_awready) begin
        wr_act <= 1'b1; wr_id <= s_axi_awid; wr_addr <= s_axi_awaddr;
      end else if (wr_act && s_axi_wvalid && s_axi_wready) begin
        wr_act <= 1'b0; s_axi_bvalid <= 1'b1; s_axi_bid <= wr_id;
        if (!ddr_ready || wr_addr >= 16'h0100) begin
          s_axi_bresp <= 2'b10; // SLVERR: init not ready or in Self-Refresh!
        end else begin
          for (int b = 0; b < 4; b++)
            if (s_axi_wstrb[b]) dram_mem[wr_addr[7:2]][b*8 +: 8] <= s_axi_wdata[b*8 +: 8];
          ddr4_cs_n <= 1'b0; ddr4_bg <= wr_addr[7:6]; ddr4_ba <= wr_addr[5:4];
          ddr4_adr  <= {3'b100, 10'd0, wr_addr[3:0]}; ddr4_dq <= {32'h0, s_axi_wdata};
          s_axi_bresp <= 2'b00;
        end
      end

      // ---- AXI4 Read ----
      if (s_axi_rvalid && s_axi_rready) begin s_axi_rvalid <= 1'b0; s_axi_rlast <= 1'b0; end
      if (s_axi_arvalid && s_axi_arready) begin
        s_axi_rvalid <= 1'b1; s_axi_rid <= s_axi_arid; s_axi_rlast <= 1'b1;
        if (!ddr_ready || s_axi_araddr >= 16'h0100) begin
          s_axi_rdata <= 32'h0; s_axi_rresp <= 2'b10;
        end else begin
          s_axi_rdata <= dram_mem[s_axi_araddr[7:2]];
          s_axi_rresp <= 2'b00;
        end
      end
    end
  end
endmodule
