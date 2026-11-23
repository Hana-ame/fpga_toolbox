`timescale 1ns/1ps
// ============================================================================
// Unified All-Directional Translation + Shared 256B Memory SoC Hub
//   6 Standard Interfaces around 1 Arbitrated, BUF_CYCS-Buffered 256B Backing MEM:
//     IF1: SPI Slave  (Mode 0/1/2/3 CPOL/CPHA)
//     IF2: APB4 Slave (32-bit + PSTRB + PSLVERR)
//     IF3: AXI4 Slave (32-bit + WSTRB + ID + SLVERR)
//     IF4: Ethernet GMII (Auto-Negotiation + Cable Hot-Plug + CRC + EEE Sleep)
//     IF5: PCIe TLP   (POR->LTSSM->L0 + Surprise Hot-Plug + L1 Sleep)
//     IF6: DDR4 PHY   (POR->CKE->MRS->ZQCL->READY + Self-Refresh Sleep)
// ============================================================================
module soc_omni_bus_hub #(
  parameter int BUF_CYCS = 2,
  parameter int GEN      = 4,
  parameter int LANES    = 4
) (
  input  logic        clk,
  input  logic        por_n,
  // ---- Mode / Hot-Plug / Negotiation / Power-Management Controls ----
  input  logic [1:0]  spi_mode,             // {CPOL, CPHA} = 00..11
  input  logic        eth_cable_present,    // 1=plugged, 0=unplugged (hot-plug!)
  input  logic [1:0]  eth_peer_cap,         // 11=1000M, 10=100M, 00=AN_FAIL
  input  logic        eee_sleep_req,
  output logic        eee_sleep_ack,
  input  logic        pcie_hotplug_present, // 1=inserted, 0=surprise-removed (hot-plug!)
  input  logic        pcie_deskew_ok,       // 1=deskew pass, 0=LTSSM config fail
  input  logic        pm_l1_req,
  output logic        pm_l1_ack,
  input  logic        ddr_sr_req,
  output logic        ddr_sr_ack,
  output logic        ddr_ready,
  output logic [2:0]  ltssm_state,          // 0=DETECT,1=POLL,2=CFG,3=L0,4=L1,5=FAIL
  output logic        tlp_reject_nol0,
  output logic        rx_crc_err,
  output logic        rx_drop_nolink,
  // ---- IF1: SPI Slave (16b frame: [15]=WR, [14:8]=addr, [7:0]=data) ----
  input  logic        s_spi_sclk,
  input  logic        s_spi_cs_n,
  input  logic        s_spi_mosi,
  output logic        s_spi_miso,
  // ---- IF2: APB4 Slave ----
  input  logic        s_apb_psel,
  input  logic        s_apb_penable,
  input  logic        s_apb_pwrite,
  input  logic [15:0] s_apb_paddr,
  input  logic [31:0] s_apb_pwdata,
  input  logic [3:0]  s_apb_pstrb,
  output logic        s_apb_pready,
  output logic [31:0] s_apb_prdata,
  output logic        s_apb_pslverr,
  // ---- IF3: AXI4 Slave ----
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
  // ---- IF4: Ethernet GMII RX/TX ----
  input  logic        eth_rx_dv,
  input  logic [7:0]  eth_rx_data,
  output logic        eth_tx_en,
  output logic [7:0]  eth_tx_data,
  output logic        eth_an_done,
  output logic        eth_an_fail,
  output logic        eth_link_up,
  output logic [1:0]  eth_speed,
  // ---- IF5: PCIe TLP RX/TX ----
  input  logic        pcie_rx_tvalid,
  output logic        pcie_rx_tready,
  input  logic [63:0] pcie_rx_tdata,
  input  logic        pcie_rx_tlast,
  output logic        pcie_tx_tvalid,
  input  logic        pcie_tx_tready,
  output logic [63:0] pcie_tx_tdata,
  output logic        pcie_tx_tlast,
  output logic        pcie_link_up,
  output logic [3:0]  pcie_cfg_speed,
  output logic [5:0]  pcie_cfg_lanes,
  // ---- IF6: DDR4 PHY Command Bus (backing 256B shared MEM) ----
  output logic        ddr4_cke,
  output logic        ddr4_act_n,
  output logic        ddr4_cs_n,
  output logic [1:0]  ddr4_bg,
  output logic [1:0]  ddr4_ba,
  output logic [16:0] ddr4_adr,
  output logic [63:0] ddr4_dq
);
  // Unified 256-Byte Shared Memory (64 x 32-bit words, accessible by ALL 6 protocols!)
  logic [7:0] mem [0:255];

  // ---- DDR4 POR -> CKE -> MRS -> ZQCL -> READY / Self-Refresh FSM ----
  localparam logic [2:0] D_POR = 0, D_CKE = 1, D_MRS = 2, D_ZQCL = 3, D_READY = 4, D_SR = 5;
  logic [2:0] ddr_st, ddr_cnt;
  assign ddr_ready  = (ddr_st == D_READY);
  assign ddr_sr_ack = (ddr_st == D_SR);
  assign ddr4_cke   = (ddr_st != D_POR && ddr_st != D_SR);

  // ---- Ethernet Auto-Negotiation + Cable Hot-Plug + EEE Sleep FSM ----
  localparam logic [1:0] E_DOWN = 0, E_TRAIN = 1, E_UP = 2, E_FAIL = 3;
  logic [1:0] eth_st;
  logic [2:0] eth_cnt;
  logic [1:0] eth_cap_d;
  assign eth_an_done = (eth_st == E_UP || eth_st == E_FAIL);
  assign eth_an_fail = (eth_st == E_FAIL);
  assign eth_link_up = (eth_st == E_UP) && eth_cable_present && !eee_sleep_ack;

  // ---- PCIe LTSSM + Surprise Hot-Plug + L1 Sleep FSM ----
  localparam logic [2:0] P_DETECT = 0, P_POLL = 1, P_CFG = 2, P_L0 = 3, P_L1 = 4, P_FAIL = 5;
  logic [2:0] pcie_cnt;
  assign pcie_link_up   = (ltssm_state == P_L0) && pcie_hotplug_present;
  assign pm_l1_ack      = (ltssm_state == P_L1);
  assign pcie_cfg_speed = pcie_link_up ? 4'(GEN) : 4'd0;
  assign pcie_cfg_lanes = pcie_link_up ? 6'(LANES) : 6'd0;

  // ---- SPI Slave 4-Mode Edge Detector ----
  logic sclk_d;
  wire sclk_rise   = s_spi_sclk && !sclk_d;
  wire sclk_fall   = !s_spi_sclk && sclk_d;
  wire sample_edge = (spi_mode[1] ^ spi_mode[0]) ? sclk_fall : sclk_rise;
  logic [15:0] spi_rx;
  logic [4:0]  spi_bcnt;
  logic [7:0]  spi_tx;
  assign s_spi_miso = spi_tx[7];

  // ---- APB Buffered FSM (BUF_CYCS) ----
  logic       apb_buf_act, apb_done;
  logic [3:0] apb_bcnt;
  logic       apb_wr_r;
  logic [15:0] apb_addr_r;
  logic [31:0] apb_wdata_r;
  logic [3:0]  apb_strb_r;

  // ---- AXI4 Buffered FSM (BUF_CYCS) ----
  logic       axi_wr_act, axi_wbuf_act, axi_rbuf_act;
  logic [3:0] axi_wbcnt, axi_rbcnt, axi_wid, axi_rid_r, axi_wstrb_r;
  logic [15:0] axi_waddr_r, axi_raddr_r;
  logic [31:0] axi_wdata_r;

  // ---- Ethernet RX/TX ----
  logic [2:0] rx_idx, tx_idx;
  logic [7:0] rx_addr, rx_xor, tx_addr, tx_xor;
  logic [7:0] rx_buf [0:3];
  logic       tx_act;

  // ---- PCIe Buffered FSM (BUF_CYCS) ----
  logic       pcie_buf_act;
  logic [3:0] pcie_bcnt;
  logic [63:0] pcie_lat;

  logic _u;
  assign _u = (^s_axi_awlen) ^ (^s_axi_awsize) ^ (^s_axi_awburst) ^ (^s_axi_arlen) ^ (^s_axi_arsize) ^ (^s_axi_arburst) ^ s_axi_wlast ^ pcie_rx_tlast;

  assign s_axi_awready  = !axi_wr_act && !axi_wbuf_act && !s_axi_bvalid;
  assign s_axi_wready   = axi_wr_act;
  assign s_axi_arready  = !axi_rbuf_act && !s_axi_rvalid;
  assign pcie_rx_tready = !pcie_buf_act && !pcie_tx_tvalid;

  always_ff @(posedge clk) begin
    if (!por_n) begin
      for (int i = 0; i < 256; i++) mem[i] <= 8'h00;
      ddr_st <= D_POR; ddr_cnt <= 0;
      ddr4_act_n <= 1; ddr4_cs_n <= 1; ddr4_bg <= 0; ddr4_ba <= 0; ddr4_adr <= 0; ddr4_dq <= 0;
      eth_st <= E_DOWN; eth_cnt <= 0; eth_speed <= 0; eth_cap_d <= 0; eee_sleep_ack <= 0;
      ltssm_state <= P_DETECT; pcie_cnt <= 0; tlp_reject_nol0 <= 0;
      sclk_d <= 0; spi_rx <= 0; spi_bcnt <= 0; spi_tx <= 0;
      apb_buf_act <= 0; apb_done <= 0; apb_bcnt <= 0; apb_wr_r <= 0; apb_addr_r <= 0; apb_wdata_r <= 0; apb_strb_r <= 0;
      s_apb_pready <= 0; s_apb_prdata <= 0; s_apb_pslverr <= 0;
      axi_wr_act <= 0; axi_wbuf_act <= 0; axi_rbuf_act <= 0; axi_wbcnt <= 0; axi_rbcnt <= 0;
      axi_wid <= 0; axi_rid_r <= 0; axi_waddr_r <= 0; axi_raddr_r <= 0; axi_wdata_r <= 0; axi_wstrb_r <= 0;
      s_axi_bvalid <= 0; s_axi_bresp <= 0; s_axi_bid <= 0;
      s_axi_rvalid <= 0; s_axi_rresp <= 0; s_axi_rid <= 0; s_axi_rdata <= 0; s_axi_rlast <= 0;
      rx_idx <= 0; rx_addr <= 0; rx_xor <= 0; rx_crc_err <= 0; rx_drop_nolink <= 0;
      tx_act <= 0; tx_idx <= 0; tx_addr <= 0; tx_xor <= 0; eth_tx_en <= 0; eth_tx_data <= 0;
      pcie_buf_act <= 0; pcie_bcnt <= 0; pcie_lat <= 0;
      pcie_tx_tvalid <= 0; pcie_tx_tdata <= 0; pcie_tx_tlast <= 0;
    end else begin
      ddr4_cs_n <= 1; ddr4_act_n <= 1;

      // 1. DDR4 POR -> CKE -> MRS -> ZQCL -> READY / Self-Refresh
      case (ddr_st)
        D_POR:   if (ddr_cnt == 2) begin ddr_st <= D_CKE;  ddr_cnt <= 0; end else ddr_cnt <= ddr_cnt + 1;
        D_CKE:   if (ddr_cnt == 2) begin ddr_st <= D_MRS;  ddr_cnt <= 0; end else ddr_cnt <= ddr_cnt + 1;
        D_MRS:   begin ddr4_cs_n <= 0; ddr4_adr <= 17'h0010; ddr_st <= D_ZQCL; end
        D_ZQCL:  begin ddr4_cs_n <= 0; ddr4_adr <= 17'h0400; ddr_st <= D_READY; end
        D_READY: if (ddr_sr_req && !axi_wbuf_act && !axi_rbuf_act && !apb_buf_act && !pcie_buf_act) ddr_st <= D_SR;
        D_SR:    if (!ddr_sr_req) ddr_st <= D_READY;
        default: ddr_st <= D_POR;
      endcase

      // 2. Ethernet Cable Hot-Plug + Auto-Negotiation + EEE Sleep
      eth_cap_d <= eth_peer_cap;
      if (!eth_cable_present) begin
        // Hot-plug cable unplug -> immediate link drop & abort!
        eth_st <= E_DOWN; eth_speed <= 2'b00; eth_cnt <= 0; eee_sleep_ack <= 0;
      end else begin
        case (eth_st)
          E_DOWN: begin eth_st <= E_TRAIN; eth_cnt <= 0; end
          E_TRAIN: begin
            if (eth_cnt == 2) begin
              if (eth_peer_cap == 2'b11)      begin eth_st <= E_UP;   eth_speed <= 2'b10; end
              else if (eth_peer_cap == 2'b10) begin eth_st <= E_UP;   eth_speed <= 2'b01; end
              else                            begin eth_st <= E_FAIL; eth_speed <= 2'b00; end
            end else eth_cnt <= eth_cnt + 1;
          end
          E_UP, E_FAIL: begin
            if (eth_peer_cap != eth_cap_d) begin eth_st <= E_TRAIN; eth_cnt <= 0; end
          end
        endcase
        if (eee_sleep_req && eth_st == E_UP && !tx_act && !eth_rx_dv) eee_sleep_ack <= 1;
        else if (!eee_sleep_req) eee_sleep_ack <= 0;
      end

      // 3. PCIe Surprise Hot-Plug + LTSSM + L1 Sleep
      if (!pcie_hotplug_present) begin
        // Surprise removal -> immediate drop to DETECT!
        ltssm_state <= P_DETECT; pcie_cnt <= 0;
      end else begin
        case (ltssm_state)
          P_DETECT: begin ltssm_state <= P_POLL; pcie_cnt <= 0; end
          P_POLL:   if (pcie_cnt == 1) begin ltssm_state <= P_CFG; pcie_cnt <= 0; end else pcie_cnt <= pcie_cnt + 1;
          P_CFG:    if (pcie_deskew_ok) ltssm_state <= P_L0; else if (pcie_cnt == 2) ltssm_state <= P_FAIL; else pcie_cnt <= pcie_cnt + 1;
          P_L0:     if (pm_l1_req && !pcie_buf_act && !pcie_tx_tvalid) ltssm_state <= P_L1;
          P_L1:     if (!pm_l1_req) ltssm_state <= P_L0;
          P_FAIL:   if (pcie_deskew_ok) begin ltssm_state <= P_DETECT; pcie_cnt <= 0; end
          default:  ltssm_state <= P_DETECT;
        endcase
      end

      // 4. IF1: SPI Slave -> Shared MEM (Mode 0/1/2/3)
      sclk_d <= s_spi_sclk;
      if (!ddr_ready || s_spi_cs_n) begin
        spi_bcnt <= 0;
      end else if (sample_edge) begin
        spi_rx <= {spi_rx[14:0], s_spi_mosi};
        if (spi_bcnt == 7) begin
          if (!spi_rx[6]) spi_tx <= mem[{1'b0, spi_rx[5:0], s_spi_mosi}];
          spi_bcnt <= spi_bcnt + 1;
        end else if (spi_bcnt == 15) begin
          logic [15:0] fr;
          fr = {spi_rx[14:0], s_spi_mosi};
          if (fr[15]) mem[{1'b0, fr[14:8]}] <= fr[7:0];
          spi_bcnt <= 0;
        end else begin
          if (spi_bcnt > 7) spi_tx <= {spi_tx[6:0], 1'b0};
          spi_bcnt <= spi_bcnt + 1;
        end
      end

      // 5. IF2: APB4 Slave (with BUF_CYCS buffer) -> Shared MEM
      s_apb_pready <= 0; s_apb_pslverr <= 0;
      if (apb_done) begin
        apb_done <= 0;
      end else if (!apb_buf_act && s_apb_psel && s_apb_penable) begin
        apb_wr_r <= s_apb_pwrite; apb_addr_r <= s_apb_paddr; apb_wdata_r <= s_apb_pwdata; apb_strb_r <= s_apb_pstrb;
        if (BUF_CYCS == 0) begin
          s_apb_pready <= 1; apb_done <= 1;
          if (!ddr_ready || s_apb_paddr >= 16'h0100) begin s_apb_pslverr <= 1; s_apb_prdata <= 0; end
          else if (s_apb_pwrite) begin
            for (int b = 0; b < 4; b++) if (s_apb_pstrb[b]) mem[s_apb_paddr[7:0] + 8'(b)] <= s_apb_pwdata[b*8 +: 8];
          end else begin
            s_apb_prdata <= {mem[s_apb_paddr[7:0]+8'd3], mem[s_apb_paddr[7:0]+8'd2], mem[s_apb_paddr[7:0]+8'd1], mem[s_apb_paddr[7:0]+8'd0]};
          end
        end else begin
          apb_buf_act <= 1; apb_bcnt <= 4'(BUF_CYCS);
        end
      end else if (apb_buf_act) begin
        if (apb_bcnt <= 1) begin
          apb_buf_act <= 0; s_apb_pready <= 1; apb_done <= 1;
          if (!ddr_ready || apb_addr_r >= 16'h0100) begin s_apb_pslverr <= 1; s_apb_prdata <= 0; end
          else if (apb_wr_r) begin
            for (int b = 0; b < 4; b++) if (apb_strb_r[b]) mem[apb_addr_r[7:0] + 8'(b)] <= apb_wdata_r[b*8 +: 8];
          end else begin
            s_apb_prdata <= {mem[apb_addr_r[7:0]+8'd3], mem[apb_addr_r[7:0]+8'd2], mem[apb_addr_r[7:0]+8'd1], mem[apb_addr_r[7:0]+8'd0]};
          end
        end else apb_bcnt <= apb_bcnt - 1;
      end

      // 6. IF3: AXI4 Slave (with BUF_CYCS buffer) -> Shared MEM + ETH TX trigger (0x0100)
      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
      if (s_axi_rvalid && s_axi_rready) begin s_axi_rvalid <= 0; s_axi_rlast <= 0; end

      if (s_axi_awvalid && s_axi_awready) begin axi_wr_act <= 1; axi_wid <= s_axi_awid; axi_waddr_r <= s_axi_awaddr; end
      else if (axi_wr_act && s_axi_wvalid && s_axi_wready) begin
        axi_wr_act <= 0; axi_wdata_r <= s_axi_wdata; axi_wstrb_r <= s_axi_wstrb;
        if (BUF_CYCS == 0) begin
          s_axi_bvalid <= 1; s_axi_bid <= axi_wid;
          if (!ddr_ready) s_axi_bresp <= 2'b10;
          else if (axi_waddr_r < 16'h0100) begin
            for (int b = 0; b < 4; b++) if (s_axi_wstrb[b]) mem[axi_waddr_r[7:0] + 8'(b)] <= s_axi_wdata[b*8 +: 8];
            ddr4_cs_n <= 0; ddr4_bg <= axi_waddr_r[7:6]; ddr4_ba <= axi_waddr_r[5:4]; ddr4_dq <= {32'h0, s_axi_wdata};
            s_axi_bresp <= 2'b00;
          end else if (axi_waddr_r == 16'h0100) begin
            if (!eth_link_up) s_axi_bresp <= 2'b10;
            else begin tx_act <= 1; tx_idx <= 0; tx_addr <= s_axi_wdata[7:0]; tx_xor <= 0; s_axi_bresp <= 2'b00; end
          end else s_axi_bresp <= 2'b10;
        end else begin axi_wbuf_act <= 1; axi_wbcnt <= 4'(BUF_CYCS); end
      end else if (axi_wbuf_act) begin
        if (axi_wbcnt <= 1) begin
          axi_wbuf_act <= 0; s_axi_bvalid <= 1; s_axi_bid <= axi_wid;
          if (!ddr_ready) s_axi_bresp <= 2'b10;
          else if (axi_waddr_r < 16'h0100) begin
            for (int b = 0; b < 4; b++) if (axi_wstrb_r[b]) mem[axi_waddr_r[7:0] + 8'(b)] <= axi_wdata_r[b*8 +: 8];
            ddr4_cs_n <= 0; ddr4_bg <= axi_waddr_r[7:6]; ddr4_ba <= axi_waddr_r[5:4]; ddr4_dq <= {32'h0, axi_wdata_r};
            s_axi_bresp <= 2'b00;
          end else if (axi_waddr_r == 16'h0100) begin
            if (!eth_link_up) s_axi_bresp <= 2'b10;
            else begin tx_act <= 1; tx_idx <= 0; tx_addr <= axi_wdata_r[7:0]; tx_xor <= 0; s_axi_bresp <= 2'b00; end
          end else s_axi_bresp <= 2'b10;
        end else axi_wbcnt <= axi_wbcnt - 1;
      end

      if (s_axi_arvalid && s_axi_arready) begin
        axi_rid_r <= s_axi_arid; axi_raddr_r <= s_axi_araddr;
        if (BUF_CYCS == 0) begin
          s_axi_rvalid <= 1; s_axi_rid <= s_axi_arid; s_axi_rlast <= 1;
          if (!ddr_ready || s_axi_araddr >= 16'h0100) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
          else begin
            s_axi_rdata <= {mem[s_axi_araddr[7:0]+8'd3], mem[s_axi_araddr[7:0]+8'd2], mem[s_axi_araddr[7:0]+8'd1], mem[s_axi_araddr[7:0]+8'd0]};
            s_axi_rresp <= 2'b00;
          end
        end else begin axi_rbuf_act <= 1; axi_rbcnt <= 4'(BUF_CYCS); end
      end else if (axi_rbuf_act) begin
        if (axi_rbcnt <= 1) begin
          axi_rbuf_act <= 0; s_axi_rvalid <= 1; s_axi_rid <= axi_rid_r; s_axi_rlast <= 1;
          if (!ddr_ready || axi_raddr_r >= 16'h0100) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
          else begin
            s_axi_rdata <= {mem[axi_raddr_r[7:0]+8'd3], mem[axi_raddr_r[7:0]+8'd2], mem[axi_raddr_r[7:0]+8'd1], mem[axi_raddr_r[7:0]+8'd0]};
            s_axi_rresp <= 2'b00;
          end
        end else axi_rbcnt <= axi_rbcnt - 1;
      end

      // 7. IF4: Ethernet RX -> CRC -> Shared MEM + TX serializer
      rx_crc_err <= 0; rx_drop_nolink <= 0;
      if (eth_rx_dv) begin
        if (!eth_link_up || !ddr_ready) begin
          rx_drop_nolink <= 1; rx_idx <= 0;
        end else begin
          case (rx_idx)
            3'd0: if (eth_rx_data == 8'hD5) begin rx_idx <= 1; rx_xor <= 8'hD5; end
            3'd1: begin rx_addr <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 2; end
            3'd2: begin rx_buf[0] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 3; end
            3'd3: begin rx_buf[1] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 4; end
            3'd4: begin rx_buf[2] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 5; end
            3'd5: begin rx_buf[3] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 6; end
            3'd6: begin
              if (eth_rx_data == rx_xor) begin
                mem[rx_addr + 8'd0] <= rx_buf[0]; mem[rx_addr + 8'd1] <= rx_buf[1];
                mem[rx_addr + 8'd2] <= rx_buf[2]; mem[rx_addr + 8'd3] <= rx_buf[3];
              end else rx_crc_err <= 1;
              rx_idx <= 0;
            end
            default: rx_idx <= 0;
          endcase
        end
      end else rx_idx <= 0;

      eth_tx_en <= 0;
      if (tx_act) begin
        if (!eth_link_up) begin tx_act <= 0; tx_idx <= 0; end
        else begin
          eth_tx_en <= 1;
          case (tx_idx)
            3'd0: begin eth_tx_data <= 8'hD5; tx_xor <= 8'hD5; tx_idx <= 1; end
            3'd1: begin eth_tx_data <= tx_addr; tx_xor <= tx_xor ^ tx_addr; tx_idx <= 2; end
            3'd2: begin eth_tx_data <= mem[tx_addr+8'd0]; tx_xor <= tx_xor ^ mem[tx_addr+8'd0]; tx_idx <= 3; end
            3'd3: begin eth_tx_data <= mem[tx_addr+8'd1]; tx_xor <= tx_xor ^ mem[tx_addr+8'd1]; tx_idx <= 4; end
            3'd4: begin eth_tx_data <= mem[tx_addr+8'd2]; tx_xor <= tx_xor ^ mem[tx_addr+8'd2]; tx_idx <= 5; end
            3'd5: begin eth_tx_data <= mem[tx_addr+8'd3]; tx_xor <= tx_xor ^ mem[tx_addr+8'd3]; tx_idx <= 6; end
            3'd6: begin eth_tx_data <= tx_xor; tx_act <= 0; tx_idx <= 0; end
            default: tx_act <= 0;
          endcase
        end
      end

      // 8. IF5: PCIe TLP (with BUF_CYCS buffer) -> Shared MEM
      tlp_reject_nol0 <= 0;
      if (pcie_tx_tvalid && pcie_tx_tready) begin pcie_tx_tvalid <= 0; pcie_tx_tlast <= 0; end
      if (!pcie_buf_act && pcie_rx_tvalid && pcie_rx_tready) begin
        if (!pcie_link_up || !ddr_ready) begin
          tlp_reject_nol0 <= 1;
        end else if (BUF_CYCS == 0) begin
          if (pcie_rx_tdata[63:56] == 8'h40) begin
            mem[pcie_rx_tdata[39:32]+8'd0] <= pcie_rx_tdata[7:0];
            mem[pcie_rx_tdata[39:32]+8'd1] <= pcie_rx_tdata[15:8];
            mem[pcie_rx_tdata[39:32]+8'd2] <= pcie_rx_tdata[23:16];
            mem[pcie_rx_tdata[39:32]+8'd3] <= pcie_rx_tdata[31:24];
          end else if (pcie_rx_tdata[63:56] == 8'h00) begin
            pcie_tx_tvalid <= 1; pcie_tx_tlast <= 1;
            pcie_tx_tdata  <= {8'h4A, pcie_rx_tdata[55:48], pcie_rx_tdata[47:32],
              mem[pcie_rx_tdata[39:32]+8'd3], mem[pcie_rx_tdata[39:32]+8'd2],
              mem[pcie_rx_tdata[39:32]+8'd1], mem[pcie_rx_tdata[39:32]+8'd0]};
          end
        end else begin
          pcie_buf_act <= 1; pcie_bcnt <= 4'(BUF_CYCS); pcie_lat <= pcie_rx_tdata;
        end
      end else if (pcie_buf_act) begin
        if (pcie_bcnt <= 1) begin
          pcie_buf_act <= 0;
          if (!pcie_link_up || !ddr_ready) begin
            tlp_reject_nol0 <= 1;
          end else if (pcie_lat[63:56] == 8'h40) begin
            mem[pcie_lat[39:32]+8'd0] <= pcie_lat[7:0];
            mem[pcie_lat[39:32]+8'd1] <= pcie_lat[15:8];
            mem[pcie_lat[39:32]+8'd2] <= pcie_lat[23:16];
            mem[pcie_lat[39:32]+8'd3] <= pcie_lat[31:24];
          end else if (pcie_lat[63:56] == 8'h00) begin
            pcie_tx_tvalid <= 1; pcie_tx_tlast <= 1;
            pcie_tx_tdata  <= {8'h4A, pcie_lat[55:48], pcie_lat[47:32],
              mem[pcie_lat[39:32]+8'd3], mem[pcie_lat[39:32]+8'd2],
              mem[pcie_lat[39:32]+8'd1], mem[pcie_lat[39:32]+8'd0]};
          end
        end else pcie_bcnt <= pcie_bcnt - 1;
      end
    end
  end
endmodule
