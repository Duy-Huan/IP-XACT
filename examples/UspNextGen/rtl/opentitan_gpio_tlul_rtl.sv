// SPDX-License-Identifier: Apache-2.0
// OpenTitan TL-UL (TileLink Uncached Lightweight) 32-bit GPIO & Edge-IRQ Controller RTL.
// Directly loaded into USP via automatic pin-level VerilatorTlulAdapter (no MMIO32 shim).
module opentitan_gpio_tlul_rtl (
    input  logic        clk_i,
    input  logic        rst_ni,
    // OpenTitan TL-UL A-channel (Host -> Device)
    input  logic        tl_a_valid_i,
    output logic        tl_a_ready_o,
    input  logic [2:0]  tl_a_opcode_i,
    input  logic [2:0]  tl_a_param_i,
    input  logic [1:0]  tl_a_size_i,
    input  logic [7:0]  tl_a_source_i,
    input  logic [11:0] tl_a_address_i,
    input  logic [3:0]  tl_a_mask_i,
    input  logic [31:0] tl_a_data_i,
    // OpenTitan TL-UL D-channel (Device -> Host)
    output logic        tl_d_valid_o,
    input  logic        tl_d_ready_i,
    output logic [2:0]  tl_d_opcode_o,
    output logic [1:0]  tl_d_param_o,
    output logic [1:0]  tl_d_size_o,
    output logic [7:0]  tl_d_source_o,
    output logic [31:0] tl_d_data_o,
    output logic        tl_d_error_o,
    // OpenTitan GPIO aggregate interrupt output
    output logic        intr_gpio_o
);
    localparam logic [2:0] TL_PUT_FULL    = 3'd0;
    localparam logic [2:0] TL_PUT_PARTIAL = 3'd1;
    localparam logic [2:0] TL_GET         = 3'd4;
    localparam logic [2:0] TL_ACCESS_ACK  = 3'd0;
    localparam logic [2:0] TL_ACCESS_DATA = 3'd1;

    // OpenTitan GPIO registers
    logic [31:0] intr_state_q;
    logic [31:0] intr_enable_q;
    logic [31:0] direct_out_q;
    logic [31:0] direct_oe_q;
    logic [31:0] intr_ctrl_en_rising_q;

    // TL-UL A/D valid-ready handshake registers (D-channel holds stable under !tl_d_ready_i)
    logic        a_wait_q;
    logic        d_valid_q;
    logic [2:0]  d_opcode_q;
    logic [1:0]  d_size_q;
    logic [7:0]  d_source_q;
    logic [31:0] d_data_q;
    logic        d_error_q;

    assign tl_a_ready_o  = (!d_valid_q) && a_wait_q;
    assign tl_d_valid_o  = d_valid_q;
    assign tl_d_opcode_o = d_opcode_q;
    assign tl_d_param_o  = 2'd0;
    assign tl_d_size_o   = d_size_q;
    assign tl_d_source_o = d_source_q;
    assign tl_d_data_o   = d_data_q;
    assign tl_d_error_o  = d_error_q;
    assign intr_gpio_o   = |(intr_state_q & intr_enable_q);

    function automatic logic [31:0] apply_mask32(
        input logic [31:0] old_v,
        input logic [31:0] new_v,
        input logic [3:0]  mask
    );
        logic [31:0] res;
        for (int b = 0; b < 4; b++) begin
            res[b*8 +: 8] = mask[b] ? new_v[b*8 +: 8] : old_v[b*8 +: 8];
        end
        return res;
    endfunction

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            intr_state_q          <= 32'd0;
            intr_enable_q         <= 32'd0;
            direct_out_q          <= 32'd0;
            direct_oe_q           <= 32'd0;
            intr_ctrl_en_rising_q <= 32'd0;
            a_wait_q              <= 1'b0;
            d_valid_q             <= 1'b0;
            d_opcode_q            <= TL_ACCESS_ACK;
            d_size_q              <= 2'd2;
            d_source_q            <= 8'd0;
            d_data_q              <= 32'd0;
            d_error_q             <= 1'b0;
        end else begin
            // Hold D-channel outputs completely stable while d_valid_q && !tl_d_ready_i!
            if (d_valid_q) begin
                if (tl_d_ready_i) begin
                    d_valid_q <= 1'b0;
                    a_wait_q  <= 1'b0;
                end
            end else if (tl_a_valid_i && !a_wait_q) begin
                // 1-cycle A-channel wait-state to exercise valid-before-ready handshake
                a_wait_q <= 1'b1;
            end else if (tl_a_valid_i && tl_a_ready_o) begin
                logic        is_write;
                logic        is_read;
                logic        err;
                logic [31:0] rdata;
                is_write = (tl_a_opcode_i == TL_PUT_FULL) || (tl_a_opcode_i == TL_PUT_PARTIAL);
                is_read  = (tl_a_opcode_i == TL_GET);
                err      = !(is_write || is_read);
                rdata    = 32'd0;

                if (is_write) begin
                    case (tl_a_address_i[7:0])
                        8'h00: begin
                            // INTR_STATE: RW1C
                            intr_state_q <= intr_state_q & ~apply_mask32(32'd0, tl_a_data_i, tl_a_mask_i);
                        end
                        8'h04: intr_enable_q <= apply_mask32(intr_enable_q, tl_a_data_i, tl_a_mask_i);
                        8'h08: intr_state_q  <= intr_state_q | apply_mask32(32'd0, tl_a_data_i, tl_a_mask_i);
                        8'h14: begin
                            logic [31:0] prev_in, next_out, next_in, rising;
                            prev_in      = direct_out_q & direct_oe_q;
                            next_out     = apply_mask32(direct_out_q, tl_a_data_i, tl_a_mask_i);
                            next_in      = next_out & direct_oe_q;
                            rising       = (~prev_in) & next_in;
                            direct_out_q <= next_out;
                            intr_state_q <= intr_state_q | (rising & intr_ctrl_en_rising_q);
                        end
                        8'h18: direct_oe_q           <= apply_mask32(direct_oe_q, tl_a_data_i, tl_a_mask_i);
                        8'h1C: intr_ctrl_en_rising_q <= apply_mask32(intr_ctrl_en_rising_q, tl_a_data_i, tl_a_mask_i);
                        default: err = 1'b1;
                    endcase
                end else if (is_read) begin
                    case (tl_a_address_i[7:0])
                        8'h00: rdata = intr_state_q;
                        8'h04: rdata = intr_enable_q;
                        8'h10: rdata = direct_out_q & direct_oe_q;
                        8'h14: rdata = direct_out_q;
                        8'h18: rdata = direct_oe_q;
                        8'h1C: rdata = intr_ctrl_en_rising_q;
                        default: err = 1'b1;
                    endcase
                end

                d_valid_q  <= 1'b1;
                d_opcode_q <= is_read ? TL_ACCESS_DATA : TL_ACCESS_ACK;
                d_size_q   <= tl_a_size_i;
                d_source_q <= tl_a_source_i;
                d_data_q   <= err ? 32'd0 : rdata;
                d_error_q  <= err;
                a_wait_q   <= 1'b0;
            end
        end
    end
endmodule
