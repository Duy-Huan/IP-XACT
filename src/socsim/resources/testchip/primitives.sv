// SPDX-License-Identifier: Apache-2.0
// Technology wrappers; behavior remains in pinned OpenTitan generic primitives.
module prim_buf #(parameter int Width=1)(input [Width-1:0] in_i, output [Width-1:0] out_o);
  prim_generic_buf #(.Width(Width)) impl (.*);
endmodule
module prim_flop #(parameter int Width=1, parameter logic [Width-1:0] ResetValue='0)
 (input clk_i, rst_ni, input [Width-1:0] d_i, output [Width-1:0] q_o);
  prim_generic_flop #(.Width(Width), .ResetValue(ResetValue)) impl (.*);
endmodule
module prim_flop_2sync #(parameter int Width=1, parameter logic [Width-1:0] ResetValue='0)
 (input clk_i, rst_ni, input [Width-1:0] d_i, output [Width-1:0] q_o);
  prim_generic_flop_2sync #(.Width(Width), .ResetValue(ResetValue)) impl (.*);
endmodule
module prim_clock_gating(input clk_i, en_i, test_en_i, output clk_o);
  prim_generic_clock_gating impl (.*);
endmodule
