// SPDX-License-Identifier: Apache-2.0
// Original public fixture for the generic RTL/SDC physical-flow entry point.
module usp_physical_counter (
    input wire clock,
    input wire reset,
    input wire enable,
    output reg [127:0] count
);
    always @(posedge clock) begin
        if (reset)
            count <= 128'b0;
        else if (enable)
            count <= count + 128'd1;
    end
endmodule
