// SPDX-License-Identifier: Apache-2.0
// Synthesizable 64-Line Direct-Mapped L2 Cache Tag & DMA Coherency Snoop Monitor RTL.
// Driven live on every DRAM transaction across the 2x2 Mesh NoC inside Unified_Hetero_MultiPlatform_SoC.
//
// Lookup ports (driven by NoC2x2Router):
//   lookup_valid_i, lookup_is_dma_i, lookup_is_write_i, lookup_addr_i[31:0] -> lookup_hit_o
// MMIO performance counters (read by RISC-V firmware at 0x1A00_0000):
//   0x00 HITS_LO     (RO)
//   0x04 MISSES_LO   (RO)
//   0x08 DMA_SNOOPS  (RO: coherent invalidations triggered by DMA writes)
//   0x0C CTRL        (W: bit0=flush_cache)
module l2_cache_rtl (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        lookup_valid_i,
    input  logic        lookup_is_dma_i,
    input  logic        lookup_is_write_i,
    input  logic [31:0] lookup_addr_i,
    output logic        lookup_hit_o,
    input  logic        mmio_valid_i,
    input  logic        mmio_write_i,
    input  logic [7:0]  mmio_addr_i,
    input  logic [31:0] mmio_wdata_i,
    output logic [31:0] mmio_rdata_o,
    output logic        mmio_err_o,
    output logic [63:0] rtl_cycles_o
);
    // 64 lines x 64-byte cacheline (bits [5:0]=offset, [11:6]=index, [31:12]=tag)
    logic [19:0] tag_q   [0:63];
    logic [63:0] valid_q;
    logic [31:0] hits_q, misses_q, snoops_q;
    logic [63:0] cycle_cnt_q;

    logic [5:0]  idx_w;
    logic [19:0] tag_w;
    logic        is_hit_w;

    assign idx_w        = lookup_addr_i[11:6];
    assign tag_w        = lookup_addr_i[31:12];
    assign is_hit_w     = valid_q[idx_w] && (tag_q[idx_w] == tag_w);
    assign lookup_hit_o = is_hit_w;
    assign rtl_cycles_o = cycle_cnt_q;

    always_comb begin
        mmio_rdata_o = 32'd0;
        mmio_err_o   = 1'b0;
        if (mmio_valid_i) begin
            if (mmio_write_i) begin
                mmio_err_o = (mmio_addr_i != 8'h0C);
            end else begin
                case (mmio_addr_i)
                    8'h00: mmio_rdata_o = hits_q;
                    8'h04: mmio_rdata_o = misses_q;
                    8'h08: mmio_rdata_o = snoops_q;
                    8'h0C: mmio_rdata_o = 32'd64;  // line count
                    default: mmio_err_o = 1'b1;
                endcase
            end
        end
    end

    integer i;
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            valid_q     <= 64'd0;
            hits_q      <= 32'd0;
            misses_q    <= 32'd0;
            snoops_q    <= 32'd0;
            cycle_cnt_q <= 64'd0;
            for (i = 0; i < 64; i = i + 1) tag_q[i] <= 20'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            if (mmio_valid_i && mmio_write_i && mmio_addr_i == 8'h0C && mmio_wdata_i[0]) begin
                valid_q <= 64'd0;
            end else if (lookup_valid_i) begin
                if (lookup_is_dma_i) begin
                    // Coherent DMA snoop: invalidate matching line on DMA write
                    if (lookup_is_write_i && is_hit_w) begin
                        valid_q[idx_w] <= 1'b0;
                    end
                    snoops_q <= snoops_q + 32'd1;
                end else begin
                    if (is_hit_w) begin
                        hits_q <= hits_q + 32'd1;
                    end else begin
                        misses_q       <= misses_q + 32'd1;
                        valid_q[idx_w] <= 1'b1;
                        tag_q[idx_w]   <= tag_w;
                    end
                end
            end
        end
    end
endmodule
