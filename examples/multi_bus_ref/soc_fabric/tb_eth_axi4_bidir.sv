`timescale 1ns/1ps
module tb_eth_axi4_bidir;
  logic        clk = 0, por_n = 0, an_start = 0, eee_sleep_req = 0;
  logic [1:0]  peer_cap = 2'b00, eth_speed;
  logic        eth_an_done, eth_an_fail, eth_link_up, eee_sleep_ack;
  logic        eth_rx_dv = 0, eth_tx_en, rx_crc_err, rx_drop_nolink;
  logic [7:0]  eth_rx_data = 0, eth_tx_data;
  logic [3:0]  s_axi_awid = 0, s_axi_bid, s_axi_arid = 0, s_axi_rid;
  logic [15:0] s_axi_awaddr = 0, s_axi_araddr = 0;
  logic [7:0]  s_axi_awlen = 0, s_axi_arlen = 0;
  logic [2:0]  s_axi_awsize = 3'd2, s_axi_arsize = 3'd2;
  logic [1:0]  s_axi_awburst = 2'b01, s_axi_arburst = 2'b01, s_axi_bresp, s_axi_rresp;
  logic        s_axi_awvalid = 0, s_axi_awready, s_axi_wvalid = 0, s_axi_wready, s_axi_wlast = 0;
  logic        s_axi_bvalid, s_axi_bready = 1, s_axi_arvalid = 0, s_axi_arready, s_axi_rvalid, s_axi_rready = 1, s_axi_rlast;
  logic [31:0] s_axi_wdata = 0, s_axi_rdata;
  logic [3:0]  s_axi_wstrb = 4'hF;
  int          errs = 0;

  eth_axi4_bidir dut (.*);
  always #5 clk = ~clk;

  task automatic axi_wr(input logic [15:0] addr, input logic [31:0] data, output logic [1:0] resp);
    @(posedge clk); #1;
    s_axi_awaddr = addr; s_axi_awvalid = 1;
    while (!s_axi_awready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_awvalid = 0;
    s_axi_wdata = data; s_axi_wstrb = 4'hF; s_axi_wlast = 1; s_axi_wvalid = 1;
    while (!s_axi_wready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_wvalid = 0; s_axi_wlast = 0;
    while (!(s_axi_bvalid && s_axi_bready)) begin @(posedge clk); #1; end
    resp = s_axi_bresp;
    @(posedge clk); #1;
  endtask

  task automatic axi_rd(input logic [15:0] addr, output logic [31:0] data, output logic [1:0] resp);
    @(posedge clk); #1;
    s_axi_araddr = addr; s_axi_arvalid = 1;
    while (!s_axi_arready) begin @(posedge clk); #1; end
    @(posedge clk); #1; s_axi_arvalid = 0;
    while (!(s_axi_rvalid && s_axi_rready)) begin @(posedge clk); #1; end
    data = s_axi_rdata; resp = s_axi_rresp;
    @(posedge clk); #1;
  endtask

  task automatic send_eth_rx(input logic [7:0] addr, input logic [31:0] payload, input logic bad_crc);
    logic [7:0] f [0:6];
    f[0] = 8'hD5; f[1] = addr;
    f[2] = payload[7:0]; f[3] = payload[15:8]; f[4] = payload[23:16]; f[5] = payload[31:24];
    f[6] = f[0] ^ f[1] ^ f[2] ^ f[3] ^ f[4] ^ f[5] ^ (bad_crc ? 8'hFF : 8'h00);
    for (int i = 0; i < 7; i++) begin
      eth_rx_dv = 1; eth_rx_data = f[i];
      @(posedge clk); #1;
    end
    eth_rx_dv = 0;
    @(posedge clk); #1;
  endtask

  initial begin
    logic [31:0] rd;
    logic [1:0]  resp;
    logic [7:0]  tx_cap [0:6];
    int          tx_n;
    repeat (2) @(posedge clk); #1;
    por_n = 1;

    // 1. Auto-Negotiation FAILURE case (peer_cap=00) -> link down, RX dropped (MEM untouched) & TX rejected with SLVERR!
    peer_cap = 2'b00; an_start = 1; @(posedge clk); #1; an_start = 0;
    repeat (5) @(posedge clk); #1;
    send_eth_rx(8'h20, 32'hBAD0_DEAD, 1'b0); // MUST be dropped because link is down!
    axi_rd(16'h0020, rd, resp);
    axi_wr(16'h0100, 32'h0000_0020, resp);
    if (!eth_an_fail || eth_link_up || resp !== 2'b10 || rd !== 32'h0000_0000) begin
      $display("[CHECK] id=ETH_AN_ERR_001 | proto=ETH-AXI4 | cat=an_negotiation_fail | tx=AN peer_cap=00 -> TX | exp=an_fail=1,bresp=SLVERR(2) | got=an_fail=%0b,bresp=%0d | status=FAIL", eth_an_fail, resp); errs++;
    end else begin
      $display("[CHECK] id=ETH_AN_ERR_001 | proto=ETH-AXI4 | cat=an_negotiation_fail | tx=AN peer_cap=00 -> TX | exp=an_fail=1,bresp=SLVERR(2) | got=an_fail=%0b,bresp=%0d | status=PASS", eth_an_fail, resp);
    end

    // 2. Auto-Negotiation 100M downgrade (peer_cap=10) then 1000M full speed (peer_cap=11)
    peer_cap = 2'b10; an_start = 1; @(posedge clk); #1; an_start = 0;
    repeat (5) @(posedge clk); #1;
    if (!eth_link_up || eth_speed !== 2'b01) begin
      $display("[CHECK] id=ETH_AN_100M_002 | proto=ETH-AXI4 | cat=an_100m_downgrade | tx=AN peer_cap=10 | exp=link=1,spd=100M(1) | got=link=%0b,spd=%0d | status=FAIL", eth_link_up, eth_speed); errs++;
    end else begin
      $display("[CHECK] id=ETH_AN_100M_002 | proto=ETH-AXI4 | cat=an_100m_downgrade | tx=AN peer_cap=10 | exp=link=1,spd=100M(1) | got=link=%0b,spd=%0d | status=PASS", eth_link_up, eth_speed);
    end

    peer_cap = 2'b11; an_start = 1; @(posedge clk); #1; an_start = 0;
    repeat (5) @(posedge clk); #1;
    if (!eth_link_up || eth_speed !== 2'b10) begin
      $display("[CHECK] id=ETH_AN_1000M_003 | proto=ETH-AXI4 | cat=an_1000m_up | tx=AN peer_cap=11 | exp=link=1,spd=1000M(2) | got=link=%0b,spd=%0d | status=FAIL", eth_link_up, eth_speed); errs++;
    end else begin
      $display("[CHECK] id=ETH_AN_1000M_003 | proto=ETH-AXI4 | cat=an_1000m_up | tx=AN peer_cap=11 | exp=link=1,spd=1000M(2) | got=link=%0b,spd=%0d | status=PASS", eth_link_up, eth_speed);
    end

    // 3. Ethernet RX -> AXI4 shared MEM + Bad CRC rejection
    send_eth_rx(8'h20, 32'h11223344, 1'b0); // valid frame -> writes MEM[0x20]
    send_eth_rx(8'h20, 32'hBAD0BAD0, 1'b1); // bad CRC -> MUST NOT overwrite MEM[0x20]!
    axi_rd(16'h0020, rd, resp);
    if (rd !== 32'h11223344) begin
      $display("[CHECK] id=ETH_TO_AXI_004 | proto=ETH-AXI4 | cat=eth_rx_crc_to_axi_mem | tx=ETH RX(valid+badCRC) -> AXI RD 0x20 | exp=0x11223344 | got=0x%08h | status=FAIL", rd); errs++;
    end else begin
      $display("[CHECK] id=ETH_TO_AXI_004 | proto=ETH-AXI4 | cat=eth_rx_crc_to_axi_mem | tx=ETH RX(valid+badCRC) -> AXI RD 0x20 | exp=0x11223344 | got=0x%08h | status=PASS", rd);
    end

    // 4. AXI4 -> Ethernet TX frame emission from MEM[0x30]
    axi_wr(16'h0030, 32'hDEADBEEF, resp);
    axi_wr(16'h0100, 32'h0000_0030, resp);
    tx_n = 0;
    for (int t = 0; t < 12; t++) begin
      if (eth_tx_en && tx_n < 7) begin tx_cap[tx_n] = eth_tx_data; tx_n++; end
      @(posedge clk); #1;
    end
    if (tx_n !== 7 || tx_cap[0] !== 8'hD5 || tx_cap[1] !== 8'h30 || {tx_cap[5], tx_cap[4], tx_cap[3], tx_cap[2]} !== 32'hDEADBEEF) begin
      $display("[CHECK] id=AXI_TO_ETH_005 | proto=ETH-AXI4 | cat=axi_mem_to_eth_tx | tx=AXI WR 0x30 -> ETH TX frame | exp=0xDEADBEEF(7B) | got=0x%08h(%0dB) | status=FAIL", {tx_cap[5], tx_cap[4], tx_cap[3], tx_cap[2]}, tx_n); errs++;
    end else begin
      $display("[CHECK] id=AXI_TO_ETH_005 | proto=ETH-AXI4 | cat=axi_mem_to_eth_tx | tx=AXI WR 0x30 -> ETH TX frame | exp=0xDEADBEEF(7B) | got=0x%08h(%0dB) | status=PASS", {tx_cap[5], tx_cap[4], tx_cap[3], tx_cap[2]}, tx_n);
    end

    // 5. EEE Low-Power Sleep Handshake + TX reject during sleep + Wake Retention
    eee_sleep_req = 1;
    repeat (3) @(posedge clk); #1;
    axi_wr(16'h0100, 32'h0000_0030, resp);
    if (!eee_sleep_ack || resp !== 2'b10) begin
      $display("[CHECK] id=ETH_EEE_006 | proto=ETH-AXI4 | cat=eee_sleep_wake | tx=eee_sleep_req=1 -> TX | exp=ack=1,SLVERR(2) | got=ack=%0b,bresp=%0d | status=FAIL", eee_sleep_ack, resp); errs++;
    end else begin
      eee_sleep_req = 0;
      repeat (3) @(posedge clk); #1;
      axi_rd(16'h0030, rd, resp);
      if (eee_sleep_ack || !eth_link_up || rd !== 32'hDEADBEEF) begin
        $display("[CHECK] id=ETH_EEE_006 | proto=ETH-AXI4 | cat=eee_sleep_wake | tx=wake -> AXI RD retention | exp=0xDEADBEEF | got=0x%08h | status=FAIL", rd); errs++;
      end else begin
        $display("[CHECK] id=ETH_EEE_006 | proto=ETH-AXI4 | cat=eee_sleep_wake | tx=EEE sleep(reject)->wake->RD | exp=0xDEADBEEF | got=0x%08h | status=PASS", rd);
      end
    end

    if (errs == 0) begin $display("PASS: eth_axi4_bidir all 6 AN/CRC/bidir/EEE checks passed"); $finish; end
    else begin $display("FAIL: eth_axi4_bidir %0d errors", errs); $fatal(1); end
  end
endmodule
