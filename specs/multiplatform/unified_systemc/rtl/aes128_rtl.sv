// SPDX-License-Identifier: Apache-2.0
// Synthesizable FIPS 197 AES-128 Hardware Encryption Engine RTL (10 hardware clock rounds).
// Original clean-room RTL co-simulated via Verilator 5.032 inside Unified_Hetero_MultiPlatform_SoC.
// NOTE: Distinct from upstream OpenTitan hw/ip/aes RTL.
//
// Register Map:
//   0x00 CTRL       (W: bit0=start_encrypt; R: bit0=busy, bit1=done)
//   0x04 IRQ_STATUS (R/W1C: bit0=aes_done_irq)
//   0x08 BLOCKS_CNT (RO: total 128-bit blocks encrypted)
//   0x10..0x1C KEY[0..3] (RW: 128-bit big-endian key words)
//   0x20..0x2C PT[0..3]  (RW: 128-bit big-endian plaintext words)
//   0x30..0x3C CT[0..3]  (RO: 128-bit big-endian ciphertext words)
module aes128_rtl (
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
    logic [31:0] key_q   [0:3];
    logic [31:0] pt_q    [0:3];
    logic [31:0] ct_q    [0:3];
    logic [31:0] st_q    [0:3];
    logic [31:0] rk_q    [0:3];
    logic        busy_q, done_q, irq_q;
    logic [3:0]  round_q;
    logic [31:0] blocks_cnt_q;
    logic [63:0] cycle_cnt_q;

    function automatic logic [7:0] sbox(input logic [7:0] x);
        case (x)
            8'h00: sbox=8'h63; 8'h01: sbox=8'h7c; 8'h02: sbox=8'h77; 8'h03: sbox=8'h7b;
            8'h04: sbox=8'hf2; 8'h05: sbox=8'h6b; 8'h06: sbox=8'h6f; 8'h07: sbox=8'hc5;
            8'h08: sbox=8'h30; 8'h09: sbox=8'h01; 8'h0a: sbox=8'h67; 8'h0b: sbox=8'h2b;
            8'h0c: sbox=8'hfe; 8'h0d: sbox=8'hd7; 8'h0e: sbox=8'hab; 8'h0f: sbox=8'h76;
            8'h10: sbox=8'hca; 8'h11: sbox=8'h82; 8'h12: sbox=8'hc9; 8'h13: sbox=8'h7d;
            8'h14: sbox=8'hfa; 8'h15: sbox=8'h59; 8'h16: sbox=8'h47; 8'h17: sbox=8'hf0;
            8'h18: sbox=8'had; 8'h19: sbox=8'hd4; 8'h1a: sbox=8'ha2; 8'h1b: sbox=8'haf;
            8'h1c: sbox=8'h9c; 8'h1d: sbox=8'ha4; 8'h1e: sbox=8'h72; 8'h1f: sbox=8'hc0;
            8'h20: sbox=8'hb7; 8'h21: sbox=8'hfd; 8'h22: sbox=8'h93; 8'h23: sbox=8'h26;
            8'h24: sbox=8'h36; 8'h25: sbox=8'h3f; 8'h26: sbox=8'hf7; 8'h27: sbox=8'hcc;
            8'h28: sbox=8'h34; 8'h29: sbox=8'ha5; 8'h2a: sbox=8'he5; 8'h2b: sbox=8'hf1;
            8'h2c: sbox=8'h71; 8'h2d: sbox=8'hd8; 8'h2e: sbox=8'h31; 8'h2f: sbox=8'h15;
            8'h30: sbox=8'h04; 8'h31: sbox=8'hc7; 8'h32: sbox=8'h23; 8'h33: sbox=8'hc3;
            8'h34: sbox=8'h18; 8'h35: sbox=8'h96; 8'h36: sbox=8'h05; 8'h37: sbox=8'h9a;
            8'h38: sbox=8'h07; 8'h39: sbox=8'h12; 8'h3a: sbox=8'h80; 8'h3b: sbox=8'he2;
            8'h3c: sbox=8'heb; 8'h3d: sbox=8'h27; 8'h3e: sbox=8'hb2; 8'h3f: sbox=8'h75;
            8'h40: sbox=8'h09; 8'h41: sbox=8'h83; 8'h42: sbox=8'h2c; 8'h43: sbox=8'h1a;
            8'h44: sbox=8'h1b; 8'h45: sbox=8'h6e; 8'h46: sbox=8'h5a; 8'h47: sbox=8'ha0;
            8'h48: sbox=8'h52; 8'h49: sbox=8'h3b; 8'h4a: sbox=8'hd6; 8'h4b: sbox=8'hb3;
            8'h4c: sbox=8'h29; 8'h4d: sbox=8'he3; 8'h4e: sbox=8'h2f; 8'h4f: sbox=8'h84;
            8'h50: sbox=8'h53; 8'h51: sbox=8'hd1; 8'h52: sbox=8'h00; 8'h53: sbox=8'hed;
            8'h54: sbox=8'h20; 8'h55: sbox=8'hfc; 8'h56: sbox=8'hb1; 8'h57: sbox=8'h5b;
            8'h58: sbox=8'h6a; 8'h59: sbox=8'hcb; 8'h5a: sbox=8'hbe; 8'h5b: sbox=8'h39;
            8'h5c: sbox=8'h4a; 8'h5d: sbox=8'h4c; 8'h5e: sbox=8'h58; 8'h5f: sbox=8'hcf;
            8'h60: sbox=8'hd0; 8'h61: sbox=8'hef; 8'h62: sbox=8'haa; 8'h63: sbox=8'hfb;
            8'h64: sbox=8'h43; 8'h65: sbox=8'h4d; 8'h66: sbox=8'h33; 8'h67: sbox=8'h85;
            8'h68: sbox=8'h45; 8'h69: sbox=8'hf9; 8'h6a: sbox=8'h02; 8'h6b: sbox=8'h7f;
            8'h6c: sbox=8'h50; 8'h6d: sbox=8'h3c; 8'h6e: sbox=8'h9f; 8'h6f: sbox=8'ha8;
            8'h70: sbox=8'h51; 8'h71: sbox=8'ha3; 8'h72: sbox=8'h40; 8'h73: sbox=8'h8f;
            8'h74: sbox=8'h92; 8'h75: sbox=8'h9d; 8'h76: sbox=8'h38; 8'h77: sbox=8'hf5;
            8'h78: sbox=8'hbc; 8'h79: sbox=8'hb6; 8'h7a: sbox=8'hda; 8'h7b: sbox=8'h21;
            8'h7c: sbox=8'h10; 8'h7d: sbox=8'hff; 8'h7e: sbox=8'hf3; 8'h7f: sbox=8'hd2;
            8'h80: sbox=8'hcd; 8'h81: sbox=8'h0c; 8'h82: sbox=8'h13; 8'h83: sbox=8'hec;
            8'h84: sbox=8'h5f; 8'h85: sbox=8'h97; 8'h86: sbox=8'h44; 8'h87: sbox=8'h17;
            8'h88: sbox=8'hc4; 8'h89: sbox=8'ha7; 8'h8a: sbox=8'h7e; 8'h8b: sbox=8'h3d;
            8'h8c: sbox=8'h64; 8'h8d: sbox=8'h5d; 8'h8e: sbox=8'h19; 8'h8f: sbox=8'h73;
            8'h90: sbox=8'h60; 8'h91: sbox=8'h81; 8'h92: sbox=8'h4f; 8'h93: sbox=8'hdc;
            8'h94: sbox=8'h22; 8'h95: sbox=8'h2a; 8'h96: sbox=8'h90; 8'h97: sbox=8'h88;
            8'h98: sbox=8'h46; 8'h99: sbox=8'hee; 8'h9a: sbox=8'hb8; 8'h9b: sbox=8'h14;
            8'h9c: sbox=8'hde; 8'h9d: sbox=8'h5e; 8'h9e: sbox=8'h0b; 8'h9f: sbox=8'hdb;
            8'ha0: sbox=8'he0; 8'ha1: sbox=8'h32; 8'ha2: sbox=8'h3a; 8'ha3: sbox=8'h0a;
            8'ha4: sbox=8'h49; 8'ha5: sbox=8'h06; 8'ha6: sbox=8'h24; 8'ha7: sbox=8'h5c;
            8'ha8: sbox=8'hc2; 8'ha9: sbox=8'hd3; 8'haa: sbox=8'hac; 8'hab: sbox=8'h62;
            8'hac: sbox=8'h91; 8'had: sbox=8'h95; 8'hae: sbox=8'he4; 8'haf: sbox=8'h79;
            8'hb0: sbox=8'he7; 8'hb1: sbox=8'hc8; 8'hb2: sbox=8'h37; 8'hb3: sbox=8'h6d;
            8'hb4: sbox=8'h8d; 8'hb5: sbox=8'hd5; 8'hb6: sbox=8'h4e; 8'hb7: sbox=8'ha9;
            8'hb8: sbox=8'h6c; 8'hb9: sbox=8'h56; 8'hba: sbox=8'hf4; 8'hbb: sbox=8'hea;
            8'hbc: sbox=8'h65; 8'hbd: sbox=8'h7a; 8'hbe: sbox=8'hae; 8'hbf: sbox=8'h08;
            8'hc0: sbox=8'hba; 8'hc1: sbox=8'h78; 8'hc2: sbox=8'h25; 8'hc3: sbox=8'h2e;
            8'hc4: sbox=8'h1c; 8'hc5: sbox=8'ha6; 8'hc6: sbox=8'hb4; 8'hc7: sbox=8'hc6;
            8'hc8: sbox=8'he8; 8'hc9: sbox=8'hdd; 8'hca: sbox=8'h74; 8'hcb: sbox=8'h1f;
            8'hcc: sbox=8'h4b; 8'hcd: sbox=8'hbd; 8'hce: sbox=8'h8b; 8'hcf: sbox=8'h8a;
            8'hd0: sbox=8'h70; 8'hd1: sbox=8'h3e; 8'hd2: sbox=8'hb5; 8'hd3: sbox=8'h66;
            8'hd4: sbox=8'h48; 8'hd5: sbox=8'h03; 8'hd6: sbox=8'hf6; 8'hd7: sbox=8'h0e;
            8'hd8: sbox=8'h61; 8'hd9: sbox=8'h35; 8'hda: sbox=8'h57; 8'hdb: sbox=8'hb9;
            8'hdc: sbox=8'h86; 8'hdd: sbox=8'hc1; 8'hde: sbox=8'h1d; 8'hdf: sbox=8'h9e;
            8'he0: sbox=8'he1; 8'he1: sbox=8'hf8; 8'he2: sbox=8'h98; 8'he3: sbox=8'h11;
            8'he4: sbox=8'h69; 8'he5: sbox=8'hd9; 8'he6: sbox=8'h8e; 8'he7: sbox=8'h94;
            8'he8: sbox=8'h9b; 8'he9: sbox=8'h1e; 8'hea: sbox=8'h87; 8'heb: sbox=8'he9;
            8'hec: sbox=8'hce; 8'hed: sbox=8'h55; 8'hee: sbox=8'h28; 8'hef: sbox=8'hdf;
            8'hf0: sbox=8'h8c; 8'hf1: sbox=8'ha1; 8'hf2: sbox=8'h89; 8'hf3: sbox=8'h0d;
            8'hf4: sbox=8'hbf; 8'hf5: sbox=8'he6; 8'hf6: sbox=8'h42; 8'hf7: sbox=8'h68;
            8'hf8: sbox=8'h41; 8'hf9: sbox=8'h99; 8'hfa: sbox=8'h2d; 8'hfb: sbox=8'h0f;
            8'hfc: sbox=8'hb0; 8'hfd: sbox=8'h54; 8'hfe: sbox=8'hbb; 8'hff: sbox=8'h16;
        endcase
    endfunction

    function automatic logic [7:0] xtime(input logic [7:0] x);
        xtime = {x[6:0], 1'b0} ^ (x[7] ? 8'h1b : 8'h00);
    endfunction

    function automatic logic [31:0] mix_col(input logic [31:0] col);
        logic [7:0] a0, a1, a2, a3;
        logic [7:0] r0, r1, r2, r3;
        a0 = col[31:24]; a1 = col[23:16]; a2 = col[15:8]; a3 = col[7:0];
        r0 = xtime(a0) ^ xtime(a1) ^ a1 ^ a2 ^ a3;
        r1 = a0 ^ xtime(a1) ^ xtime(a2) ^ a2 ^ a3;
        r2 = a0 ^ a1 ^ xtime(a2) ^ xtime(a3) ^ a3;
        r3 = xtime(a0) ^ a0 ^ a1 ^ a2 ^ xtime(a3);
        mix_col = {r0, r1, r2, r3};
    endfunction

    function automatic logic [7:0] rcon_byte(input logic [3:0] r);
        case (r)
            4'd1:  rcon_byte = 8'h01;
            4'd2:  rcon_byte = 8'h02;
            4'd3:  rcon_byte = 8'h04;
            4'd4:  rcon_byte = 8'h08;
            4'd5:  rcon_byte = 8'h10;
            4'd6:  rcon_byte = 8'h20;
            4'd7:  rcon_byte = 8'h40;
            4'd8:  rcon_byte = 8'h80;
            4'd9:  rcon_byte = 8'h1b;
            4'd10: rcon_byte = 8'h36;
            default: rcon_byte = 8'h00;
        endcase
    endfunction

    // Combinational round key expansion & round transform
    logic [31:0] ktmp_w, nk0_w, nk1_w, nk2_w, nk3_w;
    logic [7:0]  sb_w [0:15];
    logic [31:0] sr0_w, sr1_w, sr2_w, sr3_w;
    logic [31:0] nx0_w, nx1_w, nx2_w, nx3_w;

    always_comb begin
        ktmp_w = {sbox(rk_q[3][23:16]) ^ rcon_byte(round_q),
                  sbox(rk_q[3][15:8]),
                  sbox(rk_q[3][7:0]),
                  sbox(rk_q[3][31:24])};
        nk0_w = rk_q[0] ^ ktmp_w;
        nk1_w = rk_q[1] ^ nk0_w;
        nk2_w = rk_q[2] ^ nk1_w;
        nk3_w = rk_q[3] ^ nk2_w;

        sb_w[0]  = sbox(st_q[0][31:24]); sb_w[1]  = sbox(st_q[0][23:16]);
        sb_w[2]  = sbox(st_q[0][15:8]);  sb_w[3]  = sbox(st_q[0][7:0]);
        sb_w[4]  = sbox(st_q[1][31:24]); sb_w[5]  = sbox(st_q[1][23:16]);
        sb_w[6]  = sbox(st_q[1][15:8]);  sb_w[7]  = sbox(st_q[1][7:0]);
        sb_w[8]  = sbox(st_q[2][31:24]); sb_w[9]  = sbox(st_q[2][23:16]);
        sb_w[10] = sbox(st_q[2][15:8]);  sb_w[11] = sbox(st_q[2][7:0]);
        sb_w[12] = sbox(st_q[3][31:24]); sb_w[13] = sbox(st_q[3][23:16]);
        sb_w[14] = sbox(st_q[3][15:8]);  sb_w[15] = sbox(st_q[3][7:0]);

        sr0_w = {sb_w[0],  sb_w[5],  sb_w[10], sb_w[15]};
        sr1_w = {sb_w[4],  sb_w[9],  sb_w[14], sb_w[3]};
        sr2_w = {sb_w[8],  sb_w[13], sb_w[2],  sb_w[7]};
        sr3_w = {sb_w[12], sb_w[1],  sb_w[6],  sb_w[11]};

        if (round_q == 4'd10) begin
            nx0_w = sr0_w ^ nk0_w;
            nx1_w = sr1_w ^ nk1_w;
            nx2_w = sr2_w ^ nk2_w;
            nx3_w = sr3_w ^ nk3_w;
        end else begin
            nx0_w = mix_col(sr0_w) ^ nk0_w;
            nx1_w = mix_col(sr1_w) ^ nk1_w;
            nx2_w = mix_col(sr2_w) ^ nk2_w;
            nx3_w = mix_col(sr3_w) ^ nk3_w;
        end
    end

    assign busy_o       = busy_q;
    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i) begin
            if (req_write_i) begin
                case (req_addr_i)
                    8'h00, 8'h04,
                    8'h10, 8'h14, 8'h18, 8'h1C,
                    8'h20, 8'h24, 8'h28, 8'h2C: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = {30'd0, done_q, busy_q};
                    8'h04: rsp_rdata_o = {31'd0, irq_q};
                    8'h08: rsp_rdata_o = blocks_cnt_q;
                    8'h10: rsp_rdata_o = key_q[0];
                    8'h14: rsp_rdata_o = key_q[1];
                    8'h18: rsp_rdata_o = key_q[2];
                    8'h1C: rsp_rdata_o = key_q[3];
                    8'h20: rsp_rdata_o = pt_q[0];
                    8'h24: rsp_rdata_o = pt_q[1];
                    8'h28: rsp_rdata_o = pt_q[2];
                    8'h2C: rsp_rdata_o = pt_q[3];
                    8'h30: rsp_rdata_o = ct_q[0];
                    8'h34: rsp_rdata_o = ct_q[1];
                    8'h38: rsp_rdata_o = ct_q[2];
                    8'h3C: rsp_rdata_o = ct_q[3];
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    integer i;
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            for (i = 0; i < 4; i = i + 1) begin
                key_q[i] <= 32'd0;
                pt_q[i]  <= 32'd0;
                ct_q[i]  <= 32'd0;
                st_q[i]  <= 32'd0;
                rk_q[i]  <= 32'd0;
            end
            busy_q       <= 1'b0;
            done_q       <= 1'b0;
            irq_q        <= 1'b0;
            round_q      <= 4'd0;
            blocks_cnt_q <= 32'd0;
            cycle_cnt_q  <= 64'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            if (busy_q) begin
                rk_q[0] <= nk0_w; rk_q[1] <= nk1_w; rk_q[2] <= nk2_w; rk_q[3] <= nk3_w;
                if (round_q == 4'd10) begin
                    ct_q[0]      <= nx0_w;
                    ct_q[1]      <= nx1_w;
                    ct_q[2]      <= nx2_w;
                    ct_q[3]      <= nx3_w;
                    busy_q       <= 1'b0;
                    done_q       <= 1'b1;
                    irq_q        <= 1'b1;
                    blocks_cnt_q <= blocks_cnt_q + 32'd1;
                end else begin
                    st_q[0] <= nx0_w; st_q[1] <= nx1_w; st_q[2] <= nx2_w; st_q[3] <= nx3_w;
                    round_q <= round_q + 4'd1;
                end
            end else if (req_valid_i && req_write_i && !rsp_err_o) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i[0]) begin
                            for (i = 0; i < 4; i = i + 1) begin
                                st_q[i] <= pt_q[i] ^ key_q[i];
                                rk_q[i] <= key_q[i];
                            end
                            round_q <= 4'd1;
                            busy_q  <= 1'b1;
                            done_q  <= 1'b0;
                        end
                    end
                    8'h04: if (req_wdata_i[0]) irq_q <= 1'b0;
                    8'h10: key_q[0] <= req_wdata_i;
                    8'h14: key_q[1] <= req_wdata_i;
                    8'h18: key_q[2] <= req_wdata_i;
                    8'h1C: key_q[3] <= req_wdata_i;
                    8'h20: pt_q[0]  <= req_wdata_i;
                    8'h24: pt_q[1]  <= req_wdata_i;
                    8'h28: pt_q[2]  <= req_wdata_i;
                    8'h2C: pt_q[3]  <= req_wdata_i;
                    default: ;
                endcase
            end
        end
    end
endmodule
