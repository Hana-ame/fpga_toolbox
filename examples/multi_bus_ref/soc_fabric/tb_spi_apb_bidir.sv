`timescale 1ns/1ps
module tb_spi_apb_bidir;
  logic        clk = 0, por_n = 0;
  logic [1:0]  spi_mode = 2'b00;
  logic        pm_sleep_req = 0, pm_sleep_ack, por_ready;
  logic        s_spi_sclk = 0, s_spi_cs_n = 1, s_spi_mosi = 0, s_spi_miso;
  logic        s_apb_psel = 0, s_apb_penable = 0, s_apb_pwrite = 0, s_apb_pready, s_apb_pslverr;
  logic [15:0] s_apb_paddr = 0;
  logic [31:0] s_apb_pwdata = 0, s_apb_prdata;
  logic [3:0]  s_apb_pstrb = 4'hF;
  logic        m_spi_sclk, m_spi_cs_n, m_spi_mosi, m_spi_miso = 0;
  int          errs = 0;

  // External SPI Slave responder on m_spi_* (captures MOSI and echoes 0x3C on MISO)
  logic [15:0] ext_rx = 0;
  logic [7:0]  ext_tx = 8'h3C;
  logic        m_sclk_d = 0;
  always_ff @(posedge clk) begin
    m_sclk_d <= m_spi_sclk;
    if (m_spi_cs_n) begin
      ext_tx <= 8'h3C;
    end else if (m_spi_sclk && !m_sclk_d) begin
      ext_rx <= {ext_rx[14:0], m_spi_mosi};
      m_spi_miso <= ext_tx[7];
      ext_tx <= {ext_tx[6:0], 1'b0};
    end
  end

  spi_apb_bidir dut (.*);
  always #5 clk = ~clk;

  task automatic apb_op(input logic wr, input logic [15:0] addr, input logic [31:0] wdata, output logic [31:0] rdata, output logic err);
    @(posedge clk); #1;
    s_apb_psel = 1; s_apb_penable = 0; s_apb_pwrite = wr; s_apb_paddr = addr; s_apb_pwdata = wdata; s_apb_pstrb = 4'hF;
    @(posedge clk); #1;
    s_apb_penable = 1;
    do begin @(posedge clk); #1; end while (!s_apb_pready);
    rdata = s_apb_prdata; err = s_apb_pslverr;
    s_apb_psel = 0; s_apb_penable = 0;
  endtask

  task automatic spi_send_16(input logic [1:0] mode, input logic [15:0] frame);
    logic cpol, cpha;
    cpol = mode[1]; cpha = mode[0];
    spi_mode = mode;
    s_spi_sclk = cpol;
    @(posedge clk); #1;
    s_spi_cs_n = 0;
    for (int i = 15; i >= 0; i--) begin
      if (cpha == 1'b0) begin
        s_spi_mosi = frame[i];
        repeat (2) @(posedge clk); #1;
        s_spi_sclk = ~cpol; // sample edge for CPHA=0
        repeat (2) @(posedge clk); #1;
        s_spi_sclk = cpol;
      end else begin
        s_spi_sclk = ~cpol; // leading edge shifts
        s_spi_mosi = frame[i];
        repeat (2) @(posedge clk); #1;
        s_spi_sclk = cpol;  // trailing edge samples for CPHA=1
        repeat (2) @(posedge clk); #1;
      end
    end
    @(posedge clk); #1;
    s_spi_cs_n = 1;
    repeat (2) @(posedge clk); #1;
  endtask

  initial begin
    logic [31:0] rd;
    logic        err;
    // 1. Power-On Reset rejection check (immediately after por_n=1, por_ready is still 0!)
    repeat (2) @(posedge clk); #1;
    por_n = 1;
    apb_op(1'b0, 16'h0000, 32'h0, rd, err);
    if (err !== 1'b1) begin
      $display("[CHECK] id=SPI_POR_001 | proto=SPI-APB | cat=por_init_guard | tx=APB RD before por_ready | exp=pslverr=1 | got=pslverr=%0b | status=FAIL", err); errs++;
    end else begin
      $display("[CHECK] id=SPI_POR_001 | proto=SPI-APB | cat=por_init_guard | tx=APB RD before por_ready | exp=pslverr=1 | got=pslverr=%0b | status=PASS", err);
    end

    // Wait for POR init complete
    while (!por_ready) @(posedge clk);
    @(posedge clk); #1;

    // 2. Test ALL 4 SPI Modes (Mode 0, 1, 2, 3) writing 4 bytes at addr 0x10..0x13 via SPI -> read back via APB!
    spi_send_16(2'b00, {1'b1, 7'h10, 8'h11}); // Mode 0
    spi_send_16(2'b01, {1'b1, 7'h11, 8'h22}); // Mode 1
    spi_send_16(2'b10, {1'b1, 7'h12, 8'h33}); // Mode 2
    spi_send_16(2'b11, {1'b1, 7'h13, 8'h44}); // Mode 3
    apb_op(1'b0, 16'h0010, 32'h0, rd, err);
    if (err || rd !== 32'h44332211) begin
      $display("[CHECK] id=SPI_MODE0123_002 | proto=SPI-APB | cat=spi_4modes_to_apb | tx=SPI Mode 0/1/2/3 WR 0x10..0x13 -> APB RD | exp=0x44332211 | got=0x%08h(err=%0b) | status=FAIL", rd, err); errs++;
    end else begin
      $display("[CHECK] id=SPI_MODE0123_002 | proto=SPI-APB | cat=spi_4modes_to_apb | tx=SPI Mode 0/1/2/3 WR 0x10..0x13 -> APB RD | exp=0x44332211 | got=0x%08h(err=%0b) | status=PASS", rd, err);
    end

    // 3. APB -> SPI Master egress test
    spi_mode = 2'b00;
    apb_op(1'b1, 16'h0100, 32'h0000_A55A, rd, err);
    repeat (40) @(posedge clk); #1;
    if (err || ext_rx !== 16'hA55A) begin
      $display("[CHECK] id=APB_TO_SPI_003 | proto=SPI-APB | cat=apb_to_spi_master | tx=APB WR 0x0100=0xA55A -> m_spi MOSI | exp=0xA55A | got=0x%04h | status=FAIL", ext_rx); errs++;
    end else begin
      $display("[CHECK] id=APB_TO_SPI_003 | proto=SPI-APB | cat=apb_to_spi_master | tx=APB WR 0x0100=0xA55A -> m_spi MOSI | exp=0xA55A | got=0x%04h | status=PASS", ext_rx);
    end

    // 4. Low-Power Sleep Handshake + Reject during Sleep + Wake Retention
    pm_sleep_req = 1;
    repeat (3) @(posedge clk); #1;
    apb_op(1'b0, 16'h0010, 32'h0, rd, err);
    if (!pm_sleep_ack || !err) begin
      $display("[CHECK] id=SPI_PM_004 | proto=SPI-APB | cat=sleep_wake_handshake | tx=pm_sleep_req=1 -> APB RD | exp=ack=1,pslverr=1 | got=ack=%0b,pslverr=%0b | status=FAIL", pm_sleep_ack, err); errs++;
    end else begin
      pm_sleep_req = 0;
      repeat (3) @(posedge clk); #1;
      apb_op(1'b0, 16'h0010, 32'h0, rd, err);
      if (pm_sleep_ack || err || rd !== 32'h44332211) begin
        $display("[CHECK] id=SPI_PM_004 | proto=SPI-APB | cat=sleep_wake_handshake | tx=wake -> APB RD retention | exp=0x44332211 | got=0x%08h | status=FAIL", rd); errs++;
      end else begin
        $display("[CHECK] id=SPI_PM_004 | proto=SPI-APB | cat=sleep_wake_handshake | tx=sleep(ack=1,reject)->wake->RD | exp=0x44332211 | got=0x%08h | status=PASS", rd);
      end
    end

    if (errs == 0) begin $display("PASS: spi_apb_bidir all 4 POR/Mode0-3/bidir/sleep checks passed"); $finish; end
    else begin $display("FAIL: spi_apb_bidir %0d errors", errs); $fatal(1); end
  end
endmodule
