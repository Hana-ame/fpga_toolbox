`timescale 1ns/1ps
module tb_soc_omni_bus_hub #(parameter int BUF_CYCS = 2);
  logic        clk = 0, por_n = 0;
  logic [1:0]  spi_mode = 2'b00, eth_peer_cap = 2'b00, eth_speed;
  logic        eth_cable_present = 1, eee_sleep_req = 0, eee_sleep_ack;
  logic        pcie_hotplug_present = 1, pcie_deskew_ok = 0, pm_l1_req = 0, pm_l1_ack;
  logic        ddr_sr_req = 0, ddr_sr_ack, ddr_ready;
  logic [2:0]  ltssm_state;
  logic        tlp_reject_nol0, rx_crc_err, rx_drop_nolink;
  logic        s_spi_sclk = 0, s_spi_cs_n = 1, s_spi_mosi = 0, s_spi_miso;
  logic        s_apb_psel = 0, s_apb_penable = 0, s_apb_pwrite = 0, s_apb_pready, s_apb_pslverr;
  logic [15:0] s_apb_paddr = 0;
  logic [31:0] s_apb_pwdata = 0, s_apb_prdata;
  logic [3:0]  s_apb_pstrb = 4'hF;
  logic [3:0]  s_axi_awid = 0, s_axi_bid, s_axi_arid = 0, s_axi_rid;
  logic [15:0] s_axi_awaddr = 0, s_axi_araddr = 0;
  logic [7:0]  s_axi_awlen = 0, s_axi_arlen = 0;
  logic [2:0]  s_axi_awsize = 3'd2, s_axi_arsize = 3'd2;
  logic [1:0]  s_axi_awburst = 2'b01, s_axi_arburst = 2'b01, s_axi_bresp, s_axi_rresp;
  logic        s_axi_awvalid = 0, s_axi_awready, s_axi_wvalid = 0, s_axi_wready, s_axi_wlast = 0;
  logic        s_axi_bvalid, s_axi_bready = 1, s_axi_arvalid = 0, s_axi_arready, s_axi_rvalid, s_axi_rready = 1, s_axi_rlast;
  logic [31:0] s_axi_wdata = 0, s_axi_rdata;
  logic [3:0]  s_axi_wstrb = 4'hF;
  logic        eth_rx_dv = 0, eth_tx_en, eth_an_done, eth_an_fail, eth_link_up;
  logic [7:0]  eth_rx_data = 0, eth_tx_data;
  logic        pcie_rx_tvalid = 0, pcie_rx_tready, pcie_rx_tlast = 0;
  logic [63:0] pcie_rx_tdata = 0, pcie_tx_tdata;
  logic        pcie_tx_tvalid, pcie_tx_tready = 1, pcie_tx_tlast;
  logic        pcie_link_up;
  logic [3:0]  pcie_cfg_speed;
  logic [5:0]  pcie_cfg_lanes;
  logic        ddr4_cke, ddr4_act_n, ddr4_cs_n;
  logic [1:0]  ddr4_bg, ddr4_ba;
  logic [16:0] ddr4_adr;
  logic [63:0] ddr4_dq;

  soc_omni_bus_hub #(.BUF_CYCS(BUF_CYCS), .GEN(4), .LANES(4)) dut (.*);
  always #5 clk = ~clk;

  byte unsigned golden [0:255];
  int errs = 0;

  function automatic logic [31:0] g_rd32(input logic [7:0] addr);
    return {golden[addr+8'd3], golden[addr+8'd2], golden[addr+8'd1], golden[addr+8'd0]};
  endfunction

  task automatic apb_op(input logic wr, input logic [15:0] addr, input logic [31:0] wd, input logic [3:0] strb, output logic [31:0] rd, output logic err);
    @(posedge clk); #1;
    s_apb_psel = 1; s_apb_penable = 0; s_apb_pwrite = wr; s_apb_paddr = addr; s_apb_pwdata = wd; s_apb_pstrb = strb;
    @(posedge clk); #1; s_apb_penable = 1;
    do begin @(posedge clk); #1; end while (!s_apb_pready);
    rd = s_apb_prdata; err = s_apb_pslverr;
    if (wr && !err && addr < 16'h0100) for (int b = 0; b < 4; b++) if (strb[b]) golden[addr[7:0]+8'(b)] = wd[b*8 +: 8];
    s_apb_psel = 0; s_apb_penable = 0;
  endtask

  task automatic axi_wr(input logic [15:0] addr, input logic [31:0] wd, input logic [3:0] strb, output logic [1:0] resp);
    @(posedge clk); #1; s_axi_awaddr = addr; s_axi_awvalid = 1;
    while (!s_axi_awready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_awvalid = 0;
    s_axi_wdata = wd; s_axi_wstrb = strb; s_axi_wlast = 1; s_axi_wvalid = 1;
    while (!s_axi_wready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_wvalid = 0; s_axi_wlast = 0;
    while (!(s_axi_bvalid && s_axi_bready)) begin @(posedge clk); #1; end
    resp = s_axi_bresp;
    if (resp == 2'b00 && addr < 16'h0100) for (int b = 0; b < 4; b++) if (strb[b]) golden[addr[7:0]+8'(b)] = wd[b*8 +: 8];
    @(posedge clk); #1;
  endtask

  task automatic axi_rd(input logic [15:0] addr, output logic [31:0] rd, output logic [1:0] resp);
    @(posedge clk); #1; s_axi_araddr = addr; s_axi_arvalid = 1;
    while (!s_axi_arready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_arvalid = 0;
    while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge clk); #1; end
    rd = s_axi_rdata; resp = s_axi_rresp;
    @(posedge clk); #1;
  endtask

  task automatic spi_wr_byte(input logic [1:0] mode, input logic [6:0] addr, input logic [7:0] val);
    logic cpol, cpha;
    logic [15:0] fr;
    cpol = mode[1]; cpha = mode[0]; fr = {1'b1, addr, val};
    spi_mode = mode; s_spi_sclk = cpol;
    @(posedge clk); #1; s_spi_cs_n = 0;
    for (int i = 15; i >= 0; i--) begin
      if (cpha == 0) begin
        s_spi_mosi = fr[i]; repeat (2) @(posedge clk); #1; s_spi_sclk = ~cpol; repeat (2) @(posedge clk); #1; s_spi_sclk = cpol;
      end else begin
        s_spi_sclk = ~cpol; s_spi_mosi = fr[i]; repeat (2) @(posedge clk); #1; s_spi_sclk = cpol; repeat (2) @(posedge clk); #1;
      end
    end
    @(posedge clk); #1; s_spi_cs_n = 1; repeat (2) @(posedge clk); #1;
    golden[{1'b0, addr}] = val;
  endtask

  task automatic eth_rx_word(input logic [7:0] addr, input logic [31:0] val, input logic bad_crc, input logic expect_store);
    logic [7:0] f [0:6];
    f[0] = 8'hD5; f[1] = addr;
    f[2] = val[7:0]; f[3] = val[15:8]; f[4] = val[23:16]; f[5] = val[31:24];
    f[6] = f[0] ^ f[1] ^ f[2] ^ f[3] ^ f[4] ^ f[5] ^ (bad_crc ? 8'hFF : 8'h00);
    for (int i = 0; i < 7; i++) begin eth_rx_dv = 1; eth_rx_data = f[i]; @(posedge clk); #1; end
    eth_rx_dv = 0; @(posedge clk); #1;
    if (expect_store && !bad_crc) for (int b = 0; b < 4; b++) golden[addr + 8'(b)] = val[b*8 +: 8];
  endtask

  task automatic pcie_mwr(input logic [7:0] tag, input logic [15:0] addr, input logic [31:0] val, input logic expect_store);
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1; pcie_rx_tdata = {8'h40, tag, addr, val};
    while (!pcie_rx_tready) begin @(posedge clk); #1; end
    @(posedge clk); #1; pcie_rx_tvalid = 0; pcie_rx_tlast = 0;
    while (!pcie_rx_tready) begin @(posedge clk); #1; end
    if (expect_store) for (int b = 0; b < 4; b++) golden[addr[7:0] + 8'(b)] = val[b*8 +: 8];
  endtask

  task automatic pcie_mrd(input logic [7:0] tag, input logic [15:0] addr, output logic [31:0] rd);
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1; pcie_rx_tdata = {8'h00, tag, addr, 32'h0};
    while (!pcie_rx_tready) begin @(posedge clk); #1; end
    @(posedge clk); #1; pcie_rx_tvalid = 0; pcie_rx_tlast = 0;
    while (!(pcie_tx_tvalid && pcie_tx_tready)) begin @(posedge clk); #1; end
    rd = pcie_tx_tdata[31:0];
    @(posedge clk); #1;
  endtask

  initial begin
    logic [31:0] rd;
    logic [1:0]  resp;
    logic        err;
    int          bad;
    for (int i = 0; i < 256; i++) golden[i] = 8'h00;

    // ==== 1. Power-On Reset Init Guard ====
    repeat (2) @(posedge clk); #1;
    por_n = 1;
    apb_op(1'b0, 16'h0000, 32'h0, 4'hF, rd, err);
    if (!err) begin
      $display("[CHECK] id=OMNI_B%0d_POR_001 | proto=OMNI-HUB | cat=por_init_guard | tx=APB RD before DDR4 READY (BUF=%0d) | exp=pslverr=1 | got=0 | status=FAIL", BUF_CYCS, BUF_CYCS); errs++;
    end else begin
      $display("[CHECK] id=OMNI_B%0d_POR_001 | proto=OMNI-HUB | cat=por_init_guard | tx=APB RD before DDR4 READY (BUF=%0d) | exp=pslverr=1 | got=1 | status=PASS", BUF_CYCS, BUF_CYCS);
    end

    while (!ddr_ready) @(posedge clk);
    repeat (8) @(posedge clk); #1;

    // ==== 2. Negotiation Failure Cases (ETH peer_cap=00 & PCIe deskew=0) ====
    axi_wr(16'h0100, 32'h0000_0000, 4'hF, resp); // ETH TX must fail
    pcie_rx_tvalid = 1; pcie_rx_tdata = {8'h40, 8'h01, 16'h00E0, 32'hBAD0_0001};
    @(posedge clk); #1; pcie_rx_tvalid = 0;
    if (!eth_an_fail || resp !== 2'b10 || ltssm_state !== 3'd5 || !tlp_reject_nol0) begin
      $display("[CHECK] id=OMNI_B%0d_NEG_002 | proto=OMNI-HUB | cat=an_and_ltssm_fail | tx=ETH cap=00 + PCIe deskew=0 | exp=an_fail=1,SLVERR,st=5,rej=1 | got=an=%0b,r=%0d,st=%0d,rej=%0b | status=FAIL", BUF_CYCS, eth_an_fail, resp, ltssm_state, tlp_reject_nol0); errs++;
    end else begin
      $display("[CHECK] id=OMNI_B%0d_NEG_002 | proto=OMNI-HUB | cat=an_and_ltssm_fail | tx=ETH cap=00 + PCIe deskew=0 | exp=an_fail=1,SLVERR,st=5,rej=1 | got=an=1,SLVERR,st=5,rej=1 | status=PASS", BUF_CYCS);
    end

    // Bring up Ethernet 1000M & PCIe Gen4x4 L0
    eth_peer_cap = 2'b11; pcie_deskew_ok = 1;
    while (!eth_link_up || !pcie_link_up) @(posedge clk);
    @(posedge clk); #1;

    // ==== 3. ALL-DIRECTIONAL TRANSLATION + FULL 256-BYTE SHARED MEM SWEEP ====
    // Region A (0x00..0x3F, 64B): SPI Mode 0/1/2/3 writes
    for (int i = 0; i < 64; i++) spi_wr_byte(2'(i & 3), 7'(i), 8'(i * 7 + 161));
    // Region B (0x40..0x7F, 64B): APB4 writes + partial PSTRB overwrites
    for (int w = 16; w < 32; w++) apb_op(1'b1, 16'(w*4), 32'hB000_0000 ^ (32'h0102_0304 * 32'(w)), 4'hF, rd, err);
    apb_op(1'b1, 16'h0040, 32'hFF00_EE00, 4'b1010, rd, err);
    // Region C (0x80..0xBF, 64B): AXI4 writes + partial WSTRB overwrites
    for (int w = 32; w < 48; w++) axi_wr(16'(w*4), 32'hC000_0000 ^ (32'h0506_0708 * 32'(w)), 4'hF, resp);
    axi_wr(16'h0084, 32'h00AA_00BB, 4'b0101, resp);
    // Region D (0xC0..0xDF, 32B): Ethernet RX valid frames + bad-CRC attack frame
    for (int w = 48; w < 56; w++) eth_rx_word(8'(w*4), 32'hD000_0000 ^ (32'h1122_3344 * 32'(w)), 1'b0, 1'b1);
    eth_rx_word(8'hC0, 32'hBAD0_BAD0, 1'b1, 1'b0); // bad CRC MUST be dropped!
    // Region E (0xE0..0xFF, 32B): PCIe MWr writes
    for (int w = 56; w < 64; w++) pcie_mwr(8'(w), 16'(w*4), 32'hE000_0000 ^ (32'h99AA_BBCC * 32'(w)), 1'b1);

    // Cross-protocol full 256B readback (every region read by a DIFFERENT protocol!)
    bad = 0;
    for (int w = 0; w < 16; w++) begin axi_rd(16'(w*4), rd, resp); if (resp != 0 || rd !== g_rd32(8'(w*4))) bad++; end          // SPI -> AXI4
    for (int w = 16; w < 32; w++) begin pcie_mrd(8'(w), 16'(w*4), rd); if (rd !== g_rd32(8'(w*4))) bad++; end                    // APB -> PCIe
    for (int w = 32; w < 48; w++) begin apb_op(1'b0, 16'(w*4), 0, 4'hF, rd, err); if (err || rd !== g_rd32(8'(w*4))) bad++; end  // AXI4 -> APB
    for (int w = 48; w < 56; w++) begin pcie_mrd(8'(w), 16'(w*4), rd); if (rd !== g_rd32(8'(w*4))) bad++; end                    // ETH -> PCIe
    for (int w = 56; w < 64; w++) begin apb_op(1'b0, 16'(w*4), 0, 4'hF, rd, err); if (err || rd !== g_rd32(8'(w*4))) bad++; end  // PCIe -> APB
    if (bad > 0) begin
      $display("[CHECK] id=OMNI_B%0d_XMEM_003 | proto=OMNI-HUB | cat=all_dir_256B_xproto | tx=SPI/APB/AXI/ETH/PCIe cross-RD 256B | exp=256/256B_match | got=bad=%0d | status=FAIL", BUF_CYCS, bad); errs++;
    end else begin
      $display("[CHECK] id=OMNI_B%0d_XMEM_003 | proto=OMNI-HUB | cat=all_dir_256B_xproto | tx=SPI/APB/AXI/ETH/PCIe cross-RD 256B | exp=256/256B_match | got=256/256B_match | status=PASS", BUF_CYCS);
    end

    // ==== 4. Ethernet Hot-Plug Cable Disconnect & Reconnect ====
    eth_cable_present = 0;
    repeat (3) @(posedge clk); #1;
    eth_rx_word(8'hC0, 32'hDEAD_9999, 1'b0, 1'b0); // must drop while unplugged!
    axi_wr(16'h0100, 32'h0000_00C0, 4'hF, resp);    // TX must return SLVERR!
    axi_rd(16'h00C0, rd, resp);
    if (eth_link_up || eth_an_done || eth_speed !== 2'b00 || rd !== g_rd32(8'hC0)) begin
      $display("[CHECK] id=OMNI_B%0d_ETH_HP_004 | proto=OMNI-HUB | cat=eth_hotplug_reconnect | tx=unplug->drop->replug->RX | exp=drop+recover | got=link=%0b | status=FAIL", BUF_CYCS, eth_link_up); errs++;
    end else begin
      eth_cable_present = 1;
      while (!eth_link_up) @(posedge clk);
      @(posedge clk); #1;
      eth_rx_word(8'hC0, 32'h7788_99AA, 1'b0, 1'b1);
      axi_rd(16'h00C0, rd, resp);
      if (rd !== 32'h7788_99AA) begin
        $display("[CHECK] id=OMNI_B%0d_ETH_HP_004 | proto=OMNI-HUB | cat=eth_hotplug_reconnect | tx=replug ETH RX->AXI RD | exp=0x778899AA | got=0x%08h | status=FAIL", BUF_CYCS, rd); errs++;
      end else begin
        $display("[CHECK] id=OMNI_B%0d_ETH_HP_004 | proto=OMNI-HUB | cat=eth_hotplug_reconnect | tx=unplug(drop)->replug(1000M)->RX/RD | exp=0x778899AA | got=0x%08h | status=PASS", BUF_CYCS, rd);
      end
    end

    // ==== 5. PCIe Surprise-Removal & Hot-Plug Re-Insert ====
    pcie_hotplug_present = 0;
    repeat (3) @(posedge clk); #1;
    pcie_rx_tvalid = 1; pcie_rx_tdata = {8'h40, 8'hAA, 16'h00E0, 32'h0};
    @(posedge clk); #1; pcie_rx_tvalid = 0;
    if (pcie_link_up || ltssm_state !== 3'd0 || !tlp_reject_nol0) begin
      $display("[CHECK] id=OMNI_B%0d_PCIE_HP_005 | proto=OMNI-HUB | cat=pcie_hotplug_retrain | tx=surprise-remove->reinsert | exp=drop+retrain | got=up=%0b,rej=%0b | status=FAIL", BUF_CYCS, pcie_link_up, tlp_reject_nol0); errs++;
    end else begin
      pcie_hotplug_present = 1;
      while (!pcie_link_up) @(posedge clk);
      @(posedge clk); #1;
      pcie_mrd(8'hBB, 16'h00E0, rd);
      if (rd !== g_rd32(8'hE0)) begin
        $display("[CHECK] id=OMNI_B%0d_PCIE_HP_005 | proto=OMNI-HUB | cat=pcie_hotplug_retrain | tx=reinsert->L0->MRd 0xE0 | exp=0x%08h | got=0x%08h | status=FAIL", BUF_CYCS, g_rd32(8'hE0), rd); errs++;
      end else begin
        $display("[CHECK] id=OMNI_B%0d_PCIE_HP_005 | proto=OMNI-HUB | cat=pcie_hotplug_retrain | tx=remove(rej)->reinsert(L0)->MRd | exp=0x%08h | got=0x%08h | status=PASS", BUF_CYCS, g_rd32(8'hE0), rd);
      end
    end

    // ==== 6. Multi-Domain Sleep Handshake (PCIe L1 + ETH EEE + DDR4 Self-Refresh) & 256B Wake Retention ====
    pm_l1_req = 1; eee_sleep_req = 1; ddr_sr_req = 1;
    repeat (6) @(posedge clk); #1;
    apb_op(1'b0, 16'h0000, 0, 4'hF, rd, err); // rejected during DDR SR!
    if (!pm_l1_ack || !eee_sleep_ack || !ddr_sr_ack || ddr4_cke || !err) begin
      $display("[CHECK] id=OMNI_B%0d_PM_006 | proto=OMNI-HUB | cat=tri_domain_sleep_wake | tx=L1+EEE+DDR_SR sleep | exp=3x_ack=1,cke=0,err=1 | got=l1=%0b,eee=%0b,sr=%0b,cke=%0b | status=FAIL", BUF_CYCS, pm_l1_ack, eee_sleep_ack, ddr_sr_ack, ddr4_cke); errs++;
    end else begin
      pm_l1_req = 0; eee_sleep_req = 0; ddr_sr_req = 0;
      repeat (6) @(posedge clk); #1;
      bad = 0;
      for (int w = 0; w < 64; w++) begin
        axi_rd(16'(w*4), rd, resp);
        if (resp != 0 || rd !== g_rd32(8'(w*4))) bad++;
      end
      if (bad > 0 || !ddr_ready || !pcie_link_up || !eth_link_up) begin
        $display("[CHECK] id=OMNI_B%0d_PM_006 | proto=OMNI-HUB | cat=tri_domain_sleep_wake | tx=wake->full 256B audit | exp=256/256B_match | got=bad=%0d | status=FAIL", BUF_CYCS, bad); errs++;
      end else begin
        $display("[CHECK] id=OMNI_B%0d_PM_006 | proto=OMNI-HUB | cat=tri_domain_sleep_wake | tx=L1+EEE+SR->wake->256B audit | exp=256/256B_match | got=256/256B_match | status=PASS", BUF_CYCS);
      end
    end

    if (errs == 0) begin $display("PASS: soc_omni_bus_hub(BUF=%0d) all 6 full-lifecycle/hotplug/256B checks passed", BUF_CYCS); $finish; end
    else begin $display("FAIL: soc_omni_bus_hub(BUF=%0d) %0d errors", BUF_CYCS, errs); $fatal(1); end
  end
endmodule
