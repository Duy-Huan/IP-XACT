// SPDX-License-Identifier: Apache-2.0
// Zero-delay functional models for the eight clock-gate cells whose Liberty
// statetables are not imported by Yosys 0.52. Follow the pinned Nangate45 table:
// sample E (or E|SE) while CK is low, hold while high, GCK = CK & held enable.
`define USP_CLOCK_CELL(N) \
module N(input CK, E, output GCK); \
  reg enabled; always @* if (!CK) enabled = E; \
  assign GCK = CK & enabled; \
endmodule
`define USP_TEST_CLOCK_CELL(N) \
module N(input CK, E, SE, output GCK); \
  reg enabled; always @* if (!CK) enabled = E | SE; \
  assign GCK = CK & enabled; \
endmodule
`USP_CLOCK_CELL(CLKGATE_X1)
`USP_CLOCK_CELL(CLKGATE_X2)
`USP_CLOCK_CELL(CLKGATE_X4)
`USP_CLOCK_CELL(CLKGATE_X8)
`USP_TEST_CLOCK_CELL(CLKGATETST_X1)
`USP_TEST_CLOCK_CELL(CLKGATETST_X2)
`USP_TEST_CLOCK_CELL(CLKGATETST_X4)
`USP_TEST_CLOCK_CELL(CLKGATETST_X8)
`undef USP_CLOCK_CELL
`undef USP_TEST_CLOCK_CELL
