`timescale 1ns/1ps
module eth_axi4_bidir (
  input  logic        clk,
  input  logic        por_n,
  // Auto-Negotiation & EEE Low-Power Handshake
  input  logic        an_start,
  input  logic [1:0]  peer_cap,        // 2'b11=1000M, 2'b10=100M, 2'b00=NO_LINK(AN_FAIL!)
  output logic        eth_an_done,
  output logic        eth_an_fail,
  output logic        eth_link_up,
  output logic [1:0]  eth_speed,       // 2'b10=1000M, 2'b01=100M, 2'b00=DOWN
  input  logic        eee_sleep_req,
  output logic        eee_sleep_ack,
  // Interface 1: Ethernet Byte-Stream RX/TX (GMII-like)
  input  logic        eth_rx_dv,
  input  logic [7:0]  eth_rx_data,
  output logic        eth_tx_en,
  output logic [7:0]  eth_tx_data,
  output logic        rx_crc_err,
  output logic        rx_drop_nolink,
  // Interface 2: AXI4 Slave (to read/write built-in MEM & trigger TX frame)
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
  input  logic        s_axi_rready
);
  // Built-in 256-byte shared DMA MEM
  logic [7:0] mem [0:255];

  // Auto-Negotiation FSM: AN_IDLE -> AN_TRAIN (3 cycles) -> AN_UP or AN_FAIL
  typedef enum logic [1:0] { AN_IDLE, AN_TRAIN, AN_UP, AN_FAIL } an_t;
  an_t        an_st;
  logic [2:0] an_cnt;

  // RX Frame parser: [0]=0x55(SFD), [1]=dst_addr, [2]=len(4), [3..6]=4B payload, [7]=XOR_CRC8
  logic [2:0] rx_idx;
  logic [7:0] rx_addr;
  logic [7:0] rx_buf [0:3];
  logic [7:0] rx_xor;

  // TX Frame generator (triggered by AXI4 write to 0x0100 with wdata[7:0]=src_addr)
  logic       tx_active;
  logic [2:0] tx_idx;
  logic [7:0] tx_addr;
  logic [7:0] tx_xor;

  // AXI4 slave state
  logic       wr_act;
  logic [3:0] wr_id;
  logic [15:0] wr_addr;
  logic       _u;
  assign _u = (^s_axi_awlen) ^ (^s_axi_awsize) ^ (^s_axi_awburst) ^ (^s_axi_arlen) ^ (^s_axi_arsize) ^ (^s_axi_arburst) ^ s_axi_wlast;

  assign eth_an_done = (an_st == AN_UP || an_st == AN_FAIL);
  assign eth_an_fail = (an_st == AN_FAIL);
  assign eth_link_up = (an_st == AN_UP) && !eee_sleep_ack;

  assign s_axi_awready = !wr_act && !s_axi_bvalid;
  assign s_axi_wready  = wr_act;
  assign s_axi_arready = !s_axi_rvalid;

  always_ff @(posedge clk) begin
    if (!por_n) begin
      for (int i = 0; i < 256; i++) mem[i] <= 8'h00;
      an_st <= AN_IDLE; an_cnt <= 3'd0; eth_speed <= 2'b00;
      eee_sleep_ack <= 1'b0;
      rx_idx <= 3'd0; rx_addr <= 8'h0; rx_xor <= 8'h0;
      rx_crc_err <= 1'b0; rx_drop_nolink <= 1'b0;
      tx_active <= 1'b0; tx_idx <= 3'd0; tx_addr <= 8'h0; tx_xor <= 8'h0;
      eth_tx_en <= 1'b0; eth_tx_data <= 8'h0;
      wr_act <= 1'b0; wr_id <= '0; wr_addr <= '0;
      s_axi_bvalid <= 1'b0; s_axi_bresp <= 2'b00; s_axi_bid <= '0;
      s_axi_rvalid <= 1'b0; s_axi_rresp <= 2'b00; s_axi_rid <= '0;
      s_axi_rdata <= 32'h0; s_axi_rlast <= 1'b0;
    end else begin
      // Auto-Negotiation FSM
      case (an_st)
        AN_IDLE: if (an_start) begin an_st <= AN_TRAIN; an_cnt <= 3'd0; end
        AN_TRAIN: begin
          if (an_cnt == 3'd2) begin
            if (peer_cap == 2'b11) begin an_st <= AN_UP; eth_speed <= 2'b10; end      // 1000M
            else if (peer_cap == 2'b10) begin an_st <= AN_UP; eth_speed <= 2'b01; end // 100M
            else begin an_st <= AN_FAIL; eth_speed <= 2'b00; end                      // AN_FAIL!
          end else begin
            an_cnt <= an_cnt + 3'd1;
          end
        end
        AN_UP, AN_FAIL: if (an_start) begin an_st <= AN_TRAIN; an_cnt <= 3'd0; end
      endcase

      // EEE Sleep Handshake (only when link up and no active TX/RX)
      if (eee_sleep_req && an_st == AN_UP && !tx_active && !eth_rx_dv)
        eee_sleep_ack <= 1'b1;
      else if (!eee_sleep_req)
        eee_sleep_ack <= 1'b0;

      // ---- Path 1: Ethernet RX -> CRC Check -> DMA Write into Built-in MEM ----
      rx_crc_err     <= 1'b0;
      rx_drop_nolink <= 1'b0;
      if (eth_rx_dv) begin
        if (!eth_link_up) begin
          rx_drop_nolink <= 1'b1; // Must drop RX frame if link down or in EEE sleep!
          rx_idx <= 3'd0;
        end else begin
          case (rx_idx)
            3'd0: if (eth_rx_data == 8'hD5) begin rx_idx <= 3'd1; rx_xor <= 8'hD5; end
            3'd1: begin rx_addr <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 3'd2; end
            3'd2: begin rx_buf[0] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 3'd3; end
            3'd3: begin rx_buf[1] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 3'd4; end
            3'd4: begin rx_buf[2] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 3'd5; end
            3'd5: begin rx_buf[3] <= eth_rx_data; rx_xor <= rx_xor ^ eth_rx_data; rx_idx <= 3'd6; end
            3'd6: begin
              if (eth_rx_data == rx_xor) begin
                mem[rx_addr + 8'd0] <= rx_buf[0];
                mem[rx_addr + 8'd1] <= rx_buf[1];
                mem[rx_addr + 8'd2] <= rx_buf[2];
                mem[rx_addr + 8'd3] <= rx_buf[3];
              end else begin
                rx_crc_err <= 1'b1; // Bad checksum -> drop without corrupting MEM!
              end
              rx_idx <= 3'd0;
            end
            default: rx_idx <= 3'd0;
          endcase
        end
      end else begin
        rx_idx <= 3'd0;
      end

      // ---- Path 2: AXI4 Slave -> Built-in MEM + Trigger Ethernet TX ----
      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
      if (s_axi_awvalid && s_axi_awready) begin
        wr_act <= 1'b1; wr_id <= s_axi_awid; wr_addr <= s_axi_awaddr;
      end else if (wr_act && s_axi_wvalid && s_axi_wready) begin
        wr_act <= 1'b0; s_axi_bvalid <= 1'b1; s_axi_bid <= wr_id;
        if (wr_addr < 16'h0100) begin
          for (int b = 0; b < 4; b++)
            if (s_axi_wstrb[b]) mem[wr_addr[7:0] + 8'(b)] <= s_axi_wdata[b*8 +: 8];
          s_axi_bresp <= 2'b00;
        end else if (wr_addr == 16'h0100) begin
          // Trigger AXI4 -> Ethernet TX from MEM[wdata[7:0]]
          if (!eth_link_up) begin
            s_axi_bresp <= 2'b10; // SLVERR: cannot TX when AN failed / link down / EEE sleep!
          end else begin
            tx_active <= 1'b1; tx_idx <= 3'd0; tx_addr <= s_axi_wdata[7:0]; tx_xor <= 8'h00;
            s_axi_bresp <= 2'b00;
          end
        end else begin
          s_axi_bresp <= 2'b10;
        end
      end

      if (s_axi_rvalid && s_axi_rready) begin s_axi_rvalid <= 1'b0; s_axi_rlast <= 1'b0; end
      if (s_axi_arvalid && s_axi_arready) begin
        s_axi_rvalid <= 1'b1; s_axi_rid <= s_axi_arid; s_axi_rlast <= 1'b1;
        if (s_axi_araddr < 16'h0100) begin
          s_axi_rdata <= {
            mem[s_axi_araddr[7:0] + 8'd3], mem[s_axi_araddr[7:0] + 8'd2],
            mem[s_axi_araddr[7:0] + 8'd1], mem[s_axi_araddr[7:0] + 8'd0]
          };
          s_axi_rresp <= 2'b00;
        end else begin
          s_axi_rdata <= 32'h0; s_axi_rresp <= 2'b10;
        end
      end

      // Ethernet TX serializer (reads 4B from MEM and emits SFD+addr+4B+XOR_CRC8)
      eth_tx_en <= 1'b0;
      if (tx_active) begin
        eth_tx_en <= 1'b1;
        case (tx_idx)
          3'd0: begin eth_tx_data <= 8'hD5; tx_xor <= 8'hD5; tx_idx <= 3'd1; end
          3'd1: begin eth_tx_data <= tx_addr; tx_xor <= tx_xor ^ tx_addr; tx_idx <= 3'd2; end
          3'd2: begin eth_tx_data <= mem[tx_addr + 8'd0]; tx_xor <= tx_xor ^ mem[tx_addr + 8'd0]; tx_idx <= 3'd3; end
          3'd3: begin eth_tx_data <= mem[tx_addr + 8'd1]; tx_xor <= tx_xor ^ mem[tx_addr + 8'd1]; tx_idx <= 3'd4; end
          3'd4: begin eth_tx_data <= mem[tx_addr + 8'd2]; tx_xor <= tx_xor ^ mem[tx_addr + 8'd2]; tx_idx <= 3'd5; end
          3'd5: begin eth_tx_data <= mem[tx_addr + 8'd3]; tx_xor <= tx_xor ^ mem[tx_addr + 8'd3]; tx_idx <= 3'd6; end
          3'd6: begin eth_tx_data <= tx_xor; tx_active <= 1'b0; tx_idx <= 3'd0; end
          default: tx_active <= 1'b0;
        endcase
      end
    end
  end
endmodule
