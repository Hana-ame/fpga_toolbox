`timescale 1ns/1ps
module ahb_lite_slave #(
  parameter int AW = 16,
  parameter int DW = 32
) (
  input  logic          HCLK,
  input  logic          HRESETn,
  input  logic          HSEL,
  input  logic [AW-1:0] HADDR,
  input  logic [1:0]    HTRANS,
  input  logic          HWRITE,
  input  logic [2:0]    HSIZE,
  input  logic [DW-1:0] HWDATA,
  output logic          HREADYOUT,
  output logic [DW-1:0] HRDATA,
  output logic          HRESP
);
  logic [DW-1:0] regs [0:3];
  logic          req_valid;
  logic          req_write;
  logic [AW-1:0] req_addr;
  logic          req_err;
  logic          wait_phase;
  logic [2:0]    _unused_hsize;
  assign _unused_hsize = HSIZE;

  always_ff @(posedge HCLK) begin
    if (!HRESETn) begin
      regs[0]    <= 32'h11223344; // RW
      regs[1]    <= 32'hCAFEBABE; // RO
      regs[2]    <= 32'h00000000; // RW
      regs[3]    <= 32'h00000000; // RW
      req_valid  <= 1'b0;
      req_write  <= 1'b0;
      req_addr   <= '0;
      req_err    <= 1'b0;
      wait_phase <= 1'b0;
      HREADYOUT  <= 1'b1;
      HRDATA     <= '0;
      HRESP      <= 1'b0;
    end else begin
      if (wait_phase) begin
        // Complete pipelined data phase
        wait_phase <= 1'b0;
        HREADYOUT  <= 1'b1;
        if (req_err) begin
          HRESP  <= 1'b1;
          HRDATA <= '0;
        end else begin
          HRESP <= 1'b0;
          if (req_write) begin
            if (req_addr[3:2] != 2'd1) // reg[1] is RO
              regs[req_addr[3:2]] <= HWDATA;
          end else begin
            HRDATA <= regs[req_addr[3:2]];
          end
        end
        req_valid <= 1'b0;
      end else if (HSEL && HTRANS[1]) begin
        // Capture address phase, insert 1 wait state
        req_valid  <= 1'b1;
        req_write  <= HWRITE;
        req_addr   <= HADDR;
        req_err    <= (HADDR >= 16'h0010);
        wait_phase <= 1'b1;
        HREADYOUT  <= 1'b0;
        HRESP      <= (HADDR >= 16'h0010);
      end else begin
        HREADYOUT <= 1'b1;
        HRESP     <= 1'b0;
      end
    end
  end
endmodule
