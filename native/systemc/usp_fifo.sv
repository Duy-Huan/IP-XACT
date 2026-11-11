// SPDX-License-Identifier: Apache-2.0
// Wrapper only. The FIFO implementation is pinned OpenTitan source, not copied here.
module usp_fifo (
  input logic clk_i, rst_ni, wvalid_i, rready_i,
  input logic [31:0] wdata_i,
  output logic wready_o, rvalid_o,
  output logic [31:0] rdata_o
);
  logic full;
  logic [2:0] depth;
  prim_fifo_sync #(.Width(32), .Depth(4), .Pass(1'b0)) fifo (
    .clk_i, .rst_ni, .clr_i(1'b0), .wvalid_i, .wready_o, .wdata_i,
    .rvalid_o, .rready_i, .rdata_o, .full_o(full), .depth_o(depth)
  );
endmodule
