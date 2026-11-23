`timescale 1ns/1ps
module apb_to_axi4_buf_bridge #(
  parameter int BUF_CYCS = 2,
  parameter int IDW      = 4,
  parameter int AW       = 16,
  parameter int DW       = 32
) (
  input  logic           clk,
  input  logic           rst_n,
  // Interface 1: Upstream APB4 Slave
  input  logic           s_apb_psel,
  input  logic           s_apb_penable,
  input  logic           s_apb_pwrite,
  input  logic [AW-1:0]  s_apb_paddr,
  input  logic [DW-1:0]  s_apb_pwdata,
  input  logic [3:0]     s_apb_pstrb,
  output logic           s_apb_pready,
  output logic [DW-1:0]  s_apb_prdata,
  output logic           s_apb_pslverr,
  // Interface 2: Downstream AXI4 Master
  output logic [IDW-1:0] m_axi_awid,
  output logic [AW-1:0]  m_axi_awaddr,
  output logic [7:0]     m_axi_awlen,
  output logic [2:0]     m_axi_awsize,
  output logic [1:0]     m_axi_awburst,
  output logic           m_axi_awvalid,
  input  logic           m_axi_awready,
  output logic [DW-1:0]  m_axi_wdata,
  output logic [3:0]     m_axi_wstrb,
  output logic           m_axi_wlast,
  output logic           m_axi_wvalid,
  input  logic           m_axi_wready,
  input  logic [IDW-1:0] m_axi_bid,
  input  logic [1:0]     m_axi_bresp,
  input  logic           m_axi_bvalid,
  output logic           m_axi_bready,
  output logic [IDW-1:0] m_axi_arid,
  output logic [AW-1:0]  m_axi_araddr,
  output logic [7:0]     m_axi_arlen,
  output logic [2:0]     m_axi_arsize,
  output logic [1:0]     m_axi_arburst,
  output logic           m_axi_arvalid,
  input  logic           m_axi_arready,
  input  logic [IDW-1:0] m_axi_rid,
  input  logic [DW-1:0]  m_axi_rdata,
  input  logic [1:0]     m_axi_rresp,
  input  logic           m_axi_rlast,
  input  logic           m_axi_rvalid,
  output logic           m_axi_rready
);
  typedef enum logic [2:0] { S_IDLE, S_BUF, S_AXI_AW, S_AXI_W, S_AXI_B, S_AXI_AR, S_AXI_R, S_DONE } st_t;
  st_t           st;
  logic [3:0]    buf_cnt;
  logic          lat_wr;
  logic [AW-1:0] lat_addr;
  logic [DW-1:0] lat_wdata;
  logic [3:0]    lat_strb;
  logic [IDW-1:0] tx_id;
  logic          _u;
  assign _u = (^m_axi_bid) ^ (^m_axi_rid) ^ m_axi_rlast;

  assign m_axi_awid    = tx_id;
  assign m_axi_awaddr  = lat_addr;
  assign m_axi_awlen   = 8'd0;
  assign m_axi_awsize  = 3'd2;
  assign m_axi_awburst = 2'b01;
  assign m_axi_awvalid = (st == S_AXI_AW);

  assign m_axi_wdata   = lat_wdata;
  assign m_axi_wstrb   = lat_strb;
  assign m_axi_wlast   = 1'b1;
  assign m_axi_wvalid  = (st == S_AXI_W);
  assign m_axi_bready  = (st == S_AXI_B);

  assign m_axi_arid    = tx_id;
  assign m_axi_araddr  = lat_addr;
  assign m_axi_arlen   = 8'd0;
  assign m_axi_arsize  = 3'd2;
  assign m_axi_arburst = 2'b01;
  assign m_axi_arvalid = (st == S_AXI_AR);
  assign m_axi_rready  = (st == S_AXI_R);

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      st <= S_IDLE; buf_cnt <= '0; tx_id <= '0;
      lat_wr <= 1'b0; lat_addr <= '0; lat_wdata <= '0; lat_strb <= '0;
      s_apb_pready <= 1'b0; s_apb_prdata <= '0; s_apb_pslverr <= 1'b0;
    end else begin
      s_apb_pready <= 1'b0;
      case (st)
        S_IDLE: begin
          if (s_apb_psel && s_apb_penable) begin
            lat_wr    <= s_apb_pwrite;
            lat_addr  <= s_apb_paddr;
            lat_wdata <= s_apb_pwdata;
            lat_strb  <= s_apb_pstrb;
            buf_cnt   <= 4'(BUF_CYCS);
            if (BUF_CYCS == 0) st <= s_apb_pwrite ? S_AXI_AW : S_AXI_AR;
            else               st <= S_BUF;
          end
        end
        S_BUF: begin
          if (buf_cnt <= 4'd1) st <= lat_wr ? S_AXI_AW : S_AXI_AR;
          else                 buf_cnt <= buf_cnt - 4'd1;
        end
        S_AXI_AW: if (m_axi_awready) st <= S_AXI_W;
        S_AXI_W:  if (m_axi_wready)  st <= S_AXI_B;
        S_AXI_B:  if (m_axi_bvalid) begin
          s_apb_pready  <= 1'b1;
          s_apb_pslverr <= (m_axi_bresp != 2'b00);
          tx_id         <= tx_id + 4'd1;
          st            <= S_DONE;
        end
        S_AXI_AR: if (m_axi_arready) st <= S_AXI_R;
        S_AXI_R:  if (m_axi_rvalid) begin
          s_apb_pready  <= 1'b1;
          s_apb_prdata  <= m_axi_rdata;
          s_apb_pslverr <= (m_axi_rresp != 2'b00);
          tx_id         <= tx_id + 4'd1;
          st            <= S_DONE;
        end
        S_DONE: st <= S_IDLE;
        default: st <= S_IDLE;
      endcase
    end
  end
endmodule
