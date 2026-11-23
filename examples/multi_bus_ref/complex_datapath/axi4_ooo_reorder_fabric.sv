`timescale 1ns/1ps
module axi4_ooo_reorder_fabric #(
  parameter int IDW = 4,
  parameter int AW  = 16,
  parameter int DW  = 32
) (
  input  logic           aclk,
  input  logic           aresetn,
  // Interface 1: Upstream AXI4 Slave
  input  logic [IDW-1:0] s_axi_awid,
  input  logic [AW-1:0]  s_axi_awaddr,
  input  logic [7:0]     s_axi_awlen,
  input  logic [2:0]     s_axi_awsize,
  input  logic [1:0]     s_axi_awburst,
  input  logic           s_axi_awvalid,
  output logic           s_axi_awready,
  input  logic [DW-1:0]  s_axi_wdata,
  input  logic [3:0]     s_axi_wstrb,
  input  logic           s_axi_wlast,
  input  logic           s_axi_wvalid,
  output logic           s_axi_wready,
  output logic [IDW-1:0] s_axi_bid,
  output logic [1:0]     s_axi_bresp,
  output logic           s_axi_bvalid,
  input  logic           s_axi_bready,
  input  logic [IDW-1:0] s_axi_arid,
  input  logic [AW-1:0]  s_axi_araddr,
  input  logic [7:0]     s_axi_arlen,
  input  logic [2:0]     s_axi_arsize,
  input  logic [1:0]     s_axi_arburst,
  input  logic           s_axi_arvalid,
  output logic           s_axi_arready,
  output logic [IDW-1:0] s_axi_rid,
  output logic [DW-1:0]  s_axi_rdata,
  output logic [1:0]     s_axi_rresp,
  output logic           s_axi_rlast,
  output logic           s_axi_rvalid,
  input  logic           s_axi_rready,
  // Interface 2: Downstream AXI4 Master (to backing memory / slave)
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
  // Write path pass-through with RAW snooping record
  assign m_axi_awid    = s_axi_awid;
  assign m_axi_awaddr  = s_axi_awaddr;
  assign m_axi_awlen   = s_axi_awlen;
  assign m_axi_awsize  = s_axi_awsize;
  assign m_axi_awburst = s_axi_awburst;
  assign m_axi_awvalid = s_axi_awvalid;
  assign s_axi_awready = m_axi_awready;

  assign m_axi_wdata   = s_axi_wdata;
  assign m_axi_wstrb   = s_axi_wstrb;
  assign m_axi_wlast   = s_axi_wlast;
  assign m_axi_wvalid  = s_axi_wvalid;
  assign s_axi_wready  = m_axi_wready;

  assign s_axi_bid     = m_axi_bid;
  assign s_axi_bresp   = m_axi_bresp;
  assign s_axi_bvalid  = m_axi_bvalid;
  assign m_axi_bready  = s_axi_bready;

  // Two-slot OoO Read Scheduler:
  //   slot 0 (slow pipe, even word addr[2]==0): 5-cycle delay before upstream R return
  //   slot 1 (fast pipe, odd  word addr[2]==1): 1-cycle delay -> overtakes slot 0 for different ARID!
  logic           s0_v, s1_v;
  logic [IDW-1:0] s0_id, s1_id;
  logic [DW-1:0]  s0_data, s1_data;
  logic [1:0]     s0_resp, s1_resp;
  logic [2:0]     s0_cnt, s1_cnt;
  logic           pending_ar;
  logic           pending_is_odd;
  logic [IDW-1:0] pending_id;
  logic           _u;
  assign _u = (^s_axi_arlen) ^ (^s_axi_arsize) ^ (^s_axi_arburst) ^ m_axi_rlast ^ (^m_axi_rid);

  wire is_odd = s_axi_araddr[2];
  // Same-ID hazard check: stall if the OTHER slot holds the same ARID (AXI4 same-ID in-order rule)
  wire same_id_hazard = (s0_v && s0_id == s_axi_arid) || (s1_v && s1_id == s_axi_arid);
  wire target_free    = is_odd ? !s1_v : !s0_v;

  assign m_axi_arid    = s_axi_arid;
  assign m_axi_araddr  = s_axi_araddr;
  assign m_axi_arlen   = 8'd0;
  assign m_axi_arsize  = 3'd2;
  assign m_axi_arburst = 2'b01;
  assign m_axi_arvalid = s_axi_arvalid && !pending_ar && target_free && !same_id_hazard;
  assign s_axi_arready = m_axi_arready && !pending_ar && target_free && !same_id_hazard;
  assign m_axi_rready  = pending_ar;

  wire s1_ready = s1_v && (s1_cnt == 3'd0);
  wire s0_ready = s0_v && (s0_cnt == 3'd0);

  // Arbitrate fast pipe (slot 1) first so fast odd-bank reads overtake slow even-bank reads!
  assign s_axi_rvalid = s1_ready || s0_ready;
  assign s_axi_rid    = s1_ready ? s1_id   : s0_id;
  assign s_axi_rdata  = s1_ready ? s1_data : s0_data;
  assign s_axi_rresp  = s1_ready ? s1_resp : s0_resp;
  assign s_axi_rlast  = s_axi_rvalid;

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      s0_v <= 1'b0; s1_v <= 1'b0;
      s0_id <= '0; s1_id <= '0;
      s0_data <= '0; s1_data <= '0;
      s0_resp <= '0; s1_resp <= '0;
      s0_cnt <= '0; s1_cnt <= '0;
      pending_ar <= 1'b0; pending_is_odd <= 1'b0; pending_id <= '0;
    end else begin
      if (s0_v && s0_cnt > 3'd0) s0_cnt <= s0_cnt - 3'd1;
      if (s1_v && s1_cnt > 3'd0) s1_cnt <= s1_cnt - 3'd1;

      // Consume upstream R handshake
      if (s_axi_rvalid && s_axi_rready) begin
        if (s1_ready) s1_v <= 1'b0;
        else if (s0_ready) s0_v <= 1'b0;
      end

      // Issue downstream AR
      if (m_axi_arvalid && m_axi_arready) begin
        pending_ar     <= 1'b1;
        pending_is_odd <= is_odd;
        pending_id     <= s_axi_arid;
      end

      // Capture downstream R into slow (5-cycle) or fast (1-cycle) reorder slot
      if (pending_ar && m_axi_rvalid && m_axi_rready) begin
        pending_ar <= 1'b0;
        if (pending_is_odd) begin
          s1_v    <= 1'b1;
          s1_id   <= pending_id;
          s1_data <= m_axi_rdata;
          s1_resp <= m_axi_rresp;
          s1_cnt  <= 3'd1; // Fast pipe
        end else begin
          s0_v    <= 1'b1;
          s0_id   <= pending_id;
          s0_data <= m_axi_rdata;
          s0_resp <= m_axi_rresp;
          s0_cnt  <= 3'd5; // Slow pipe -> allows subsequent different-ID odd read to overtake!
        end
      end
    end
  end
endmodule
