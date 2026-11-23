// SPDX-License-Identifier: Apache-2.0
// Single Unified Multi-Platform Heterogeneous SoC Emulator over SystemC/TLM-2.0 2x2 Mesh NoC.
//
// Hybrid Fidelity Top-Level (`sc_main`):
//   - Router (0,0): RV64IM_Zicsr CPU Initiator (M-mode PMP CSRs + traps + optional GDB RSP stub),
//                   TlmMemory (main DRAM, boot ROM), TlmFinisher
//   - Router (1,0): QEMU-compatible NS16550 UART, CLINT, PLIC (IRQ 10/12/13/14/20/21)
//   - Router (0,1): Chipyard GCD TLM-2.0 (IRQ 12), OpenTitan GPIO TLM-2.0 (IRQ 13),
//                   2nd-Master DMA Engine TLM-2.0 (IRQ 14, drives NoC2x2Router::dma_in)
//   - Router (1,1): MAC NPU-lite (Verilator-compiled SystemVerilog mac_npu_rtl.sv OR TLM-2.0,
//                   IRQ 20), SystemC PMP/IOPMP Bus Guard (IRQ 21, enforces CPU & DMA writes)
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_initiator_socket.h>
#include <tlm_utils/simple_target_socket.h>

#ifdef USOC_ENABLE_VERILATOR_MAC_NPU
#include "Vmac_npu_rtl.h"
#include "verilated.h"
#endif

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "unified_soc_map.h"

namespace {

inline uint32_t load_le32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

inline uint64_t load_le64(const uint8_t *p) {
    return static_cast<uint64_t>(load_le32(p)) | (static_cast<uint64_t>(load_le32(p + 4)) << 32);
}

inline int64_t sext(uint64_t value, unsigned bits) {
    const uint64_t shift = 64u - bits;
    return static_cast<int64_t>(value << shift) >> shift;
}

inline uint32_t euclidean_gcd(uint32_t a, uint32_t b) {
    while (b != 0u) {
        uint32_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

// --- Target 1: Byte-addressable TLM-2.0 Memory (DRAM / ROM) ---
struct TlmMemory : sc_core::sc_module {
    tlm_utils::simple_target_socket<TlmMemory> target;
    std::vector<uint8_t> mem;
    bool read_only;

    TlmMemory(sc_core::sc_module_name name, size_t size_bytes, bool ro)
        : sc_module(name), target("target"), mem(size_bytes, 0u), read_only(ro) {
        target.register_b_transport(this, &TlmMemory::transport);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        const unsigned len = tx.get_data_length();
        if (len == 0u || off + len > mem.size()) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        if (tx.is_write()) {
            if (read_only) {
                tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                return;
            }
            std::memcpy(mem.data() + off, tx.get_data_ptr(), len);
        } else {
            std::memcpy(tx.get_data_ptr(), mem.data() + off, len);
        }
        delay += sc_core::sc_time(2, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 2: SiFive/QEMU-compatible Test Finisher ---
struct TlmFinisher : sc_core::sc_module {
    tlm_utils::simple_target_socket<TlmFinisher> target;
    bool halted = false;
    int exit_code = 0;
    uint32_t raw_value = 0;

    explicit TlmFinisher(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &TlmFinisher::transport);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_address() != 0u || tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        uint32_t val = 0;
        if (tx.is_write()) {
            std::memcpy(&val, tx.get_data_ptr(), 4);
            raw_value = val;
            halted = true;
            if ((val & 0xFFFFu) == 0x5555u) {
                exit_code = 0;
            } else {
                const uint32_t hi = val >> 16;
                exit_code = static_cast<int>(hi ? hi : 1u);
            }
        } else {
            std::memcpy(tx.get_data_ptr(), &raw_value, 4);
        }
        delay += sc_core::sc_time(5, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 3: QEMU-compatible NS16550 UART ---
struct Uart16550Tlm : sc_core::sc_module {
    tlm_utils::simple_target_socket<Uart16550Tlm> target;
    std::ofstream console;
    std::array<uint8_t, 8> regs{};
    uint64_t tx_bytes = 0;
    uint32_t irq = 0;

    Uart16550Tlm(sc_core::sc_module_name name, const std::string &console_path)
        : sc_module(name), target("target"), console(console_path, std::ios::binary) {
        target.register_b_transport(this, &Uart16550Tlm::transport);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        const unsigned len = tx.get_data_length();
        if (off >= 8u || (len != 1u && len != 4u)) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        if (tx.is_write()) {
            const uint8_t b = tx.get_data_ptr()[0];
            if (off == 0u && !(regs[3] & 0x80u)) {
                console.put(static_cast<char>(b));
                console.flush();
                tx_bytes++;
            } else {
                regs[off] = b;
            }
        } else {
            uint8_t out = (off == 5u) ? 0x60u : regs[off];
            std::memset(tx.get_data_ptr(), 0, len);
            tx.get_data_ptr()[0] = out;
        }
        delay += sc_core::sc_time(5, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 4: RISC-V CLINT ---
struct ClintTlm : sc_core::sc_module {
    tlm_utils::simple_target_socket<ClintTlm> target;
    uint64_t mtimecmp = UINT64_MAX;
    uint32_t msip = 0;

    explicit ClintTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &ClintTlm::transport);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        const unsigned len = tx.get_data_length();
        if (off == 0xBFF8u && len == 8u && !tx.is_write()) {
            uint64_t ticks = sc_core::sc_time_stamp().value() / 100000ULL + 1ULL;
            std::memcpy(tx.get_data_ptr(), &ticks, 8);
        } else if (off == 0x4000u && len == 8u) {
            if (tx.is_write()) {
                std::memcpy(&mtimecmp, tx.get_data_ptr(), 8);
            } else {
                std::memcpy(tx.get_data_ptr(), &mtimecmp, 8);
            }
        } else if (off == 0u && len == 4u) {
            if (tx.is_write()) {
                std::memcpy(&msip, tx.get_data_ptr(), 4);
            } else {
                std::memcpy(tx.get_data_ptr(), &msip, 4);
            }
        } else {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        delay += sc_core::sc_time(5, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 5: Chipyard GCD TLM-2.0 Model ---
struct ChipyardGcdTlm : sc_core::sc_module {
    tlm_utils::simple_target_socket<ChipyardGcdTlm> target;
    uint32_t x_reg = 0;
    uint32_t result_reg = 0;
    bool input_ready = true;
    bool output_valid = false;
    uint32_t irq = 0;
    uint64_t ops_completed = 0;

    explicit ChipyardGcdTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &ChipyardGcdTlm::transport);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        if (tx.is_write()) {
            if (off == 0x04u && input_ready) {
                x_reg = data;
            } else if (off == 0x08u && input_ready && x_reg != 0u && data != 0u) {
                result_reg = euclidean_gcd(x_reg, data);
                input_ready = false;
                output_valid = true;
                irq = 1u;
                ops_completed++;
                delay += sc_core::sc_time(40, sc_core::SC_NS);
            } else {
                tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                return;
            }
        } else {
            if (off == 0x00u) {
                data = (input_ready ? 2u : 0u) | (output_valid ? 1u : 0u);
            } else if (off == 0x0Cu && output_valid) {
                data = result_reg;
                output_valid = false;
                input_ready = true;
                irq = 0u;
            } else {
                tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 6: OpenTitan GPIO TLM-2.0 Model ---
struct OpenTitanGpioTlm : sc_core::sc_module {
    tlm_utils::simple_target_socket<OpenTitanGpioTlm> target;
    uint32_t intr_state = 0;
    uint32_t intr_enable = 0;
    uint32_t direct_out = 0;
    uint32_t direct_oe = 0;
    uint32_t intr_ctrl_en_rising = 0;
    uint32_t data_in = 0;
    uint32_t irq = 0;

    explicit OpenTitanGpioTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &OpenTitanGpioTlm::transport);
    }

    void update_pins() {
        const uint32_t new_in = direct_out & direct_oe;
        const uint32_t rising = new_in & (~data_in);
        data_in = new_in;
        intr_state |= (rising & intr_ctrl_en_rising);
        irq = (intr_state & intr_enable) ? 1u : 0u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        if (tx.is_write()) {
            switch (off) {
                case 0x00u:
                    intr_state &= ~data;
                    irq = (intr_state & intr_enable) ? 1u : 0u;
                    break;
                case 0x04u:
                    intr_enable = data;
                    irq = (intr_state & intr_enable) ? 1u : 0u;
                    break;
                case 0x14u:
                    direct_out = data;
                    update_pins();
                    break;
                case 0x18u: {
                    const uint32_t mask = (data >> 16) & 0xFFFFu;
                    const uint32_t val = data & 0xFFFFu;
                    direct_out = (direct_out & ~mask) | (val & mask);
                    update_pins();
                    break;
                }
                case 0x20u:
                    direct_oe = data;
                    update_pins();
                    break;
                case 0x2Cu:
                    intr_ctrl_en_rising = data;
                    break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
        } else {
            switch (off) {
                case 0x00u: data = intr_state; break;
                case 0x04u: data = intr_enable; break;
                case 0x10u: data = data_in; break;
                case 0x14u: data = direct_out; break;
                case 0x20u: data = direct_oe; break;
                case 0x2Cu: data = intr_ctrl_en_rising; break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 7: MAC NPU-Lite (Verilator-compiled SystemVerilog RTL OR TLM-2.0) ---
#ifdef USOC_ENABLE_VERILATOR_MAC_NPU
struct MacNpuModule : sc_core::sc_module {
    tlm_utils::simple_target_socket<MacNpuModule> target;
    Vmac_npu_rtl dut;
    uint32_t acc = 0;
    uint32_t ops_done = 0;
    uint32_t irq = 0;
    uint64_t rtl_cycles = 0;

    explicit MacNpuModule(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &MacNpuModule::transport);
        dut.clk_i = 0;
        dut.rst_ni = 0;
        dut.req_valid_i = 0;
        dut.req_write_i = 0;
        dut.req_addr_i = 0;
        dut.req_wdata_i = 0;
        tick();
        dut.rst_ni = 1;
        tick();
    }

    void tick() {
        dut.clk_i = 0;
        dut.eval();
        dut.clk_i = 1;
        dut.eval();
        dut.clk_i = 0;
        dut.eval();
        irq = dut.irq_o ? 1u : 0u;
        rtl_cycles = dut.rtl_cycles_o;
    }

    uint32_t peek_reg(uint8_t addr) {
        dut.req_valid_i = 1;
        dut.req_write_i = 0;
        dut.req_addr_i = addr;
        dut.req_wdata_i = 0;
        dut.eval();
        uint32_t v = dut.rsp_rdata_o;
        dut.req_valid_i = 0;
        dut.eval();
        return v;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        if (off > 0xFFu) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);

        dut.req_valid_i = 1;
        dut.req_write_i = tx.is_write() ? 1 : 0;
        dut.req_addr_i = static_cast<uint8_t>(off);
        dut.req_wdata_i = data;
        dut.eval();

        if (dut.rsp_err_o) {
            dut.req_valid_i = 0;
            dut.eval();
            tx.set_response_status(tx.is_write() ? tlm::TLM_COMMAND_ERROR_RESPONSE
                                                 : tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }

        if (!tx.is_write()) {
            uint32_t rdata = dut.rsp_rdata_o;
            std::memcpy(tx.get_data_ptr(), &rdata, 4);
            tick();
            dut.req_valid_i = 0;
            dut.eval();
            delay += sc_core::sc_time(20, sc_core::SC_NS);
        } else {
            tick();
            dut.req_valid_i = 0;
            dut.eval();
            // Advance 1 extra clock cycle for MAC trigger to model synchronous pipeline latency.
            if (off == 0x00u && data == 1u) {
                tick();
                delay += sc_core::sc_time(40, sc_core::SC_NS);
            } else {
                delay += sc_core::sc_time(20, sc_core::SC_NS);
            }
        }
        ops_done = peek_reg(0x00u);
        acc = peek_reg(0x0Cu);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }

    static const char *backend_name() { return "verilator-rtl"; }
};
#else
struct MacNpuModule : sc_core::sc_module {
    tlm_utils::simple_target_socket<MacNpuModule> target;
    uint32_t op_a = 0;
    uint32_t op_b = 0;
    uint32_t acc = 0;
    uint32_t ops_done = 0;
    uint32_t irq = 0;
    uint64_t rtl_cycles = 0;

    explicit MacNpuModule(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &MacNpuModule::transport);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        if (tx.is_write()) {
            switch (off) {
                case 0x00u:
                    if (data == 1u) {
                        acc = static_cast<uint32_t>(acc + op_a * op_b);
                        ops_done++;
                        irq = 1u;
                        delay += sc_core::sc_time(25, sc_core::SC_NS);
                    } else if (data == 2u) {
                        acc = 0u;
                        ops_done = 0u;
                        irq = 0u;
                    } else {
                        tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                        return;
                    }
                    break;
                case 0x04u: op_a = data; break;
                case 0x08u: op_b = data; break;
                case 0x0Cu:
                    tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                    return;
                case 0x10u:
                    if (data & 1u) irq = 0u;
                    break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
        } else {
            switch (off) {
                case 0x00u: data = ops_done; break;
                case 0x04u: data = op_a; break;
                case 0x08u: data = op_b; break;
                case 0x0Cu: data = acc; break;
                case 0x10u: data = irq; break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }

    static const char *backend_name() { return "systemc-tlm"; }
};
#endif

// --- Target 8: SystemC PMP/IOPMP Bus Guard (enforces BOTH CPU and DMA writes on NoC) ---
struct PmpBusGuardTlm : sc_core::sc_module {
    tlm_utils::simple_target_socket<PmpBusGuardTlm> target;
    uint32_t ctrl = 0;
    uint32_t base_lo = 0;
    uint32_t limit_lo = 0;
    uint32_t fault_addr = 0;
    uint32_t fault_count = 0;
    uint32_t irq = 0;

    explicit PmpBusGuardTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &PmpBusGuardTlm::transport);
    }

    bool allow_write(uint64_t phys_addr) {
        if ((ctrl & 1u) && (ctrl & 2u)) {
            if (phys_addr >= static_cast<uint64_t>(base_lo) &&
                phys_addr < static_cast<uint64_t>(limit_lo)) {
                fault_addr = static_cast<uint32_t>(phys_addr);
                fault_count++;
                irq = 1u;
                return false;
            }
        }
        return true;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        if (tx.is_write()) {
            const bool locked = (ctrl & 4u) != 0u;
            switch (off) {
                case 0x00u: if (!locked) ctrl = data; break;
                case 0x04u: if (!locked) base_lo = data; break;
                case 0x08u: if (!locked) limit_lo = data; break;
                case 0x10u: if (data & 1u) irq = 0u; break;
                default:
                    tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                    return;
            }
        } else {
            switch (off) {
                case 0x00u: data = ctrl; break;
                case 0x04u: data = base_lo; break;
                case 0x08u: data = limit_lo; break;
                case 0x0Cu: data = fault_addr; break;
                case 0x10u: data = irq; break;
                case 0x14u: data = fault_count; break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 9 + Second Bus Master: NoC DMA Engine (IRQ 14) ---
struct DmaEngineTlm : sc_core::sc_module {
    tlm_utils::simple_target_socket<DmaEngineTlm> target;
    tlm_utils::simple_initiator_socket<DmaEngineTlm> dma_master;
    uint32_t src_lo = 0;
    uint32_t dst_lo = 0;
    uint32_t len_bytes = 0;
    uint32_t status = 0;  // bit0=done, bit1=bus_error
    uint32_t irq = 0;
    uint64_t bytes_transferred = 0;
    uint64_t bus_errors = 0;

    explicit DmaEngineTlm(sc_core::sc_module_name name)
        : sc_module(name), target("target"), dma_master("dma_master") {
        target.register_b_transport(this, &DmaEngineTlm::transport);
    }

    void execute_transfer(sc_core::sc_time &delay) {
        status = 0u;
        bool had_err = false;
        const uint32_t words = len_bytes / 4u;
        for (uint32_t i = 0; i < words; ++i) {
            uint32_t word = 0;
            tlm::tlm_generic_payload rtx;
            rtx.set_command(tlm::TLM_READ_COMMAND);
            rtx.set_address(static_cast<uint64_t>(src_lo) + i * 4ULL);
            rtx.set_data_ptr(reinterpret_cast<unsigned char *>(&word));
            rtx.set_data_length(4u);
            rtx.set_streaming_width(4u);
            dma_master->b_transport(rtx, delay);
            if (!rtx.is_response_ok()) {
                had_err = true;
                bus_errors++;
                break;
            }

            tlm::tlm_generic_payload wtx;
            wtx.set_command(tlm::TLM_WRITE_COMMAND);
            wtx.set_address(static_cast<uint64_t>(dst_lo) + i * 4ULL);
            wtx.set_data_ptr(reinterpret_cast<unsigned char *>(&word));
            wtx.set_data_length(4u);
            wtx.set_streaming_width(4u);
            dma_master->b_transport(wtx, delay);
            if (!wtx.is_response_ok()) {
                had_err = true;
                bus_errors++;
                break;
            }
            bytes_transferred += 4ULL;
        }
        status = had_err ? 3u : 1u;
        irq = 1u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        if (tx.is_write()) {
            switch (off) {
                case 0x00u:
                    if (data == 1u) {
                        execute_transfer(delay);
                    }
                    break;
                case 0x04u: src_lo = data; break;
                case 0x08u: dst_lo = data; break;
                case 0x0Cu: len_bytes = data; break;
                case 0x14u: if (data & 1u) irq = 0u; break;
                default:
                    tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                    return;
            }
        } else {
            switch (off) {
                case 0x04u: data = src_lo; break;
                case 0x08u: data = dst_lo; break;
                case 0x0Cu: data = len_bytes; break;
                case 0x10u: data = status; break;
                case 0x14u: data = irq; break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Target 10: Unified PLIC ---
struct PlicTlm : sc_core::sc_module {
    tlm_utils::simple_target_socket<PlicTlm> target;
    std::array<uint32_t, 32> priority{};
    uint32_t enable_mask = 0;
    uint32_t threshold = 0;
    uint32_t claimed_mask = 0;

    Uart16550Tlm *uart = nullptr;
    ChipyardGcdTlm *gcd = nullptr;
    OpenTitanGpioTlm *gpio = nullptr;
    DmaEngineTlm *dma = nullptr;
    MacNpuModule *npu = nullptr;
    PmpBusGuardTlm *pmp_guard = nullptr;

    explicit PlicTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &PlicTlm::transport);
    }

    uint32_t active_source_mask() const {
        uint32_t m = 0;
        if (uart && uart->irq) m |= (1u << USOC_QEMU_UART_0_IRQ);
        if (gcd && gcd->irq) m |= (1u << USOC_CHIPYARD_GCD_0_IRQ);
        if (gpio && gpio->irq) m |= (1u << USOC_OPENTITAN_GPIO_0_IRQ);
        if (dma && dma->irq) m |= (1u << USOC_DMA_ENGINE_0_IRQ);
        if (npu && npu->irq) m |= (1u << USOC_MAC_NPU_0_IRQ);
        if (pmp_guard && pmp_guard->irq) m |= (1u << USOC_PMP_GUARD_0_IRQ);
        return m;
    }

    uint32_t highest_pending_irq() const {
        const uint32_t pending = active_source_mask() & enable_mask & (~claimed_mask);
        uint32_t best_id = 0;
        uint32_t best_prio = threshold;
        for (uint32_t id = 1u; id < 32u; ++id) {
            if ((pending & (1u << id)) && priority[id] > best_prio) {
                best_prio = priority[id];
                best_id = id;
            }
        }
        return best_id;
    }

    bool has_pending_irq() const { return highest_pending_irq() != 0u; }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        if (tx.is_write()) {
            if (off < 128u && (off % 4u) == 0u) {
                priority[off / 4u] = data;
            } else if (off == 0x2000u) {
                enable_mask = data;
            } else if (off == 0x200000u) {
                threshold = data;
            } else if (off == 0x200004u) {
                if (data < 32u) claimed_mask &= ~(1u << data);
            } else {
                tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                return;
            }
        } else {
            if (off < 128u && (off % 4u) == 0u) {
                data = priority[off / 4u];
            } else if (off == 0x2000u) {
                data = enable_mask;
            } else if (off == 0x200000u) {
                data = threshold;
            } else if (off == 0x200004u) {
                data = highest_pending_irq();
                if (data != 0u) claimed_mask |= (1u << data);
            } else {
                tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4);
        }
        delay += sc_core::sc_time(5, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Interconnect: SystemC/TLM-2.0 2x2 Mesh NoC Router (2 Masters: CPU + DMA) ---
struct NoC2x2Router : sc_core::sc_module {
    tlm_utils::simple_target_socket<NoC2x2Router> cpu_in;
    tlm_utils::simple_target_socket<NoC2x2Router> dma_in;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_ram;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_rom;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_finisher;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_clint;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_plic;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_uart;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_gcd;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_gpio;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_dma;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_npu;
    tlm_utils::simple_initiator_socket<NoC2x2Router> to_pmp_guard;

    PmpBusGuardTlm *pmp_guard = nullptr;
    std::ofstream trace;
    uint64_t total_transactions = 0;
    uint64_t total_hops = 0;
    uint64_t mmio_transactions = 0;
    uint64_t dma_master_transactions = 0;
    std::array<uint64_t, 4> router_hits{};
    uint64_t hit_uart = 0, hit_clint = 0, hit_plic = 0, hit_gcd = 0;
    uint64_t hit_gpio = 0, hit_dma = 0, hit_npu = 0, hit_pmp_guard = 0, hit_pmp_blocked = 0;

    NoC2x2Router(sc_core::sc_module_name name, const std::string &trace_path)
        : sc_module(name),
          cpu_in("cpu_in"),
          dma_in("dma_in"),
          to_ram("to_ram"),
          to_rom("to_rom"),
          to_finisher("to_finisher"),
          to_clint("to_clint"),
          to_plic("to_plic"),
          to_uart("to_uart"),
          to_gcd("to_gcd"),
          to_gpio("to_gpio"),
          to_dma("to_dma"),
          to_npu("to_npu"),
          to_pmp_guard("to_pmp_guard"),
          trace(trace_path) {
        cpu_in.register_b_transport(this, &NoC2x2Router::cpu_transport);
        dma_in.register_b_transport(this, &NoC2x2Router::dma_transport);
    }

    static unsigned manhattan_hops(int sx, int sy, int dx, int dy) {
        return static_cast<unsigned>(std::abs(dx - sx) + std::abs(dy - sy));
    }

    void log_tx(const char *master, const char *target, int rx, int ry, unsigned hops,
                uint64_t phys_addr, uint64_t off, bool is_write, uint32_t value, bool ok) {
        mmio_transactions++;
        trace << "{\"master\":\"" << master << "\",\"target\":\"" << target << "\",\"router\":["
              << rx << "," << ry << "],\"hops\":" << hops << ",\"addr\":" << phys_addr
              << ",\"offset\":" << off << ",\"op\":\"" << (is_write ? "W" : "R")
              << "\",\"value\":" << value << ",\"status\":\"" << (ok ? "OK" : "ERROR")
              << "\",\"systemc_time_ps\":" << sc_core::sc_time_stamp().value() << "}\n";
    }

    void dispatch(const char *master, int sx, int sy,
                  tlm_utils::simple_initiator_socket<NoC2x2Router> &sock, const char *target_name,
                  uint64_t base, int rx, int ry, bool log_it, tlm::tlm_generic_payload &tx,
                  sc_core::sc_time &delay) {
        const uint64_t phys_addr = tx.get_address();
        const uint64_t off = phys_addr - base;
        const unsigned hops = manhattan_hops(sx, sy, rx, ry);
        const unsigned router_idx = static_cast<unsigned>(ry * 2 + rx);
        total_transactions++;
        total_hops += hops;
        if (router_idx < 4u) router_hits[router_idx]++;
        delay += sc_core::sc_time(
            static_cast<double>(hops * USOC_NOC_HOP_NS + USOC_NOC_LINK_NS), sc_core::SC_NS);

        tx.set_address(off);
        sock->b_transport(tx, delay);
        tx.set_address(phys_addr);

        if (log_it) {
            uint32_t sample = 0;
            const unsigned copy_len = tx.get_data_length() < 4u ? tx.get_data_length() : 4u;
            std::memcpy(&sample, tx.get_data_ptr(), copy_len);
            log_tx(master, target_name, rx, ry, hops, phys_addr, off, tx.is_write(), sample,
                   tx.is_response_ok());
        }
    }

    void route(const char *master, int sx, int sy, tlm::tlm_generic_payload &tx,
               sc_core::sc_time &delay, bool log_ram) {
        const uint64_t addr = tx.get_address();
        if (addr >= USOC_MAIN_DRAM_BASE && addr < USOC_MAIN_DRAM_BASE + USOC_MAIN_DRAM_SIZE) {
            if (tx.is_write() && pmp_guard && !pmp_guard->allow_write(addr)) {
                hit_pmp_blocked++;
                delay += sc_core::sc_time(USOC_NOC_HOP_NS, sc_core::SC_NS);
                tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                log_tx(master, "pmp_guard_block", USOC_PMP_GUARD_0_RX, USOC_PMP_GUARD_0_RY,
                       manhattan_hops(sx, sy, USOC_PMP_GUARD_0_RX, USOC_PMP_GUARD_0_RY), addr, 0u,
                       true, 0u, false);
                return;
            }
            dispatch(master, sx, sy, to_ram, "main_dram", USOC_MAIN_DRAM_BASE, USOC_MAIN_DRAM_RX,
                     USOC_MAIN_DRAM_RY, log_ram, tx, delay);
        } else if (addr >= USOC_BOOT_ROM_BASE && addr < USOC_BOOT_ROM_BASE + USOC_BOOT_ROM_SIZE) {
            dispatch(master, sx, sy, to_rom, "boot_rom", USOC_BOOT_ROM_BASE, USOC_BOOT_ROM_RX,
                     USOC_BOOT_ROM_RY, false, tx, delay);
        } else if (addr >= USOC_SIM_FINISHER_BASE &&
                   addr < USOC_SIM_FINISHER_BASE + USOC_SIM_FINISHER_SIZE) {
            dispatch(master, sx, sy, to_finisher, "sim_finisher", USOC_SIM_FINISHER_BASE,
                     USOC_SIM_FINISHER_RX, USOC_SIM_FINISHER_RY, true, tx, delay);
        } else if (addr >= USOC_SYS_CLINT_BASE &&
                   addr < USOC_SYS_CLINT_BASE + USOC_SYS_CLINT_SIZE) {
            hit_clint++;
            dispatch(master, sx, sy, to_clint, "sys_clint", USOC_SYS_CLINT_BASE, USOC_SYS_CLINT_RX,
                     USOC_SYS_CLINT_RY, true, tx, delay);
        } else if (addr >= USOC_SYS_PLIC_BASE && addr < USOC_SYS_PLIC_BASE + USOC_SYS_PLIC_SIZE) {
            hit_plic++;
            dispatch(master, sx, sy, to_plic, "sys_plic", USOC_SYS_PLIC_BASE, USOC_SYS_PLIC_RX,
                     USOC_SYS_PLIC_RY, true, tx, delay);
        } else if (addr >= USOC_QEMU_UART_0_BASE &&
                   addr < USOC_QEMU_UART_0_BASE + USOC_QEMU_UART_0_SIZE) {
            hit_uart++;
            dispatch(master, sx, sy, to_uart, "qemu_uart_0", USOC_QEMU_UART_0_BASE,
                     USOC_QEMU_UART_0_RX, USOC_QEMU_UART_0_RY, true, tx, delay);
        } else if (addr >= USOC_CHIPYARD_GCD_0_BASE &&
                   addr < USOC_CHIPYARD_GCD_0_BASE + USOC_CHIPYARD_GCD_0_SIZE) {
            hit_gcd++;
            dispatch(master, sx, sy, to_gcd, "chipyard_gcd_0", USOC_CHIPYARD_GCD_0_BASE,
                     USOC_CHIPYARD_GCD_0_RX, USOC_CHIPYARD_GCD_0_RY, true, tx, delay);
        } else if (addr >= USOC_OPENTITAN_GPIO_0_BASE &&
                   addr < USOC_OPENTITAN_GPIO_0_BASE + USOC_OPENTITAN_GPIO_0_SIZE) {
            hit_gpio++;
            dispatch(master, sx, sy, to_gpio, "opentitan_gpio_0", USOC_OPENTITAN_GPIO_0_BASE,
                     USOC_OPENTITAN_GPIO_0_RX, USOC_OPENTITAN_GPIO_0_RY, true, tx, delay);
        } else if (addr >= USOC_DMA_ENGINE_0_BASE &&
                   addr < USOC_DMA_ENGINE_0_BASE + USOC_DMA_ENGINE_0_SIZE) {
            hit_dma++;
            dispatch(master, sx, sy, to_dma, "dma_engine_0", USOC_DMA_ENGINE_0_BASE,
                     USOC_DMA_ENGINE_0_RX, USOC_DMA_ENGINE_0_RY, true, tx, delay);
        } else if (addr >= USOC_MAC_NPU_0_BASE &&
                   addr < USOC_MAC_NPU_0_BASE + USOC_MAC_NPU_0_SIZE) {
            hit_npu++;
            dispatch(master, sx, sy, to_npu, "mac_npu_0", USOC_MAC_NPU_0_BASE, USOC_MAC_NPU_0_RX,
                     USOC_MAC_NPU_0_RY, true, tx, delay);
        } else if (addr >= USOC_PMP_GUARD_0_BASE &&
                   addr < USOC_PMP_GUARD_0_BASE + USOC_PMP_GUARD_0_SIZE) {
            hit_pmp_guard++;
            dispatch(master, sx, sy, to_pmp_guard, "pmp_guard_0", USOC_PMP_GUARD_0_BASE,
                     USOC_PMP_GUARD_0_RX, USOC_PMP_GUARD_0_RY, true, tx, delay);
        } else {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            log_tx(master, "unmapped", 0, 0, 0, addr, 0, tx.is_write(), 0, false);
        }
    }

    void cpu_transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        route("cpu", USOC_CPU_RX, USOC_CPU_RY, tx, delay, false);
    }

    void dma_transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        dma_master_transactions++;
        route("dma", USOC_DMA_ENGINE_0_RX, USOC_DMA_ENGINE_0_RY, tx, delay, true);
    }
};

// --- Minimal GDB Remote Serial Protocol (RSP) Stub Helper ---
struct GdbStub {
    int server_fd = -1;
    int client_fd = -1;

    bool listen_on(int port, const std::string &port_file) {
        server_fd = socket(AF_INET, SOCK_STREAM, 0);
        if (server_fd < 0) return false;
        int opt = 1;
        setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(static_cast<uint16_t>(port));
        if (bind(server_fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0 ||
            listen(server_fd, 1) != 0) {
            close(server_fd);
            server_fd = -1;
            return false;
        }
        socklen_t alen = sizeof(addr);
        if (getsockname(server_fd, reinterpret_cast<sockaddr *>(&addr), &alen) == 0 &&
            !port_file.empty()) {
            std::ofstream pf(port_file);
            pf << ntohs(addr.sin_port) << "\n";
        }
        client_fd = accept(server_fd, nullptr, nullptr);
        return client_fd >= 0;
    }

    bool recv_packet(std::string &out) {
        out.clear();
        char ch = 0;
        while (true) {
            ssize_t n = recv(client_fd, &ch, 1, 0);
            if (n <= 0) return false;
            if (ch == '$') break;
        }
        while (true) {
            ssize_t n = recv(client_fd, &ch, 1, 0);
            if (n <= 0) return false;
            if (ch == '#') break;
            out.push_back(ch);
        }
        char csum[2];
        if (recv(client_fd, csum, 2, MSG_WAITALL) != 2) return false;
        const char ack = '+';
        if (send(client_fd, &ack, 1, MSG_NOSIGNAL) != 1) return false;
        return true;
    }

    void send_packet(const std::string &payload) {
        unsigned sum = 0;
        for (unsigned char c : payload) sum = (sum + c) & 0xFFu;
        char tail[4];
        std::snprintf(tail, sizeof(tail), "#%02x", sum);
        std::string wire = "$" + payload + tail;
        (void)send(client_fd, wire.data(), wire.size(), MSG_NOSIGNAL);
    }

    static std::string to_hex_le64(uint64_t v) {
        static const char hex[] = "0123456789abcdef";
        std::string out(16, '0');
        for (int i = 0; i < 8; ++i) {
            uint8_t b = static_cast<uint8_t>((v >> (i * 8)) & 0xFFu);
            out[static_cast<size_t>(i * 2)] = hex[b >> 4];
            out[static_cast<size_t>(i * 2 + 1)] = hex[b & 0xFu];
        }
        return out;
    }

    void cleanup() {
        if (client_fd >= 0) close(client_fd);
        if (server_fd >= 0) close(server_fd);
        client_fd = server_fd = -1;
    }
};

// --- Initiator: RV64IM_Zicsr SystemC/TLM-2.0 CPU Core with M-mode PMP CSRs & GDB RSP ---
struct Rv64CpuInitiator : sc_core::sc_module {
    tlm_utils::simple_initiator_socket<Rv64CpuInitiator> bus;
    std::array<uint64_t, 32> x{};
    uint64_t pc = USOC_CPU_ENTRY;
    uint64_t mstatus = 0, mie = 0, mtvec = 0, mscratch = 0, mepc = 0, mcause = 0, mtval = 0;
    uint64_t pmpcfg0 = 0;
    std::array<uint64_t, 4> pmpaddr{};
    uint64_t instructions = 0;
    uint64_t traps_taken = 0;
    bool timed_out = false;
    bool illegal_trap = false;

    TlmFinisher *finisher = nullptr;
    PlicTlm *plic = nullptr;
    GdbStub *gdb = nullptr;
    uint64_t max_steps = 500000ULL;

    SC_HAS_PROCESS(Rv64CpuInitiator);

    explicit Rv64CpuInitiator(sc_core::sc_module_name name) : sc_module(name), bus("bus") {
        SC_THREAD(run);
    }

    bool bus_rw(uint64_t addr, void *ptr, unsigned len, bool is_write) {
        tlm::tlm_generic_payload tx;
        tx.set_command(is_write ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
        tx.set_address(addr);
        tx.set_data_ptr(static_cast<unsigned char *>(ptr));
        tx.set_data_length(len);
        tx.set_streaming_width(len);
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
        bus->b_transport(tx, delay);
        if (delay > sc_core::SC_ZERO_TIME) {
            wait(delay);
        }
        return tx.is_response_ok();
    }

    uint64_t read_csr(uint32_t csr) const {
        switch (csr) {
            case 0x300u: return mstatus;
            case 0x301u: return 0x8000000000001100ULL;
            case 0x304u: return mie;
            case 0x305u: return mtvec;
            case 0x340u: return mscratch;
            case 0x341u: return mepc;
            case 0x342u: return mcause;
            case 0x343u: return mtval;
            case 0x344u: return (plic && plic->has_pending_irq()) ? (1ULL << 11) : 0ULL;
            case 0x3A0u: return pmpcfg0;
            case 0x3B0u: return pmpaddr[0];
            case 0x3B1u: return pmpaddr[1];
            case 0x3B2u: return pmpaddr[2];
            case 0x3B3u: return pmpaddr[3];
            case 0xB00u: return instructions;
            case 0xB02u: return instructions;
            case 0xF14u: return 0ULL;
            default: return 0ULL;
        }
    }

    void write_csr(uint32_t csr, uint64_t val) {
        switch (csr) {
            case 0x300u: mstatus = val; break;
            case 0x304u: mie = val; break;
            case 0x305u: mtvec = val; break;
            case 0x340u: mscratch = val; break;
            case 0x341u: mepc = val; break;
            case 0x342u: mcause = val; break;
            case 0x343u: mtval = val; break;
            case 0x3A0u: pmpcfg0 = val; break;
            case 0x3B0u: pmpaddr[0] = val; break;
            case 0x3B1u: pmpaddr[1] = val; break;
            case 0x3B2u: pmpaddr[2] = val; break;
            case 0x3B3u: pmpaddr[3] = val; break;
            default: break;
        }
    }

    void take_trap(uint64_t cause, uint64_t tval, uint64_t fault_pc) {
        traps_taken++;
        mepc = fault_pc;
        mcause = cause;
        mtval = tval;
        const bool old_mie = (mstatus & 8ULL) != 0ULL;
        mstatus = (mstatus & ~(1ULL << 7)) | (old_mie ? (1ULL << 7) : 0ULL);
        mstatus &= ~8ULL;
        pc = mtvec & ~3ULL;
    }

    bool step_one() {
        if (finisher && finisher->halted) return false;
        if ((mstatus & 8ULL) && (mie & (1ULL << 11)) && plic && plic->has_pending_irq()) {
            take_trap(0x800000000000000bULL, 0ULL, pc);
        }
        uint32_t insn = 0;
        if (!bus_rw(pc, &insn, 4u, false)) {
            take_trap(1ULL, pc, pc);
            return true;
        }
        instructions++;
        const uint64_t cur_pc = pc;
        uint64_t next_pc = pc + 4ULL;

        const uint32_t opcode = insn & 0x7Fu;
        const uint32_t rd = (insn >> 7) & 0x1Fu;
        const uint32_t funct3 = (insn >> 12) & 0x7u;
        const uint32_t rs1 = (insn >> 15) & 0x1Fu;
        const uint32_t rs2 = (insn >> 20) & 0x1Fu;
        const uint32_t funct7 = (insn >> 25) & 0x7Fu;

        bool trapped = false;
        switch (opcode) {
            case 0x37u: x[rd] = static_cast<uint64_t>(sext(insn & 0xFFFFF000u, 32)); break;
            case 0x17u: x[rd] = cur_pc + static_cast<uint64_t>(sext(insn & 0xFFFFF000u, 32)); break;
            case 0x6Fu: {
                const uint64_t imm =
                    (((insn >> 31) & 1u) << 20) | (((insn >> 21) & 0x3FFu) << 1) |
                    (((insn >> 20) & 1u) << 11) | (((insn >> 12) & 0xFFu) << 12);
                x[rd] = next_pc;
                next_pc = cur_pc + static_cast<uint64_t>(sext(imm, 21));
                break;
            }
            case 0x67u: {
                const int64_t imm = sext(insn >> 20, 12);
                const uint64_t target = (x[rs1] + static_cast<uint64_t>(imm)) & ~1ULL;
                x[rd] = next_pc;
                next_pc = target;
                break;
            }
            case 0x63u: {
                const uint64_t imm =
                    (((insn >> 31) & 1u) << 12) | (((insn >> 7) & 1u) << 11) |
                    (((insn >> 25) & 0x3Fu) << 5) | (((insn >> 8) & 0xFu) << 1);
                bool take = false;
                switch (funct3) {
                    case 0u: take = x[rs1] == x[rs2]; break;
                    case 1u: take = x[rs1] != x[rs2]; break;
                    case 4u: take = static_cast<int64_t>(x[rs1]) < static_cast<int64_t>(x[rs2]); break;
                    case 5u: take = static_cast<int64_t>(x[rs1]) >= static_cast<int64_t>(x[rs2]); break;
                    case 6u: take = x[rs1] < x[rs2]; break;
                    case 7u: take = x[rs1] >= x[rs2]; break;
                    default: break;
                }
                if (take) next_pc = cur_pc + static_cast<uint64_t>(sext(imm, 13));
                break;
            }
            case 0x03u: {
                const uint64_t addr = x[rs1] + static_cast<uint64_t>(sext(insn >> 20, 12));
                uint64_t buf = 0;
                const unsigned len = 1u << (funct3 & 3u);
                if (!bus_rw(addr, &buf, len, false)) {
                    take_trap(5ULL, addr, cur_pc);
                    trapped = true;
                    break;
                }
                switch (funct3) {
                    case 0u: x[rd] = static_cast<uint64_t>(sext(buf, 8)); break;
                    case 1u: x[rd] = static_cast<uint64_t>(sext(buf, 16)); break;
                    case 2u: x[rd] = static_cast<uint64_t>(sext(buf, 32)); break;
                    case 3u: x[rd] = buf; break;
                    case 4u: x[rd] = buf & 0xFFULL; break;
                    case 5u: x[rd] = buf & 0xFFFFULL; break;
                    case 6u: x[rd] = buf & 0xFFFFFFFFULL; break;
                    default: break;
                }
                break;
            }
            case 0x23u: {
                const uint64_t imm = (((insn >> 25) & 0x7Fu) << 5) | ((insn >> 7) & 0x1Fu);
                const uint64_t addr = x[rs1] + static_cast<uint64_t>(sext(imm, 12));
                uint64_t val = x[rs2];
                const unsigned len = 1u << (funct3 & 3u);
                if (!bus_rw(addr, &val, len, true)) {
                    take_trap(7ULL, addr, cur_pc);
                    trapped = true;
                }
                break;
            }
            case 0x13u: {
                const int64_t imm = sext(insn >> 20, 12);
                const uint32_t shamt = (insn >> 20) & 0x3Fu;
                switch (funct3) {
                    case 0u: x[rd] = x[rs1] + static_cast<uint64_t>(imm); break;
                    case 1u: x[rd] = x[rs1] << shamt; break;
                    case 2u: x[rd] = (static_cast<int64_t>(x[rs1]) < imm) ? 1ULL : 0ULL; break;
                    case 3u: x[rd] = (x[rs1] < static_cast<uint64_t>(imm)) ? 1ULL : 0ULL; break;
                    case 4u: x[rd] = x[rs1] ^ static_cast<uint64_t>(imm); break;
                    case 5u:
                        x[rd] = (insn & 0x40000000u)
                                    ? static_cast<uint64_t>(static_cast<int64_t>(x[rs1]) >> shamt)
                                    : (x[rs1] >> shamt);
                        break;
                    case 6u: x[rd] = x[rs1] | static_cast<uint64_t>(imm); break;
                    case 7u: x[rd] = x[rs1] & static_cast<uint64_t>(imm); break;
                    default: break;
                }
                break;
            }
            case 0x1Bu: {
                const int32_t imm = static_cast<int32_t>(sext(insn >> 20, 12));
                const uint32_t shamt = (insn >> 20) & 0x1Fu;
                const uint32_t a = static_cast<uint32_t>(x[rs1]);
                uint32_t r = 0;
                switch (funct3) {
                    case 0u: r = a + static_cast<uint32_t>(imm); break;
                    case 1u: r = a << shamt; break;
                    case 5u:
                        r = (insn & 0x40000000u)
                                ? static_cast<uint32_t>(static_cast<int32_t>(a) >> shamt)
                                : (a >> shamt);
                        break;
                    default: break;
                }
                x[rd] = static_cast<uint64_t>(sext(r, 32));
                break;
            }
            case 0x33u: {
                const uint64_t a = x[rs1], b = x[rs2];
                if (funct7 == 1u) {
                    switch (funct3) {
                        case 0u: x[rd] = a * b; break;
                        case 4u: {
                            int64_t sa = static_cast<int64_t>(a), sb = static_cast<int64_t>(b);
                            x[rd] = (sb == 0) ? UINT64_MAX : static_cast<uint64_t>(sa / sb);
                            break;
                        }
                        case 5u: x[rd] = (b == 0u) ? UINT64_MAX : (a / b); break;
                        case 6u: {
                            int64_t sa = static_cast<int64_t>(a), sb = static_cast<int64_t>(b);
                            x[rd] = (sb == 0) ? a : static_cast<uint64_t>(sa % sb);
                            break;
                        }
                        case 7u: x[rd] = (b == 0u) ? a : (a % b); break;
                        default: break;
                    }
                } else {
                    const uint32_t sh = static_cast<uint32_t>(b & 0x3Fu);
                    switch (funct3) {
                        case 0u: x[rd] = (funct7 == 0x20u) ? (a - b) : (a + b); break;
                        case 1u: x[rd] = a << sh; break;
                        case 2u: x[rd] = (static_cast<int64_t>(a) < static_cast<int64_t>(b)) ? 1ULL : 0ULL; break;
                        case 3u: x[rd] = (a < b) ? 1ULL : 0ULL; break;
                        case 4u: x[rd] = a ^ b; break;
                        case 5u:
                            x[rd] = (funct7 == 0x20u)
                                        ? static_cast<uint64_t>(static_cast<int64_t>(a) >> sh)
                                        : (a >> sh);
                            break;
                        case 6u: x[rd] = a | b; break;
                        case 7u: x[rd] = a & b; break;
                        default: break;
                    }
                }
                break;
            }
            case 0x3Bu: {
                const uint32_t a = static_cast<uint32_t>(x[rs1]);
                const uint32_t b = static_cast<uint32_t>(x[rs2]);
                uint32_t r = 0;
                if (funct7 == 1u) {
                    switch (funct3) {
                        case 0u: r = a * b; break;
                        case 4u: r = (b == 0u) ? UINT32_MAX : static_cast<uint32_t>(static_cast<int32_t>(a) / static_cast<int32_t>(b)); break;
                        case 5u: r = (b == 0u) ? UINT32_MAX : (a / b); break;
                        case 6u: r = (b == 0u) ? a : static_cast<uint32_t>(static_cast<int32_t>(a) % static_cast<int32_t>(b)); break;
                        case 7u: r = (b == 0u) ? a : (a % b); break;
                        default: break;
                    }
                } else {
                    const uint32_t sh = b & 0x1Fu;
                    switch (funct3) {
                        case 0u: r = (funct7 == 0x20u) ? (a - b) : (a + b); break;
                        case 1u: r = a << sh; break;
                        case 5u:
                            r = (funct7 == 0x20u)
                                    ? static_cast<uint32_t>(static_cast<int32_t>(a) >> sh)
                                    : (a >> sh);
                            break;
                        default: break;
                    }
                }
                x[rd] = static_cast<uint64_t>(sext(r, 32));
                break;
            }
            case 0x0Fu: break;
            case 0x73u: {
                const uint32_t csr = (insn >> 20) & 0xFFFu;
                if (funct3 == 0u) {
                    if (insn == 0x30200073u) {
                        const bool mpie = (mstatus & (1ULL << 7)) != 0ULL;
                        mstatus = (mstatus & ~8ULL) | (mpie ? 8ULL : 0ULL) | (1ULL << 7);
                        next_pc = mepc;
                    } else if (insn == 0x10500073u) {
                        wait(sc_core::sc_time(20, sc_core::SC_NS));
                    } else {
                        take_trap(11ULL, 0ULL, cur_pc);
                        trapped = true;
                    }
                } else {
                    const uint64_t old = read_csr(csr);
                    const uint64_t src = (funct3 & 4u) ? static_cast<uint64_t>(rs1) : x[rs1];
                    switch (funct3 & 3u) {
                        case 1u: write_csr(csr, src); break;
                        case 2u: if (rs1 != 0u) write_csr(csr, old | src); break;
                        case 3u: if (rs1 != 0u) write_csr(csr, old & ~src); break;
                        default: break;
                    }
                    x[rd] = old;
                }
                break;
            }
            default:
                illegal_trap = true;
                take_trap(2ULL, insn, cur_pc);
                trapped = true;
                break;
        }
        x[0] = 0ULL;
        if (!trapped) pc = next_pc;
        wait(sc_core::sc_time(20, sc_core::SC_NS));
        return true;
    }

    void run_gdb_loop() {
        std::string pkt;
        while (gdb->recv_packet(pkt)) {
            if (pkt == "?") {
                gdb->send_packet("S05");
            } else if (pkt.rfind("qSupported", 0) == 0) {
                gdb->send_packet("PacketSize=4096");
            } else if (pkt == "g") {
                std::string regs;
                regs.reserve(33 * 16);
                for (int i = 0; i < 32; ++i) regs += GdbStub::to_hex_le64(x[static_cast<size_t>(i)]);
                regs += GdbStub::to_hex_le64(pc);
                gdb->send_packet(regs);
            } else if (!pkt.empty() && pkt[0] == 'm') {
                size_t comma = pkt.find(',');
                if (comma != std::string::npos) {
                    uint64_t addr = std::strtoull(pkt.substr(1, comma - 1).c_str(), nullptr, 16);
                    unsigned len = static_cast<unsigned>(
                        std::strtoul(pkt.substr(comma + 1).c_str(), nullptr, 16));
                    if (len > 256u) len = 256u;
                    std::vector<uint8_t> buf(len, 0u);
                    if (bus_rw(addr, buf.data(), len, false)) {
                        static const char hex[] = "0123456789abcdef";
                        std::string h;
                        for (uint8_t b : buf) {
                            h.push_back(hex[b >> 4]);
                            h.push_back(hex[b & 0xFu]);
                        }
                        gdb->send_packet(h);
                    } else {
                        gdb->send_packet("E01");
                    }
                } else {
                    gdb->send_packet("E01");
                }
            } else if (pkt == "s") {
                if (instructions < max_steps && step_one()) {
                    gdb->send_packet("S05");
                } else {
                    gdb->send_packet("W00");
                    break;
                }
            } else if (pkt == "c") {
                while (instructions < max_steps && step_one()) {}
                if (!(finisher && finisher->halted)) timed_out = true;
                char reply[8];
                int code = (finisher && finisher->halted) ? finisher->exit_code : 124;
                std::snprintf(reply, sizeof(reply), "W%02x", code & 0xFF);
                gdb->send_packet(reply);
                break;
            } else if (pkt == "k" || pkt == "D") {
                gdb->send_packet("OK");
                break;
            } else {
                gdb->send_packet("");
            }
        }
        sc_core::sc_stop();
    }

    void run() {
        if (gdb && gdb->client_fd >= 0) {
            run_gdb_loop();
            return;
        }
        for (uint64_t step = 0; step < max_steps; ++step) {
            if (!step_one()) {
                sc_core::sc_stop();
                return;
            }
        }
        timed_out = true;
        sc_core::sc_stop();
    }
};

bool load_elf64(const std::string &path, TlmMemory &dram, uint64_t &entry_out) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    std::vector<uint8_t> data((std::istreambuf_iterator<char>(in)),
                              std::istreambuf_iterator<char>());
    if (data.size() < 64u || data[0] != 0x7Fu || data[1] != 'E' || data[2] != 'L' ||
        data[3] != 'F' || data[4] != 2u || data[5] != 1u) {
        return false;
    }
    entry_out = load_le64(data.data() + 24);
    const uint64_t phoff = load_le64(data.data() + 32);
    const uint16_t phentsize = static_cast<uint16_t>(data[54] | (data[55] << 8));
    const uint16_t phnum = static_cast<uint16_t>(data[56] | (data[57] << 8));
    for (uint16_t i = 0; i < phnum; ++i) {
        const uint64_t off = phoff + static_cast<uint64_t>(i) * phentsize;
        if (off + 56u > data.size()) return false;
        const uint32_t p_type = load_le32(data.data() + off);
        if (p_type != 1u) continue;
        const uint64_t p_offset = load_le64(data.data() + off + 8);
        const uint64_t p_paddr = load_le64(data.data() + off + 24);
        const uint64_t p_filesz = load_le64(data.data() + off + 32);
        const uint64_t p_memsz = load_le64(data.data() + off + 40);
        if (p_paddr < USOC_MAIN_DRAM_BASE ||
            (p_paddr - USOC_MAIN_DRAM_BASE) + p_memsz > dram.mem.size() ||
            p_offset + p_filesz > data.size()) {
            return false;
        }
        const uint64_t dst = p_paddr - USOC_MAIN_DRAM_BASE;
        std::memcpy(dram.mem.data() + dst, data.data() + p_offset, p_filesz);
        if (p_memsz > p_filesz) {
            std::memset(dram.mem.data() + dst + p_filesz, 0, p_memsz - p_filesz);
        }
    }
    return true;
}

}  // namespace

int sc_main(int argc, char **argv) {
    if (argc < 5) {
        std::cerr << "Usage: unified-soc-emu <firmware.elf> <console.log> <noc-trace.jsonl> "
                     "<summary.json> [max_steps] [--gdb-port N] [--gdb-port-file PATH]\n";
        return 2;
    }
#ifdef USOC_ENABLE_VERILATOR_MAC_NPU
    Verilated::commandArgs(1, argv);
#endif
    const std::string elf_path = argv[1];
    const std::string console_path = argv[2];
    const std::string trace_path = argv[3];
    const std::string summary_path = argv[4];
    uint64_t max_steps = 500000ULL;
    int gdb_port = -1;
    std::string gdb_port_file;

    for (int i = 5; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--gdb-port" && i + 1 < argc) {
            gdb_port = std::atoi(argv[++i]);
        } else if (arg == "--gdb-port-file" && i + 1 < argc) {
            gdb_port_file = argv[++i];
        } else if (i == 5) {
            max_steps = std::strtoull(arg.c_str(), nullptr, 10);
        }
    }

    sc_core::sc_set_time_resolution(1, sc_core::SC_PS);

    Rv64CpuInitiator cpu("unified_rv64_core");
    NoC2x2Router noc("systemc_noc_2x2", trace_path);
    TlmMemory dram("main_dram", USOC_MAIN_DRAM_SIZE, false);
    TlmMemory rom("boot_rom", USOC_BOOT_ROM_SIZE, true);
    TlmFinisher finisher("sim_finisher");
    ClintTlm clint("sys_clint");
    PlicTlm plic("sys_plic");
    Uart16550Tlm uart("qemu_uart_0", console_path);
    ChipyardGcdTlm gcd("chipyard_gcd_0");
    OpenTitanGpioTlm gpio("opentitan_gpio_0");
    DmaEngineTlm dma("dma_engine_0");
    MacNpuModule npu("mac_npu_0");
    PmpBusGuardTlm pmp_guard("pmp_guard_0");

    // Wire SystemC/TLM-2.0 2x2 Mesh NoC topology (2 Masters: CPU + DMA -> 11 Targets)
    cpu.bus.bind(noc.cpu_in);
    dma.dma_master.bind(noc.dma_in);
    noc.to_ram.bind(dram.target);
    noc.to_rom.bind(rom.target);
    noc.to_finisher.bind(finisher.target);
    noc.to_clint.bind(clint.target);
    noc.to_plic.bind(plic.target);
    noc.to_uart.bind(uart.target);
    noc.to_gcd.bind(gcd.target);
    noc.to_gpio.bind(gpio.target);
    noc.to_dma.bind(dma.target);
    noc.to_npu.bind(npu.target);
    noc.to_pmp_guard.bind(pmp_guard.target);

    plic.uart = &uart;
    plic.gcd = &gcd;
    plic.gpio = &gpio;
    plic.dma = &dma;
    plic.npu = &npu;
    plic.pmp_guard = &pmp_guard;
    noc.pmp_guard = &pmp_guard;
    cpu.finisher = &finisher;
    cpu.plic = &plic;
    cpu.max_steps = max_steps;

    uint64_t entry = USOC_CPU_ENTRY;
    if (!load_elf64(elf_path, dram, entry)) {
        std::cerr << "Failed to load RV64 ELF: " << elf_path << "\n";
        return 2;
    }
    cpu.pc = entry;

    GdbStub gdb;
    if (gdb_port >= 0) {
        if (!gdb.listen_on(gdb_port, gdb_port_file)) {
            std::cerr << "Failed to open GDB RSP port\n";
            return 2;
        }
        cpu.gdb = &gdb;
    }

    sc_core::sc_start();
    gdb.cleanup();

    const bool passed = finisher.halted && finisher.exit_code == 0 && !cpu.timed_out;
    std::ofstream summary(summary_path);
    summary << "{\n"
            << "  \"status\": \"" << (passed ? "PASSED" : "FAILED") << "\",\n"
            << "  \"mac_npu_backend\": \"" << MacNpuModule::backend_name() << "\",\n"
            << "  \"halted\": " << (finisher.halted ? "true" : "false") << ",\n"
            << "  \"timed_out\": " << (cpu.timed_out ? "true" : "false") << ",\n"
            << "  \"exit_code\": " << (cpu.timed_out ? 124 : finisher.exit_code) << ",\n"
            << "  \"instructions_executed\": " << cpu.instructions << ",\n"
            << "  \"traps_taken\": " << cpu.traps_taken << ",\n"
            << "  \"systemc_time_ps\": " << sc_core::sc_time_stamp().value() << ",\n"
            << "  \"noc_total_transactions\": " << noc.total_transactions << ",\n"
            << "  \"noc_mmio_transactions\": " << noc.mmio_transactions << ",\n"
            << "  \"noc_dma_master_transactions\": " << noc.dma_master_transactions << ",\n"
            << "  \"noc_total_hops\": " << noc.total_hops << ",\n"
            << "  \"router_hits\": [" << noc.router_hits[0] << ", " << noc.router_hits[1] << ", "
            << noc.router_hits[2] << ", " << noc.router_hits[3] << "],\n"
            << "  \"ip_hits\": {\n"
            << "    \"qemu_uart_0\": " << noc.hit_uart << ",\n"
            << "    \"sys_clint\": " << noc.hit_clint << ",\n"
            << "    \"sys_plic\": " << noc.hit_plic << ",\n"
            << "    \"chipyard_gcd_0\": " << noc.hit_gcd << ",\n"
            << "    \"opentitan_gpio_0\": " << noc.hit_gpio << ",\n"
            << "    \"dma_engine_0\": " << noc.hit_dma << ",\n"
            << "    \"mac_npu_0\": " << noc.hit_npu << ",\n"
            << "    \"pmp_guard_0\": " << noc.hit_pmp_guard << ",\n"
            << "    \"pmp_guard_blocked_writes\": " << noc.hit_pmp_blocked << "\n"
            << "  },\n"
            << "  \"ip_results\": {\n"
            << "    \"uart_tx_bytes\": " << uart.tx_bytes << ",\n"
            << "    \"gcd_ops_completed\": " << gcd.ops_completed << ",\n"
            << "    \"dma_bytes_transferred\": " << dma.bytes_transferred << ",\n"
            << "    \"dma_bus_errors\": " << dma.bus_errors << ",\n"
            << "    \"npu_ops_done\": " << npu.ops_done << ",\n"
            << "    \"npu_acc_final\": " << npu.acc << ",\n"
            << "    \"npu_rtl_cycles\": " << npu.rtl_cycles << ",\n"
            << "    \"pmp_guard_fault_count\": " << pmp_guard.fault_count << ",\n"
            << "    \"cpu_pmpcfg0\": " << cpu.pmpcfg0 << "\n"
            << "  }\n"
            << "}\n";
    summary.flush();
    return passed ? 0 : (cpu.timed_out ? 124 : finisher.exit_code);
}
