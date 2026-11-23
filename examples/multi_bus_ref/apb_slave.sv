`timescale 1ns/1ps
module apb_slave #(
  parameter int AW = 16,
  parameter int DW = 32
) (
  input  logic          pclk,
  input  logic          presetn,
  input  logic          psel,
  input  logic          penable,
  input  logic          pwrite,
  input  logic [AW-1:0] paddr,
  input  logic [DW-1:0] pwdata,
  input  logic [3:0]    pstrb,
  output logic          pready,
  output logic [DW-1:0] prdata,
  output logic          pslverr
);
  logic [DW-1:0] regs [0:3];
  logic          wait_done;

  always_ff @(posedge pclk) begin
    if (!presetn) begin
      regs[0]   <= 32'h12345678; // RW
      regs[1]   <= 32'hDEAD0001; // RO
      regs[2]   <= 32'h000000FF; // W1C
      regs[3]   <= 32'h00000000; // RW
      wait_done <= 1'b0;
      pready    <= 1'b0;
      prdata    <= '0;
      pslverr   <= 1'b0;
    end else begin
      pready  <= 1'b0;
      pslverr <= 1'b0;
      if (psel && penable && !wait_done) begin
        // Insert 1 wait state before asserting pready
        wait_done <= 1'b1;
        pready    <= 1'b1;
        if (paddr[15:4] != 12'h000 || paddr[1:0] != 2'b00) begin
          pslverr <= 1'b1;
          prdata  <= 32'h0;
        end else if (pwrite) begin
          case (paddr[3:2])
            2'd0: for (int b = 0; b < 4; b++) if (pstrb[b]) regs[0][b*8 +: 8] <= pwdata[b*8 +: 8];
            2'd1: ; // RO ignored
            2'd2: regs[2] <= regs[2] & ~pwdata; // W1C
            2'd3: for (int b = 0; b < 4; b++) if (pstrb[b]) regs[3][b*8 +: 8] <= pwdata[b*8 +: 8];
          endcase
        end else begin
          prdata <= regs[paddr[3:2]];
        end
      end else begin
        wait_done <= 1'b0;
      end
    end
  end
endmodule
