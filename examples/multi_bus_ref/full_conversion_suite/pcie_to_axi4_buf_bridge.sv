`timescale 1ns/1ps
module pcie_to_axi4_buf_bridge #(
  parameter int GEN      = 4,
  parameter int LANES    = 4,
  parameter int BUF_CYCS = 2
) (
  input  logic        clk,
  input  logic        por_n,
  input  logic        phy_rx_detect,
  input  logic        phy_deskew_ok,
  input  logic        pm_l1_req,
  output logic        pm_l1_ack,
  output logic [2:0]  ltssm_state,
  output logic        pcie_link_up,
  output logic [3:0]  pcie_cfg_speed,
  output logic [5:0]  pcie_cfg_lanes,
  output logic        tlp_reject_nol0,
  // Interface 1: Upstream PCIe TLP (64-bit)
  input  logic        pcie_rx_tvalid,
  output logic        pcie_rx_tready,
  input  logic [63:0] pcie_rx_tdata,
  input  logic        pcie_rx_tlast,
  output logic        pcie_tx_tvalid,
  input  logic        pcie_tx_tready,
  output logic [63:0] pcie_tx_tdata,
  output logic        pcie_tx_tlast,
  // Interface 2: Downstream AXI4 Master
  output logic [3:0]  m_axi_awid,
  output logic [15:0] m_axi_awaddr,
  output logic [7:0]  m_axi_awlen,
  output logic [2:0]  m_axi_awsize,
  output logic [1:0]  m_axi_awburst,
  output logic        m_axi_awvalid,
  input  logic        m_axi_awready,
  output logic [31:0] m_axi_wdata,
  output logic [3:0]  m_axi_wstrb,
  output logic        m_axi_wlast,
  output logic        m_axi_wvalid,
  input  logic        m_axi_wready,
  input  logic [3:0]  m_axi_bid,
  input  logic [1:0]  m_axi_bresp,
  input  logic        m_axi_bvalid,
  output logic        m_axi_bready,
  output logic [3:0]  m_axi_arid,
  output logic [15:0] m_axi_araddr,
  output logic [7:0]  m_axi_arlen,
  output logic [2:0]  m_axi_arsize,
  output logic [1:0]  m_axi_arburst,
  output logic        m_axi_arvalid,
  input  logic        m_axi_arready,
  input  logic [3:0]  m_axi_rid,
  input  logic [31:0] m_axi_rdata,
  input  logic [1:0]  m_axi_rresp,
  input  logic        m_axi_rlast,
  input  logic        m_axi_rvalid,
  output logic        m_axi_rready
);
  localparam logic [2:0] ST_DETECT = 3'd0, ST_POLL = 3'd1, ST_CFG = 3'd2, ST_L0 = 3'd3, ST_L1 = 3'd4, ST_FAIL = 3'd5;
  typedef enum logic [2:0] { B_IDLE, B_BUF, B_AW, B_W, B_B, B_AR, B_R } bst_t;
  bst_t        bst;
  logic [2:0]  tcnt;
  logic [3:0]  buf_cnt;
  logic [7:0]  lat_cmd, lat_tag;
  logic [15:0] lat_addr;
  logic [31:0] lat_data;
  logic        _u;
  assign _u = pcie_rx_tlast ^ (^m_axi_bid) ^ (^m_axi_bresp) ^ (^m_axi_rid) ^ (^m_axi_rresp) ^ m_axi_rlast;

  assign pcie_link_up   = (ltssm_state == ST_L0);
  assign pm_l1_ack      = (ltssm_state == ST_L1);
  assign pcie_cfg_speed = pcie_link_up ? 4'(GEN) : 4'd0;
  assign pcie_cfg_lanes = pcie_link_up ? 6'(LANES) : 6'd0;
  assign pcie_rx_tready = (bst == B_IDLE) && !pcie_tx_tvalid;

  assign m_axi_awid = lat_tag[3:0]; assign m_axi_awaddr = lat_addr; assign m_axi_awlen = 8'd0; assign m_axi_awsize = 3'd2; assign m_axi_awburst = 2'b01;
  assign m_axi_awvalid = (bst == B_AW);
  assign m_axi_wdata = lat_data; assign m_axi_wstrb = 4'hF; assign m_axi_wlast = 1'b1;
  assign m_axi_wvalid = (bst == B_W); assign m_axi_bready = (bst == B_B);
  assign m_axi_arid = lat_tag[3:0]; assign m_axi_araddr = lat_addr; assign m_axi_arlen = 8'd0; assign m_axi_arsize = 3'd2; assign m_axi_arburst = 2'b01;
  assign m_axi_arvalid = (bst == B_AR); assign m_axi_rready = (bst == B_R);

  always_ff @(posedge clk) begin
    if (!por_n) begin
      ltssm_state <= ST_DETECT; tcnt <= '0; bst <= B_IDLE; buf_cnt <= '0;
      lat_cmd <= '0; lat_tag <= '0; lat_addr <= '0; lat_data <= '0;
      tlp_reject_nol0 <= 1'b0; pcie_tx_tvalid <= 1'b0; pcie_tx_tdata <= '0; pcie_tx_tlast <= 1'b0;
    end else begin
      case (ltssm_state)
        ST_DETECT: if (phy_rx_detect) begin ltssm_state <= ST_POLL; tcnt <= 0; end else if (tcnt == 3) ltssm_state <= ST_FAIL; else tcnt <= tcnt + 1;
        ST_POLL:   if (tcnt == 1) begin ltssm_state <= ST_CFG; tcnt <= 0; end else tcnt <= tcnt + 1;
        ST_CFG:    if (phy_deskew_ok) ltssm_state <= ST_L0; else if (tcnt == 2) ltssm_state <= ST_FAIL; else tcnt <= tcnt + 1;
        ST_L0:     if (pm_l1_req && bst == B_IDLE && !pcie_tx_tvalid) ltssm_state <= ST_L1;
        ST_L1:     if (!pm_l1_req) ltssm_state <= ST_L0;
        ST_FAIL:   if (phy_rx_detect && phy_deskew_ok) begin ltssm_state <= ST_DETECT; tcnt <= 0; end
        default:   ltssm_state <= ST_DETECT;
      endcase

      tlp_reject_nol0 <= 1'b0;
      if (pcie_tx_tvalid && pcie_tx_tready) begin pcie_tx_tvalid <= 1'b0; pcie_tx_tlast <= 1'b0; end

      case (bst)
        B_IDLE: if (pcie_rx_tvalid && pcie_rx_tready) begin
          if (ltssm_state != ST_L0) begin
            tlp_reject_nol0 <= 1'b1;
          end else begin
            lat_cmd <= pcie_rx_tdata[63:56]; lat_tag <= pcie_rx_tdata[55:48];
            lat_addr <= pcie_rx_tdata[47:32]; lat_data <= pcie_rx_tdata[31:0];
            buf_cnt <= 4'(BUF_CYCS);
            if (BUF_CYCS == 0) bst <= (pcie_rx_tdata[63:56] == 8'h40) ? B_AW : B_AR;
            else               bst <= B_BUF;
          end
        end
        B_BUF: if (buf_cnt <= 4'd1) bst <= (lat_cmd == 8'h40) ? B_AW : B_AR; else buf_cnt <= buf_cnt - 4'd1;
        B_AW:  if (m_axi_awready) bst <= B_W;
        B_W:   if (m_axi_wready)  bst <= B_B;
        B_B:   if (m_axi_bvalid)  bst <= B_IDLE;
        B_AR:  if (m_axi_arready) bst <= B_R;
        B_R:   if (m_axi_rvalid) begin
          pcie_tx_tvalid <= 1'b1; pcie_tx_tlast <= 1'b1;
          pcie_tx_tdata  <= {8'h4A, lat_tag, lat_addr, m_axi_rdata};
          bst            <= B_IDLE;
        end
        default: bst <= B_IDLE;
      endcase
    end
  end
endmodule
