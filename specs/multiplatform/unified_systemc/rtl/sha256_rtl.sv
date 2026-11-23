// SPDX-License-Identifier: Apache-2.0
// Synthesizable FIPS 180-4 SHA-256 Hardware Compression Engine RTL (64 clock rounds per block).
// Original clean-room RTL co-simulated via Verilator 5.032 inside Unified_Hetero_MultiPlatform_SoC.
//
// Register Map:
//   0x00 CTRL       (W: bit0=start_first_block, bit1=start_next_block; R: bit0=busy, bit1=done)
//   0x04 IRQ_STATUS (R/W1C: bit0=hash_done_irq)
//   0x08 BLOCKS_CNT (R: total 512-bit blocks hashed)
//   0x10..0x4C MSG_W[0..15] (RW: 16x 32-bit big-endian message block words)
//   0x50..0x6C DIGEST[0..7] (RO: 8x 32-bit SHA-256 hash output H0..H7)
module sha256_rtl (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        req_valid_i,
    input  logic        req_write_i,
    input  logic [7:0]  req_addr_i,
    input  logic [31:0] req_wdata_i,
    output logic [31:0] rsp_rdata_o,
    output logic        rsp_err_o,
    output logic        busy_o,
    output logic        irq_o,
    output logic [63:0] rtl_cycles_o
);
    localparam logic [31:0] IV0 = 32'h6a09e667;
    localparam logic [31:0] IV1 = 32'hbb67ae85;
    localparam logic [31:0] IV2 = 32'h3c6ef372;
    localparam logic [31:0] IV3 = 32'ha54ff53a;
    localparam logic [31:0] IV4 = 32'h510e527f;
    localparam logic [31:0] IV5 = 32'h9b05688c;
    localparam logic [31:0] IV6 = 32'h1f83d9ab;
    localparam logic [31:0] IV7 = 32'h5be0cd19;

    logic [31:0] msg_q [0:15];
    logic [31:0] w_q   [0:15];
    logic [31:0] h_q   [0:7];
    logic [31:0] a_q, b_q, c_q, d_q, e_q, f_q, g_q, h_work_q;
    logic        busy_q, done_q, irq_q;
    logic [6:0]  round_q;
    logic [31:0] blocks_cnt_q;
    logic [63:0] cycle_cnt_q;

    function automatic logic [31:0] rotr32(input logic [31:0] x, input logic [4:0] n);
        rotr32 = (x >> n) | (x << (6'd32 - {1'b0, n}));
    endfunction

    function automatic logic [31:0] k_const(input logic [5:0] idx);
        case (idx)
            6'd0:  k_const = 32'h428a2f98; 6'd1:  k_const = 32'h71374491;
            6'd2:  k_const = 32'hb5c0fbcf; 6'd3:  k_const = 32'he9b5dba5;
            6'd4:  k_const = 32'h3956c25b; 6'd5:  k_const = 32'h59f111f1;
            6'd6:  k_const = 32'h923f82a4; 6'd7:  k_const = 32'hab1c5ed5;
            6'd8:  k_const = 32'hd807aa98; 6'd9:  k_const = 32'h12835b01;
            6'd10: k_const = 32'h243185be; 6'd11: k_const = 32'h550c7dc3;
            6'd12: k_const = 32'h72be5d74; 6'd13: k_const = 32'h80deb1fe;
            6'd14: k_const = 32'h9bdc06a7; 6'd15: k_const = 32'hc19bf174;
            6'd16: k_const = 32'he49b69c1; 6'd17: k_const = 32'hefbe4786;
            6'd18: k_const = 32'h0fc19dc6; 6'd19: k_const = 32'h240ca1cc;
            6'd20: k_const = 32'h2de92c6f; 6'd21: k_const = 32'h4a7484aa;
            6'd22: k_const = 32'h5cb0a9dc; 6'd23: k_const = 32'h76f988da;
            6'd24: k_const = 32'h983e5152; 6'd25: k_const = 32'ha831c66d;
            6'd26: k_const = 32'hb00327c8; 6'd27: k_const = 32'hbf597fc7;
            6'd28: k_const = 32'hc6e00bf3; 6'd29: k_const = 32'hd5a79147;
            6'd30: k_const = 32'h06ca6351; 6'd31: k_const = 32'h14292967;
            6'd32: k_const = 32'h27b70a85; 6'd33: k_const = 32'h2e1b2138;
            6'd34: k_const = 32'h4d2c6dfc; 6'd35: k_const = 32'h53380d13;
            6'd36: k_const = 32'h650a7354; 6'd37: k_const = 32'h766a0abb;
            6'd38: k_const = 32'h81c2c92e; 6'd39: k_const = 32'h92722c85;
            6'd40: k_const = 32'ha2bfe8a1; 6'd41: k_const = 32'ha81a664b;
            6'd42: k_const = 32'hc24b8b70; 6'd43: k_const = 32'hc76c51a3;
            6'd44: k_const = 32'hd192e819; 6'd45: k_const = 32'hd6990624;
            6'd46: k_const = 32'hf40e3585; 6'd47: k_const = 32'h106aa070;
            6'd48: k_const = 32'h19a4c116; 6'd49: k_const = 32'h1e376c08;
            6'd50: k_const = 32'h2748774c; 6'd51: k_const = 32'h34b0bcb5;
            6'd52: k_const = 32'h391c0cb3; 6'd53: k_const = 32'h4ed8aa4a;
            6'd54: k_const = 32'h5b9cca4f; 6'd55: k_const = 32'h682e6ff3;
            6'd56: k_const = 32'h748f82ee; 6'd57: k_const = 32'h78a5636f;
            6'd58: k_const = 32'h84c87814; 6'd59: k_const = 32'h8cc70208;
            6'd60: k_const = 32'h90befffa; 6'd61: k_const = 32'ha4506ceb;
            6'd62: k_const = 32'hbef9a3f7; 6'd63: k_const = 32'hc67178f2;
        endcase
    endfunction

    logic [31:0] s0_w, s1_w, ch_w, maj_w, sig0_w, sig1_w, wt_w, t1_w, t2_w;
    always_comb begin
        sig0_w = rotr32(w_q[1], 5'd7) ^ rotr32(w_q[1], 5'd18) ^ (w_q[1] >> 3);
        sig1_w = rotr32(w_q[14], 5'd17) ^ rotr32(w_q[14], 5'd19) ^ (w_q[14] >> 10);
        wt_w   = (round_q < 7'd16) ? w_q[round_q[3:0]] : (w_q[0] + sig0_w + w_q[9] + sig1_w);
        s1_w   = rotr32(e_q, 5'd6) ^ rotr32(e_q, 5'd11) ^ rotr32(e_q, 5'd25);
        ch_w   = (e_q & f_q) ^ ((~e_q) & g_q);
        t1_w   = h_work_q + s1_w + ch_w + k_const(round_q[5:0]) + wt_w;
        s0_w   = rotr32(a_q, 5'd2) ^ rotr32(a_q, 5'd13) ^ rotr32(a_q, 5'd22);
        maj_w  = (a_q & b_q) ^ (a_q & c_q) ^ (b_q & c_q);
        t2_w   = s0_w + maj_w;
    end

    assign busy_o       = busy_q;
    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    logic [7:0] msg_off_w, dig_off_w;
    logic [3:0] msg_idx_w;
    logic [2:0] dig_idx_w;
    assign msg_off_w = req_addr_i - 8'h10;
    assign dig_off_w = req_addr_i - 8'h50;
    assign msg_idx_w = msg_off_w[5:2];
    assign dig_idx_w = dig_off_w[4:2];

    // MMIO read / error decode
    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i) begin
            if (req_write_i) begin
                if (req_addr_i == 8'h00 || req_addr_i == 8'h04) begin
                    rsp_err_o = 1'b0;
                end else if (req_addr_i >= 8'h10 && req_addr_i <= 8'h4C && req_addr_i[1:0] == 2'b00) begin
                    rsp_err_o = 1'b0;
                end else begin
                    rsp_err_o = 1'b1;
                end
            end else begin
                if (req_addr_i == 8'h00) begin
                    rsp_rdata_o = {30'd0, done_q, busy_q};
                end else if (req_addr_i == 8'h04) begin
                    rsp_rdata_o = {31'd0, irq_q};
                end else if (req_addr_i == 8'h08) begin
                    rsp_rdata_o = blocks_cnt_q;
                end else if (req_addr_i >= 8'h10 && req_addr_i <= 8'h4C && req_addr_i[1:0] == 2'b00) begin
                    rsp_rdata_o = msg_q[msg_idx_w];
                end else if (req_addr_i >= 8'h50 && req_addr_i <= 8'h6C && req_addr_i[1:0] == 2'b00) begin
                    rsp_rdata_o = h_q[dig_idx_w];
                end else begin
                    rsp_err_o = 1'b1;
                end
            end
        end
    end

    integer i;
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            for (i = 0; i < 16; i = i + 1) begin
                msg_q[i] <= 32'd0;
                w_q[i]   <= 32'd0;
            end
            h_q[0] <= IV0; h_q[1] <= IV1; h_q[2] <= IV2; h_q[3] <= IV3;
            h_q[4] <= IV4; h_q[5] <= IV5; h_q[6] <= IV6; h_q[7] <= IV7;
            a_q <= 32'd0; b_q <= 32'd0; c_q <= 32'd0; d_q <= 32'd0;
            e_q <= 32'd0; f_q <= 32'd0; g_q <= 32'd0; h_work_q <= 32'd0;
            busy_q <= 1'b0;
            done_q <= 1'b0;
            irq_q  <= 1'b0;
            round_q <= 7'd0;
            blocks_cnt_q <= 32'd0;
            cycle_cnt_q  <= 64'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;

            if (busy_q) begin
                if (round_q < 7'd64) begin
                    h_work_q <= g_q;
                    g_q      <= f_q;
                    f_q      <= e_q;
                    e_q      <= d_q + t1_w;
                    d_q      <= c_q;
                    c_q      <= b_q;
                    b_q      <= a_q;
                    a_q      <= t1_w + t2_w;
                    if (round_q >= 7'd16) begin
                        for (i = 0; i < 15; i = i + 1) begin
                            w_q[i] <= w_q[i + 1];
                        end
                        w_q[15] <= wt_w;
                    end
                    round_q <= round_q + 7'd1;
                end else begin
                    h_q[0]       <= h_q[0] + a_q;
                    h_q[1]       <= h_q[1] + b_q;
                    h_q[2]       <= h_q[2] + c_q;
                    h_q[3]       <= h_q[3] + d_q;
                    h_q[4]       <= h_q[4] + e_q;
                    h_q[5]       <= h_q[5] + f_q;
                    h_q[6]       <= h_q[6] + g_q;
                    h_q[7]       <= h_q[7] + h_work_q;
                    busy_q       <= 1'b0;
                    done_q       <= 1'b1;
                    irq_q        <= 1'b1;
                    blocks_cnt_q <= blocks_cnt_q + 32'd1;
                end
            end else if (req_valid_i && req_write_i && !rsp_err_o) begin
                if (req_addr_i == 8'h00) begin
                    if (req_wdata_i[0]) begin
                        // Start first block (reset H0..H7 to FIPS IV)
                        h_q[0] <= IV0; h_q[1] <= IV1; h_q[2] <= IV2; h_q[3] <= IV3;
                        h_q[4] <= IV4; h_q[5] <= IV5; h_q[6] <= IV6; h_q[7] <= IV7;
                        a_q <= IV0; b_q <= IV1; c_q <= IV2; d_q <= IV3;
                        e_q <= IV4; f_q <= IV5; g_q <= IV6; h_work_q <= IV7;
                        for (i = 0; i < 16; i = i + 1) w_q[i] <= msg_q[i];
                        round_q <= 7'd0;
                        busy_q  <= 1'b1;
                        done_q  <= 1'b0;
                    end else if (req_wdata_i[1]) begin
                        // Continue multi-block hash using accumulated H0..H7
                        a_q <= h_q[0]; b_q <= h_q[1]; c_q <= h_q[2]; d_q <= h_q[3];
                        e_q <= h_q[4]; f_q <= h_q[5]; g_q <= h_q[6]; h_work_q <= h_q[7];
                        for (i = 0; i < 16; i = i + 1) w_q[i] <= msg_q[i];
                        round_q <= 7'd0;
                        busy_q  <= 1'b1;
                        done_q  <= 1'b0;
                    end
                end else if (req_addr_i == 8'h04) begin
                    if (req_wdata_i[0]) irq_q <= 1'b0;
                end else if (req_addr_i >= 8'h10 && req_addr_i <= 8'h4C) begin
                    msg_q[msg_idx_w] <= req_wdata_i;
                end
            end
        end
    end
endmodule
