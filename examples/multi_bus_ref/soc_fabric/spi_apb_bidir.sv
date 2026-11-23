`timescale 1ns/1ps
module spi_apb_bidir (
  input  logic        clk,
  input  logic        por_n,          // Power-On Reset (active low)
  // Mode & Power-Management Handshake
  input  logic [1:0]  spi_mode,       // {CPOL, CPHA} = 00, 01, 10, 11
  input  logic        pm_sleep_req,   // Low-power sleep request
  output logic        pm_sleep_ack,   // Sleep handshake acknowledge
  output logic        por_ready,      // Power-on initialization complete
  // Interface 1: SPI Slave (SPI -> APB path, 16-bit frame: [15]=WR, [14:8]=addr, [7:0]=data)
  input  logic        s_spi_sclk,
  input  logic        s_spi_cs_n,
  input  logic        s_spi_mosi,
  output logic        s_spi_miso,
  // Interface 2: APB Slave (APB -> SPI Master & shared MEM path)
  input  logic        s_apb_psel,
  input  logic        s_apb_penable,
  input  logic        s_apb_pwrite,
  input  logic [15:0] s_apb_paddr,
  input  logic [31:0] s_apb_pwdata,
  input  logic [3:0]  s_apb_pstrb,
  output logic        s_apb_pready,
  output logic [31:0] s_apb_prdata,
  output logic        s_apb_pslverr,
  // Interface 3: SPI Master (APB -> SPI egress to external SPI device)
  output logic        m_spi_sclk,
  output logic        m_spi_cs_n,
  output logic        m_spi_mosi,
  input  logic        m_spi_miso
);
  // Built-in 256-byte shared backing MEM (accessible by BOTH SPI->APB and APB->MEM!)
  logic [7:0] mem [0:255];

  // Power-On Reset init counter (takes 4 cycles after por_n=1 before por_ready=1)
  logic [2:0] por_cnt;
  assign por_ready = (por_cnt == 3'd4);

  // Power-management sleep state (drains active SPI/APB before asserting pm_sleep_ack)
  logic spi_busy;
  logic apb_spi_busy;

  // SPI Slave edge detector supporting ALL 4 CPOL/CPHA modes!
  wire cpol = spi_mode[1];
  wire cpha = spi_mode[0];
  logic sclk_d, cs_d;
  wire sclk_rise = s_spi_sclk && !sclk_d;
  wire sclk_fall = !s_spi_sclk && sclk_d;
  // Mode 0(00): sample rise; Mode 1(01): sample fall; Mode 2(10): sample fall; Mode 3(11): sample rise
  wire sample_edge = (cpol ^ cpha) ? sclk_fall : sclk_rise;

  logic [15:0] rx_shift;
  logic [4:0]  bit_cnt;
  logic [7:0]  tx_byte;

  // APB -> SPI Master serializer (16-bit frame out on m_spi_*)
  logic [15:0] m_tx_shift, m_rx_shift;
  logic [5:0]  m_step;
  logic        apb_wait;
  logic [31:0] last_m_spi_rx;

  assign spi_busy = !s_spi_cs_n;
  assign s_spi_miso = tx_byte[7];

  always_ff @(posedge clk) begin
    if (!por_n) begin
      for (int i = 0; i < 256; i++) mem[i] <= 8'h00;
      por_cnt <= 3'd0;
      pm_sleep_ack <= 1'b0;
      sclk_d <= 1'b0; cs_d <= 1'b1;
      rx_shift <= 16'h0; bit_cnt <= 5'd0; tx_byte <= 8'h00;
      s_apb_pready <= 1'b0; s_apb_prdata <= 32'h0; s_apb_pslverr <= 1'b0;
      apb_wait <= 1'b0; apb_spi_busy <= 1'b0;
      m_spi_sclk <= 1'b0; m_spi_cs_n <= 1'b1; m_spi_mosi <= 1'b0;
      m_tx_shift <= 16'h0; m_rx_shift <= 16'h0; m_step <= 6'd0;
      last_m_spi_rx <= 32'h0;
    end else begin
      if (por_cnt < 3'd4) por_cnt <= por_cnt + 3'd1;

      // Sleep handshake: enter sleep only when POR done & no active transactions
      if (pm_sleep_req && por_ready && !spi_busy && !apb_spi_busy)
        pm_sleep_ack <= 1'b1;
      else if (!pm_sleep_req)
        pm_sleep_ack <= 1'b0;

      sclk_d <= s_spi_sclk;
      cs_d   <= s_spi_cs_n;

      // ---- Path 1: SPI Slave -> Built-in MEM (via APB-equivalent byte write/read) ----
      if (!por_ready || pm_sleep_ack) begin
        bit_cnt <= 5'd0;
      end else if (s_spi_cs_n) begin
        bit_cnt <= 5'd0;
      end else if (sample_edge) begin
        rx_shift <= {rx_shift[14:0], s_spi_mosi};
        if (bit_cnt == 5'd7) begin
          // After receiving [15:8] = {WR, addr[6:0]}, preload read byte for MISO shift-out!
          if (!rx_shift[6]) // MSB (bit 15) is now at rx_shift[6] before this shift
            tx_byte <= mem[{1'b0, rx_shift[5:0], s_spi_mosi}];
          bit_cnt <= bit_cnt + 5'd1;
        end else if (bit_cnt == 5'd15) begin
          logic [15:0] frame;
          frame = {rx_shift[14:0], s_spi_mosi};
          if (frame[15]) begin
            // Write byte to shared MEM at addr=frame[14:8]
            mem[{1'b0, frame[14:8]}] <= frame[7:0];
          end
          bit_cnt <= 5'd0;
        end else begin
          if (bit_cnt > 5'd7) tx_byte <= {tx_byte[6:0], 1'b0};
          bit_cnt <= bit_cnt + 5'd1;
        end
      end

      // ---- Path 2: APB Slave -> Built-in MEM (0x0000..0x00FF) or APB -> SPI Master (0x0100) ----
      s_apb_pready  <= 1'b0;
      s_apb_pslverr <= 1'b0;
      if (s_apb_psel && s_apb_penable && !apb_wait) begin
        if (!por_ready || pm_sleep_ack) begin
          // Reject APB transactions during POR init or Sleep!
          apb_wait      <= 1'b1;
          s_apb_pready  <= 1'b1;
          s_apb_pslverr <= 1'b1;
          s_apb_prdata  <= 32'h0;
        end else if (s_apb_paddr < 16'h0100) begin
          apb_wait     <= 1'b1;
          s_apb_pready <= 1'b1;
          if (s_apb_pwrite) begin
            for (int b = 0; b < 4; b++)
              if (s_apb_pstrb[b]) mem[s_apb_paddr[7:0] + 8'(b)] <= s_apb_pwdata[b*8 +: 8];
          end else begin
            s_apb_prdata <= {
              mem[s_apb_paddr[7:0] + 8'd3],
              mem[s_apb_paddr[7:0] + 8'd2],
              mem[s_apb_paddr[7:0] + 8'd1],
              mem[s_apb_paddr[7:0] + 8'd0]
            };
          end
        end else if (s_apb_paddr == 16'h0100 && s_apb_pwrite && !apb_spi_busy) begin
          // Trigger APB -> SPI Master 16-bit transfer
          apb_wait     <= 1'b1;
          s_apb_pready <= 1'b1;
          apb_spi_busy <= 1'b1;
          m_spi_cs_n   <= 1'b0;
          m_spi_sclk   <= cpol;
          m_tx_shift   <= s_apb_pwdata[15:0];
          m_spi_mosi   <= s_apb_pwdata[15];
          m_step       <= 6'd0;
        end else if (s_apb_paddr == 16'h0104 && !s_apb_pwrite) begin
          apb_wait     <= 1'b1;
          s_apb_pready <= 1'b1;
          s_apb_prdata <= {31'd0, apb_spi_busy} | (last_m_spi_rx & 32'hFFFF_FF00);
        end else begin
          apb_wait      <= 1'b1;
          s_apb_pready  <= 1'b1;
          s_apb_pslverr <= 1'b1;
        end
      end else begin
        apb_wait <= 1'b0;
      end

      // SPI Master state machine (APB -> SPI egress)
      if (apb_spi_busy) begin
        m_step <= m_step + 6'd1;
        if (m_step[0] == 1'b0) begin
          m_spi_sclk <= ~cpol;
          m_rx_shift <= {m_rx_shift[14:0], m_spi_miso};
        end else begin
          m_spi_sclk <= cpol;
          m_tx_shift <= {m_tx_shift[14:0], 1'b0};
          m_spi_mosi <= m_tx_shift[14];
          if (m_step == 6'd31) begin
            apb_spi_busy  <= 1'b0;
            m_spi_cs_n    <= 1'b1;
            last_m_spi_rx <= {16'h0, m_rx_shift[7:0], 8'h00};
          end
        end
      end
    end
  end
endmodule
