// SPDX-License-Identifier: Apache-2.0
// Chipyard / firtool-style flattened AMBA AXI4 Full (64-bit) Euclidean GCD + Burst SRAM RTL.
// Directly loaded into USP via automatic pin-level VerilatorAxi4FullAdapter (no MMIO32 shim).
module chipyard_gcd_axi4_rtl (
    input  logic        clock,
    input  logic        reset,
    // Write Address Channel (AXI4 Full, Chipyard/firtool flattened naming)
    input  logic        io_axi_aw_valid,
    output logic        io_axi_aw_ready,
    input  logic [3:0]  io_axi_aw_bits_id,
    input  logic [11:0] io_axi_aw_bits_addr,
    input  logic [7:0]  io_axi_aw_bits_len,
    input  logic [2:0]  io_axi_aw_bits_size,
    input  logic [1:0]  io_axi_aw_bits_burst,
    // Write Data Channel
    input  logic        io_axi_w_valid,
    output logic        io_axi_w_ready,
    input  logic [63:0] io_axi_w_bits_data,
    input  logic [7:0]  io_axi_w_bits_strb,
    input  logic        io_axi_w_bits_last,
    // Write Response Channel
    output logic        io_axi_b_valid,
    input  logic        io_axi_b_ready,
    output logic [3:0]  io_axi_b_bits_id,
    output logic [1:0]  io_axi_b_bits_resp,
    // Read Address Channel
    input  logic        io_axi_ar_valid,
    output logic        io_axi_ar_ready,
    input  logic [3:0]  io_axi_ar_bits_id,
    input  logic [11:0] io_axi_ar_bits_addr,
    input  logic [7:0]  io_axi_ar_bits_len,
    input  logic [2:0]  io_axi_ar_bits_size,
    input  logic [1:0]  io_axi_ar_bits_burst,
    // Read Data Channel
    output logic        io_axi_r_valid,
    input  logic        io_axi_r_ready,
    output logic [3:0]  io_axi_r_bits_id,
    output logic [63:0] io_axi_r_bits_data,
    output logic [1:0]  io_axi_r_bits_resp,
    output logic        io_axi_r_bits_last,
    // Interrupt
    output logic        io_irq
);
    localparam logic [1:0] RESP_OKAY   = 2'b00;
    localparam logic [1:0] RESP_SLVERR = 2'b10;

    // 32 x 64-bit burst buffer at 0x100..0x1FF
    logic [63:0] burst_mem [0:31];

    // GCD registers and FSM
    logic [63:0] op_a_q, op_b_q, gcd_a_q, gcd_b_q, result_q;
    logic [63:0] burst_xor_q;
    logic [31:0] burst_beats_q;
    logic [15:0] gcd_cycles_q;
    logic        gcd_busy_q, gcd_done_q, irq_q;

    // Write FSM
    typedef enum logic [1:0] {W_IDLE, W_WAIT, W_DATA, W_RESP} w_state_t;
    w_state_t    w_state_q;
    logic [3:0]  aw_id_q;
    logic [11:0] aw_addr_q;
    logic [7:0]  aw_rem_q;
    logic [2:0]  aw_size_q;
    logic [1:0]  aw_burst_q;
    logic [1:0]  b_resp_q;

    // Read FSM
    typedef enum logic [1:0] {R_IDLE, R_WAIT, R_DATA} r_state_t;
    r_state_t    r_state_q;
    logic [3:0]  ar_id_q;
    logic [11:0] ar_addr_q;
    logic [7:0]  ar_rem_q;
    logic [2:0]  ar_size_q;
    logic [1:0]  ar_burst_q;
    logic [63:0] r_data_q;
    logic [1:0]  r_resp_q;
    logic        r_last_q;

    assign io_irq            = irq_q;
    assign io_axi_aw_ready   = (w_state_q == W_WAIT);
    assign io_axi_w_ready    = (w_state_q == W_DATA);
    assign io_axi_b_valid    = (w_state_q == W_RESP);
    assign io_axi_b_bits_id  = aw_id_q;
    assign io_axi_b_bits_resp = b_resp_q;

    assign io_axi_ar_ready   = (r_state_q == R_WAIT);
    assign io_axi_r_valid    = (r_state_q == R_DATA);
    assign io_axi_r_bits_id  = ar_id_q;
    assign io_axi_r_bits_data = r_data_q;
    assign io_axi_r_bits_resp = r_resp_q;
    assign io_axi_r_bits_last = r_last_q;

    function automatic logic [63:0] apply_strb(
        input logic [63:0] old_v,
        input logic [63:0] new_v,
        input logic [7:0]  strb
    );
        logic [63:0] out_v;
        for (int b = 0; b < 8; b++) begin
            out_v[b*8 +: 8] = strb[b] ? new_v[b*8 +: 8] : old_v[b*8 +: 8];
        end
        return out_v;
    endfunction

    function automatic logic [11:0] next_beat_addr(
        input logic [11:0] cur_a,
        input logic [2:0]  sz,
        input logic [1:0]  bst
    );
        if (bst == 2'b01) begin
            return cur_a + (12'd1 << sz);
        end
        return cur_a;
    endfunction

    function automatic void read_word(
        input  logic [11:0] addr,
        output logic [63:0] rdata,
        output logic [1:0]  rresp
    );
        logic [11:0] aligned;
        aligned = {addr[11:3], 3'b000};
        rdata = 64'd0;
        rresp = RESP_OKAY;
        if (aligned >= 12'h100 && aligned < 12'h200) begin
            rdata = burst_mem[aligned[7:3]];
        end else begin
            case (aligned)
                12'h000: rdata = {burst_beats_q, 16'd0, gcd_cycles_q[7:0], 5'd0, irq_q, gcd_done_q, gcd_busy_q};
                12'h008: rdata = op_a_q;
                12'h010: rdata = op_b_q;
                12'h018: rdata = result_q;
                12'h020: rdata = burst_xor_q;
                default: rresp = RESP_SLVERR;
            endcase
        end
    endfunction

    always_ff @(posedge clock) begin
        if (reset) begin
            for (int i = 0; i < 32; i++) begin
                burst_mem[i] <= 64'd0;
            end
            op_a_q        <= 64'd0;
            op_b_q        <= 64'd0;
            gcd_a_q       <= 64'd0;
            gcd_b_q       <= 64'd0;
            result_q      <= 64'd0;
            burst_xor_q   <= 64'd0;
            burst_beats_q <= 32'd0;
            gcd_cycles_q  <= 16'd0;
            gcd_busy_q    <= 1'b0;
            gcd_done_q    <= 1'b0;
            irq_q         <= 1'b0;

            w_state_q     <= W_IDLE;
            aw_id_q       <= 4'd0;
            aw_addr_q     <= 12'd0;
            aw_rem_q      <= 8'd0;
            aw_size_q     <= 3'd3;
            aw_burst_q    <= 2'd1;
            b_resp_q      <= RESP_OKAY;

            r_state_q     <= R_IDLE;
            ar_id_q       <= 4'd0;
            ar_addr_q     <= 12'd0;
            ar_rem_q      <= 8'd0;
            ar_size_q     <= 3'd3;
            ar_burst_q    <= 2'd1;
            r_data_q      <= 64'd0;
            r_resp_q      <= RESP_OKAY;
            r_last_q      <= 1'b0;
        end else begin
            // Euclidean GCD hardware FSM (runs concurrently with AXI4 traffic)
            if (gcd_busy_q) begin
                gcd_cycles_q <= gcd_cycles_q + 16'd1;
                if (gcd_b_q == 64'd0) begin
                    result_q   <= gcd_a_q;
                    gcd_busy_q <= 1'b0;
                    gcd_done_q <= 1'b1;
                    irq_q      <= 1'b1;
                end else if (gcd_a_q == 64'd0) begin
                    result_q   <= gcd_b_q;
                    gcd_busy_q <= 1'b0;
                    gcd_done_q <= 1'b1;
                    irq_q      <= 1'b1;
                end else if (gcd_a_q >= gcd_b_q) begin
                    gcd_a_q <= gcd_a_q - gcd_b_q;
                end else begin
                    gcd_b_q <= gcd_b_q - gcd_a_q;
                end
            end

            // AXI4 Write Channel FSM (1-cycle AW wait-state -> multi-beat W -> B)
            case (w_state_q)
                W_IDLE: begin
                    if (io_axi_aw_valid) begin
                        w_state_q <= W_WAIT;
                    end
                end
                W_WAIT: begin
                    if (io_axi_aw_valid) begin
                        aw_id_q    <= io_axi_aw_bits_id;
                        aw_addr_q  <= io_axi_aw_bits_addr;
                        aw_rem_q   <= io_axi_aw_bits_len;
                        aw_size_q  <= io_axi_aw_bits_size;
                        aw_burst_q <= io_axi_aw_bits_burst;
                        b_resp_q   <= RESP_OKAY;
                        w_state_q  <= W_DATA;
                    end
                end
                W_DATA: begin
                    if (io_axi_w_valid) begin
                        logic [11:0] w_aligned;
                        w_aligned = {aw_addr_q[11:3], 3'b000};
                        if (w_aligned >= 12'h100 && w_aligned < 12'h200) begin
                            logic [4:0]  idx;
                            logic [63:0] merged;
                            idx            = w_aligned[7:3];
                            merged         = apply_strb(burst_mem[idx], io_axi_w_bits_data, io_axi_w_bits_strb);
                            burst_mem[idx] <= merged;
                            burst_xor_q    <= burst_xor_q ^ merged;
                            burst_beats_q  <= burst_beats_q + 32'd1;
                        end else begin
                            case (w_aligned)
                                12'h000: begin
                                    if (io_axi_w_bits_strb[0]) begin
                                        if (io_axi_w_bits_data[0]) begin
                                            gcd_a_q      <= op_a_q;
                                            gcd_b_q      <= op_b_q;
                                            gcd_cycles_q <= 16'd0;
                                            gcd_busy_q   <= 1'b1;
                                            gcd_done_q   <= 1'b0;
                                        end
                                        if (io_axi_w_bits_data[1]) begin
                                            irq_q <= 1'b0;
                                        end
                                        if (io_axi_w_bits_data[2]) begin
                                            burst_xor_q   <= 64'd0;
                                            burst_beats_q <= 32'd0;
                                        end
                                    end
                                end
                                12'h008: op_a_q <= apply_strb(op_a_q, io_axi_w_bits_data, io_axi_w_bits_strb);
                                12'h010: op_b_q <= apply_strb(op_b_q, io_axi_w_bits_data, io_axi_w_bits_strb);
                                default: b_resp_q <= RESP_SLVERR;
                            endcase
                        end

                        if (aw_rem_q == 8'd0 || io_axi_w_bits_last) begin
                            w_state_q <= W_RESP;
                        end else begin
                            aw_rem_q  <= aw_rem_q - 8'd1;
                            aw_addr_q <= next_beat_addr(aw_addr_q, aw_size_q, aw_burst_q);
                        end
                    end
                end
                W_RESP: begin
                    if (io_axi_b_ready) begin
                        w_state_q <= W_IDLE;
                    end
                end
                default: w_state_q <= W_IDLE;
            endcase

            // AXI4 Read Channel FSM (1-cycle AR wait-state -> multi-beat R)
            case (r_state_q)
                R_IDLE: begin
                    if (io_axi_ar_valid) begin
                        r_state_q <= R_WAIT;
                    end
                end
                R_WAIT: begin
                    if (io_axi_ar_valid) begin
                        logic [63:0] rd_v;
                        logic [1:0]  rd_r;
                        ar_id_q    <= io_axi_ar_bits_id;
                        ar_addr_q  <= io_axi_ar_bits_addr;
                        ar_rem_q   <= io_axi_ar_bits_len;
                        ar_size_q  <= io_axi_ar_bits_size;
                        ar_burst_q <= io_axi_ar_bits_burst;
                        read_word(io_axi_ar_bits_addr, rd_v, rd_r);
                        r_data_q   <= rd_v;
                        r_resp_q   <= rd_r;
                        r_last_q   <= (io_axi_ar_bits_len == 8'd0);
                        r_state_q  <= R_DATA;
                    end
                end
                R_DATA: begin
                    if (io_axi_r_ready) begin
                        if (r_last_q || ar_rem_q == 8'd0) begin
                            r_state_q <= R_IDLE;
                        end else begin
                            logic [11:0] nxt_a;
                            logic [7:0]  nxt_rem;
                            logic [63:0] rd_v;
                            logic [1:0]  rd_r;
                            nxt_a   = next_beat_addr(ar_addr_q, ar_size_q, ar_burst_q);
                            nxt_rem = ar_rem_q - 8'd1;
                            ar_addr_q <= nxt_a;
                            ar_rem_q  <= nxt_rem;
                            read_word(nxt_a, rd_v, rd_r);
                            r_data_q  <= rd_v;
                            r_resp_q  <= rd_r;
                            r_last_q  <= (nxt_rem == 8'd0);
                        end
                    end
                end
                default: r_state_q <= R_IDLE;
            endcase
        end
    end
endmodule
