`timescale 1ns/1ps
module tb_pcie_to_axi4_buf #(parameter int BUF_CYCS = 2);
  logic        clk = 0, por_n = 0, phy_rx_detect = 1, phy_deskew_ok = 0, pm_l1_req = 0, pm_l1_ack;
  logic [2:0]  ltssm_state;
  logic        pcie_link_up, tlp_reject_nol0;
  logic [3:0]  pcie_cfg_speed;
  logic [5:0]  pcie_cfg_lanes;
  logic        pcie_rx_tvalid = 0, pcie_rx_tready, pcie_rx_tlast = 0;
  logic [63:0] pcie_rx_tdata = 0, pcie_tx_tdata;
  logic        pcie_tx_tvalid, pcie_tx_tready = 1, pcie_tx_tlast;
  logic [3:0]  m_axi_awid, m_axi_bid, m_axi_arid, m_axi_rid;
  logic [15:0] m_axi_awaddr, m_axi_araddr;
  logic [7:0]  m_axi_awlen, m_axi_arlen;
  logic [2:0]  m_axi_awsize, m_axi_arsize;
  logic [1:0]  m_axi_awburst, m_axi_arburst, m_axi_bresp, m_axi_rresp;
  logic        m_axi_awvalid, m_axi_awready, m_axi_wvalid, m_axi_wready, m_axi_wlast;
  logic        m_axi_bvalid, m_axi_bready, m_axi_arvalid, m_axi_arready, m_axi_rvalid, m_axi_rready, m_axi_rlast;
  logic [31:0] m_axi_wdata, m_axi_rdata;
  logic [3:0]  m_axi_wstrb;

  pcie_to_axi4_buf_bridge #(.GEN(4), .LANES(4), .BUF_CYCS(BUF_CYCS)) dut (.*);
  axi4_full_slave u_mem (
    .aclk(clk), .aresetn(por_n),
    .s_axi_awid(m_axi_awid), .s_axi_awaddr(m_axi_awaddr), .s_axi_awlen(m_axi_awlen),
    .s_axi_awsize(m_axi_awsize), .s_axi_awburst(m_axi_awburst), .s_axi_awvalid(m_axi_awvalid), .s_axi_awready(m_axi_awready),
    .s_axi_wdata(m_axi_wdata), .s_axi_wstrb(m_axi_wstrb), .s_axi_wlast(m_axi_wlast), .s_axi_wvalid(m_axi_wvalid), .s_axi_wready(m_axi_wready),
    .s_axi_bid(m_axi_bid), .s_axi_bresp(m_axi_bresp), .s_axi_bvalid(m_axi_bvalid), .s_axi_bready(m_axi_bready),
    .s_axi_arid(m_axi_arid), .s_axi_araddr(m_axi_araddr), .s_axi_arlen(m_axi_arlen),
    .s_axi_arsize(m_axi_arsize), .s_axi_arburst(m_axi_arburst), .s_axi_arvalid(m_axi_arvalid), .s_axi_arready(m_axi_arready),
    .s_axi_rid(m_axi_rid), .s_axi_rdata(m_axi_rdata), .s_axi_rresp(m_axi_rresp), .s_axi_rlast(m_axi_rlast), .s_axi_rvalid(m_axi_rvalid), .s_axi_rready(m_axi_rready)
  );
  always #5 clk = ~clk;

  logic [31:0] golden [0:63];
  int errs = 0;

  task automatic send_tlp(input logic [63:0] pkt);
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1; pcie_rx_tdata = pkt;
    while (!pcie_rx_tready) begin @(posedge clk); #1; end
    @(posedge clk); #1;
    pcie_rx_tvalid = 0; pcie_rx_tlast = 0;
  endtask

  initial begin
    int bad = 0;
    repeat (2) @(posedge clk); #1; por_n = 1;

    // 1. LTSSM Deskew Failure rejects TLP
    repeat (10) @(posedge clk); #1;
    send_tlp({8'h40, 8'h01, 16'h0000, 32'hBAD0_0000});
    if (ltssm_state !== 3'd5 || !tlp_reject_nol0) begin
      $display("[CHECK] id=PCIE2AXI_B%0d_001 | proto=PCIe-to-AXI4 | cat=ltssm_neg_fail | tx=POR deskew=0 -> TLP (BUF=%0d) | exp=st=5,rej=1 | got=st=%0d,rej=%0b | status=FAIL", BUF_CYCS, BUF_CYCS, ltssm_state, tlp_reject_nol0); errs++;
    end else begin
      $display("[CHECK] id=PCIE2AXI_B%0d_001 | proto=PCIe-to-AXI4 | cat=ltssm_neg_fail | tx=POR deskew=0 -> TLP (BUF=%0d) | exp=st=5,rej=1 | got=st=%0d,rej=%0b | status=PASS", BUF_CYCS, BUF_CYCS, ltssm_state, tlp_reject_nol0);
    end

    // 2. Recover to L0 & sweep ALL 64 words (256B) through PCIe -> BUF_CYCS -> AXI4 -> Backing MEM!
    phy_deskew_ok = 1;
    while (!pcie_link_up) @(posedge clk);
    @(posedge clk); #1;
    for (int w = 0; w < 64; w++) begin
      golden[w] = 32'h3300_0000 ^ (32'h0102_0304 * 32'(w + 1));
      send_tlp({8'h40, 8'(w), 16'(w * 4), golden[w]});
      while (!pcie_rx_tready) begin @(posedge clk); #1; end
    end
    for (int w = 63; w >= 0; w--) begin
      send_tlp({8'h00, 8'(w + 8'h80), 16'(w * 4), 32'h0});
      while (!(pcie_tx_tvalid && pcie_tx_tready)) begin @(posedge clk); #1; end
      if (pcie_tx_tdata[31:0] !== golden[w] || pcie_tx_tdata[55:48] !== 8'(w + 8'h80)) bad++;
      @(posedge clk); #1;
    end
    if (bad > 0) begin
      $display("[CHECK] id=PCIE2AXI_B%0d_002 | proto=PCIe-to-AXI4 | cat=full_256B_buf_sweep | tx=64W+64R reverse (BUF=%0d) | exp=256/256B_match | got=bad=%0d | status=FAIL", BUF_CYCS, BUF_CYCS, bad); errs++;
    end else begin
      $display("[CHECK] id=PCIE2AXI_B%0d_002 | proto=PCIe-to-AXI4 | cat=full_256B_buf_sweep | tx=64W+64R reverse (BUF=%0d) | exp=256/256B_match | got=256/256B_match | status=PASS", BUF_CYCS, BUF_CYCS);
    end

    // 3. L1 sleep -> reject -> wake -> verify retention
    pm_l1_req = 1; repeat (4) @(posedge clk); #1;
    send_tlp({8'h40, 8'hEE, 16'h0000, 32'h0});
    if (!pm_l1_ack || !tlp_reject_nol0) begin errs++; end
    pm_l1_req = 0; repeat (4) @(posedge clk); #1;
    send_tlp({8'h00, 8'h77, 16'h00FC, 32'h0});
    while (!(pcie_tx_tvalid && pcie_tx_tready)) begin @(posedge clk); #1; end
    if (pcie_tx_tdata[31:0] !== golden[63]) begin
      $display("[CHECK] id=PCIE2AXI_B%0d_003 | proto=PCIe-to-AXI4 | cat=l1_sleep_retention | tx=L1->wake RD 0xFC (BUF=%0d) | exp=0x%08h | got=0x%08h | status=FAIL", BUF_CYCS, BUF_CYCS, golden[63], pcie_tx_tdata[31:0]); errs++;
    end else begin
      $display("[CHECK] id=PCIE2AXI_B%0d_003 | proto=PCIe-to-AXI4 | cat=l1_sleep_retention | tx=L1->wake RD 0xFC (BUF=%0d) | exp=0x%08h | got=0x%08h | status=PASS", BUF_CYCS, BUF_CYCS, golden[63], pcie_tx_tdata[31:0]);
    end

    if (errs == 0) begin $display("PASS: pcie_to_axi4_buf(BUF=%0d) full 256B + LTSSM + L1 passed", BUF_CYCS); $finish; end
    else begin $display("FAIL: pcie_to_axi4_buf(BUF=%0d) %0d errors", BUF_CYCS, errs); $fatal(1); end
  end
endmodule
