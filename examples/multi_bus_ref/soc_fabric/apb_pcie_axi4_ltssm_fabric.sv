`timescale 1ns/1ps
module apb_pcie_axi4_ltssm_fabric #(
  parameter int GEN   = 4,
  parameter int LANES = 4
) (
  input  logic        clk,
  input  logic        por_n,
  // LTSSM Training & L1 Low-Power Handshake Controls
  input  logic        phy_rx_detect,    // 1=peer detected, 0=open/fail!
  input  logic        phy_deskew_ok,    // 1=lane deskew pass, 0=config fail!
  input  logic        pm_l1_req,        // L0 -> L1 low-power entry request
  output logic        pm_l1_ack,        // L1 handshake acknowledge
  output logic [2:0]  ltssm_state,      // 0=DETECT,1=POLLING,2=CONFIG,3=L0,4=L1,5=FAIL
  output logic        pcie_link_up,
  output logic [3:0]  pcie_cfg_speed,
  output logic [5:0]  pcie_cfg_lanes,
  output logic        tlp_reject_nol0,  // Asserted if TLP arrives when not in L0
  // Interface 1: PCIe TLP RX/TX (64-bit: [63:56]=cmd, [55:48]=tag, [47:32]=addr, [31:0]=data)
  input  logic        pcie_rx_tvalid,
  output logic        pcie_rx_tready,
  input  logic [63:0] pcie_rx_tdata,
  input  logic        pcie_rx_tlast,
  output logic        pcie_tx_tvalid,
  input  logic        pcie_tx_tready,
  output logic [63:0] pcie_tx_tdata,
  output logic        pcie_tx_tlast,
  // Interface 2: APB Slave (APB -> AXI4-equivalent path into SAME built-in 256B MEM!)
  input  logic        s_apb_psel,
  input  logic        s_apb_penable,
  input  logic        s_apb_pwrite,
  input  logic [15:0] s_apb_paddr,
  input  logic [31:0] s_apb_pwdata,
  input  logic [3:0]  s_apb_pstrb,
  output logic        s_apb_pready,
  output logic [31:0] s_apb_prdata,
  output logic        s_apb_pslverr
);
  localparam logic [2:0] ST_DETECT  = 3'd0;
  localparam logic [2:0] ST_POLLING = 3'd1;
  localparam logic [2:0] ST_CONFIG  = 3'd2;
  localparam logic [2:0] ST_L0      = 3'd3;
  localparam logic [2:0] ST_L1      = 3'd4;
  localparam logic [2:0] ST_FAIL    = 3'd5;

  // Built-in 256-byte shared AXI-style backing MEM (shared between PCIe and APB!)
  logic [31:0] shared_mem [0:63];
  logic [2:0]  train_cnt;
  logic        apb_wait;
  logic        _ulast;
  assign _ulast = pcie_rx_tlast;

  assign pcie_link_up   = (ltssm_state == ST_L0);
  assign pm_l1_ack      = (ltssm_state == ST_L1);
  assign pcie_cfg_speed = pcie_link_up ? 4'(GEN) : 4'd0;
  assign pcie_cfg_lanes = pcie_link_up ? 6'(LANES) : 6'd0;
  assign pcie_rx_tready = !pcie_tx_tvalid;

  always_ff @(posedge clk) begin
    if (!por_n) begin
      for (int i = 0; i < 64; i++) shared_mem[i] <= 32'h0;
      ltssm_state     <= ST_DETECT;
      train_cnt       <= 3'd0;
      tlp_reject_nol0 <= 1'b0;
      pcie_tx_tvalid  <= 1'b0; pcie_tx_tdata <= 64'h0; pcie_tx_tlast <= 1'b0;
      s_apb_pready    <= 1'b0; s_apb_prdata  <= 32'h0; s_apb_pslverr <= 1'b0;
      apb_wait        <= 1'b0;
    end else begin
      // ---- Power-On LTSSM + L1 Sleep/Wake FSM ----
      case (ltssm_state)
        ST_DETECT: begin
          if (phy_rx_detect) begin ltssm_state <= ST_POLLING; train_cnt <= 3'd0; end
          else if (train_cnt == 3'd3) ltssm_state <= ST_FAIL;
          else train_cnt <= train_cnt + 3'd1;
        end
        ST_POLLING: begin
          if (train_cnt == 3'd1) begin ltssm_state <= ST_CONFIG; train_cnt <= 3'd0; end
          else train_cnt <= train_cnt + 3'd1;
        end
        ST_CONFIG: begin
          if (phy_deskew_ok) ltssm_state <= ST_L0;
          else if (train_cnt == 3'd2) ltssm_state <= ST_FAIL; // Negotiation / deskew failure!
          else train_cnt <= train_cnt + 3'd1;
        end
        ST_L0: begin
          if (pm_l1_req && !pcie_tx_tvalid && !pcie_rx_tvalid)
            ltssm_state <= ST_L1; // Enter L1 low-power sleep
        end
        ST_L1: begin
          if (!pm_l1_req) ltssm_state <= ST_L0; // Wake back to L0 (MEM preserved!)
        end
        ST_FAIL: begin
          if (phy_rx_detect && phy_deskew_ok) begin ltssm_state <= ST_DETECT; train_cnt <= 3'd0; end
        end
        default: ltssm_state <= ST_DETECT;
      endcase

      // ---- Path 1: PCIe TLP -> Shared MEM (gated strictly by ST_L0!) ----
      tlp_reject_nol0 <= 1'b0;
      if (pcie_tx_tvalid && pcie_tx_tready) begin
        pcie_tx_tvalid <= 1'b0; pcie_tx_tlast <= 1'b0;
      end
      if (pcie_rx_tvalid && pcie_rx_tready) begin
        if (ltssm_state != ST_L0) begin
          tlp_reject_nol0 <= 1'b1; // Reject TLP if link is in DETECT/CONFIG/FAIL/L1!
        end else if (pcie_rx_tdata[63:56] == 8'h40) begin
          shared_mem[pcie_rx_tdata[39:34]] <= pcie_rx_tdata[31:0];
        end else if (pcie_rx_tdata[63:56] == 8'h00) begin
          pcie_tx_tvalid <= 1'b1;
          pcie_tx_tlast  <= 1'b1;
          pcie_tx_tdata  <= {8'h4A, pcie_rx_tdata[55:48], pcie_rx_tdata[47:32], shared_mem[pcie_rx_tdata[39:34]]};
        end
      end

      // ---- Path 2: APB -> Shared MEM (always accessible to verify cross-protocol data!) ----
      s_apb_pready <= 1'b0; s_apb_pslverr <= 1'b0;
      if (s_apb_psel && s_apb_penable && !apb_wait) begin
        apb_wait <= 1'b1; s_apb_pready <= 1'b1;
        if (s_apb_paddr >= 16'h0100) begin
          s_apb_pslverr <= 1'b1;
        end else if (s_apb_pwrite) begin
          for (int b = 0; b < 4; b++)
            if (s_apb_pstrb[b]) shared_mem[s_apb_paddr[7:2]][b*8 +: 8] <= s_apb_pwdata[b*8 +: 8];
        end else begin
          s_apb_prdata <= shared_mem[s_apb_paddr[7:2]];
        end
      end else begin
        apb_wait <= 1'b0;
      end
    end
  end
endmodule
