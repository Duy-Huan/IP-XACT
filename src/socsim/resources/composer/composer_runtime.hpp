// SPDX-License-Identifier: Apache-2.0
// Generic Multi-Platform SystemC/TLM-2.0 + Verilator RTL + Live usp-mmio32-v1 Plugin Runtime.
// Supports arbitrary multi-clock-domain, multi-crossbar, multi-master (RV64 CPU, DMA,
// JTAG-to-AXI4), AXI4-Stream dataflow pipelines, SAM SRAM buffers, Pattern Gen / Logic Analyzer,
// Verilator 5.x RTL wrappers, and out-of-process usp-mmio32-v1 Unix socket IP plugins.
#ifndef USP_COMPOSER_RUNTIME_HPP
#define USP_COMPOSER_RUNTIME_HPP

#include <systemc>
#include <tlm>
#include <tlm_utils/multi_passthrough_initiator_socket.h>
#include <tlm_utils/multi_passthrough_target_socket.h>
#include <tlm_utils/simple_initiator_socket.h>
#include <tlm_utils/simple_target_socket.h>

#include <arpa/inet.h>
#include <endian.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace usp {

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

// --- IEEE 1364 VCD Bus/IRQ Waveform Recorder ---
struct VcdWriter {
    std::ofstream out;
    bool enabled = false;

    void open(const std::string &path) {
        if (path.empty()) return;
        out.open(path);
        if (!out) return;
        enabled = true;
        out << "$version USP Multi-Platform Composed SoC VCD $end\n"
            << "$timescale 1ps $end\n"
            << "$scope module composed_soc $end\n"
            << "$var wire 32 a bus_addr $end\n"
            << "$var wire 1 w bus_wr $end\n"
            << "$var wire 1 c cdc_cross $end\n"
            << "$var wire 1 h l2_hit $end\n"
            << "$var wire 32 i plic_irq_mask $end\n"
            << "$upscope $end\n"
            << "$enddefinitions $end\n"
            << "#0\nb0 a\n0w\n0c\n0h\nb0 i\n";
    }

    static std::string bits32(uint32_t v) {
        std::string s(32, '0');
        for (int i = 0; i < 32; ++i) {
            if ((v >> (31 - i)) & 1u) s[static_cast<size_t>(i)] = '1';
        }
        return s;
    }

    void record(uint64_t ps, uint32_t addr, bool wr, bool cdc, bool l2_hit, uint32_t irq_mask) {
        if (!enabled) return;
        out << "#" << ps << "\nb" << bits32(addr) << " a\n"
            << (wr ? '1' : '0') << "w\n"
            << (cdc ? '1' : '0') << "c\n"
            << (l2_hit ? '1' : '0') << "h\n"
            << "b" << bits32(irq_mask) << " i\n";
    }
};

// --- Uniform Introspection Interface for Composed Devices ---
struct ComposedDevice {
    virtual ~ComposedDevice() = default;
    virtual uint32_t irq_level() const { return 0u; }
    virtual uint64_t rtl_cycles() const { return 0ULL; }
    virtual const char *runtime_backend() const { return "systemc-tlm"; }
};

// --- Coherent SRAM / ROM / SAM Buffer / Pattern Gen+LA Memory Target ---
struct TlmMemory : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<TlmMemory> target;
    std::vector<uint8_t> mem;
    bool read_only = false;
    uint32_t access_ns = 10u;

    TlmMemory(sc_core::sc_module_name name, size_t size_bytes, bool ro = false, uint32_t lat_ns = 10u)
        : sc_module(name), target("target"), mem(size_bytes, 0u), read_only(ro), access_ns(lat_ns) {
        target.register_b_transport(this, &TlmMemory::transport);
    }

    void write_u32_direct(size_t offset, uint32_t val) {
        if (offset + 4u <= mem.size()) {
            std::memcpy(mem.data() + offset, &val, 4u);
        }
    }

    uint32_t read_u32_direct(size_t offset) const {
        uint32_t val = 0u;
        if (offset + 4u <= mem.size()) {
            std::memcpy(&val, mem.data() + offset, 4u);
        }
        return val;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t addr = tx.get_address();
        const unsigned len = tx.get_data_length();
        if (addr + len > mem.size()) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        if (tx.is_write()) {
            if (read_only) {
                tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                return;
            }
            std::memcpy(mem.data() + addr, tx.get_data_ptr(), len);
        } else {
            std::memcpy(tx.get_data_ptr(), mem.data() + addr, len);
        }
        delay += sc_core::sc_time(access_ns, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- SiFive-Compatible Simulation Finisher ---
struct FinisherTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<FinisherTlm> target;
    bool halted = false;
    int exit_code = 1;

    explicit FinisherTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &FinisherTlm::transport);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.is_write() && tx.get_address() == 0u && tx.get_data_length() >= 4u) {
            uint32_t code = 0u;
            std::memcpy(&code, tx.get_data_ptr(), 4u);
            halted = true;
            exit_code = (code == 0x5555u) ? 0 : static_cast<int>((code >> 16) ? (code >> 16) : 1);
            tx.set_response_status(tlm::TLM_OK_RESPONSE);
            delay += sc_core::sc_time(5, sc_core::SC_NS);
            return;
        }
        tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
    }
};

// --- QEMU-Compatible NS16550 UART ---
struct Uart16550Tlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<Uart16550Tlm> target;
    std::ofstream console;
    uint8_t ier = 0, lcr = 3, mcr = 0, scr = 0;
    uint64_t tx_bytes = 0;
    uint32_t irq = 0;

    Uart16550Tlm(sc_core::sc_module_name name, const std::string &log_path)
        : sc_module(name), target("target"), console(log_path) {
        target.register_b_transport(this, &Uart16550Tlm::transport);
    }

    uint32_t irq_level() const override { return irq; }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        uint8_t byte = 0;
        if (tx.is_write()) {
            byte = tx.get_data_ptr()[0];
            if (off == 0u) {
                console.put(static_cast<char>(byte));
                console.flush();
                tx_bytes++;
                if (ier & 2u) irq = 1u;
            } else if (off == 1u) {
                ier = byte;
            } else if (off == 3u) {
                lcr = byte;
            } else if (off == 4u) {
                mcr = byte;
            } else if (off == 7u) {
                scr = byte;
            }
        } else {
            switch (off) {
                case 0u: byte = 0; break;
                case 1u: byte = ier; break;
                case 2u: byte = irq ? 0x02u : 0x01u; irq = 0u; break;
                case 3u: byte = lcr; break;
                case 4u: byte = mcr; break;
                case 5u: byte = 0x60u; break;
                case 6u: byte = 0xB0u; break;
                case 7u: byte = scr; break;
                default: byte = 0; break;
            }
            std::memset(tx.get_data_ptr(), 0, tx.get_data_length());
            tx.get_data_ptr()[0] = byte;
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Chipyard TileLink Serial Adapter ---
struct SerialAdapterTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<SerialAdapterTlm> target;
    uint32_t tx_word = 0, rx_word = 0, frames_sent = 0, irq = 0;

    explicit SerialAdapterTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &SerialAdapterTlm::transport);
    }

    uint32_t irq_level() const override { return irq; }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            if (off == 0x00u) {
                tx_word = data;
                rx_word = data ^ 0x5A000000u;
                frames_sent++;
                irq = 1u;
            } else if (off == 0x10u) {
                if (data & 1u) irq = 0u;
            } else {
                tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                return;
            }
        } else {
            if (off == 0x00u) data = tx_word;
            else if (off == 0x04u) data = rx_word;
            else if (off == 0x08u) data = frames_sent;
            else if (off == 0x10u) data = irq;
            else {
                tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(15, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Multi-Hart CLINT (MTIME + MSIP[0..3]) ---
struct ClintTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<ClintTlm> target;
    std::array<uint32_t, 4> msip{};
    std::array<uint64_t, 4> mtimecmp{UINT64_MAX, UINT64_MAX, UINT64_MAX, UINT64_MAX};
    uint64_t ipi_fires = 0;

    explicit ClintTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &ClintTlm::transport);
    }

    bool has_msip(unsigned hid) const { return hid < 4u && (msip[hid] & 1u) != 0u; }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        const unsigned len = tx.get_data_length();
        const uint64_t now_ticks = (sc_core::sc_time_stamp().value() / 100000ULL) + 1ULL;
        if (tx.is_write()) {
            if (off < 16u && (off % 4u) == 0u && len >= 4u) {
                uint32_t v = 0;
                std::memcpy(&v, tx.get_data_ptr(), 4u);
                const unsigned hid = static_cast<unsigned>(off / 4u);
                if ((v & 1u) && !(msip[hid] & 1u)) ipi_fires++;
                msip[hid] = v & 1u;
            } else if (off >= 0x4000u && off < 0x4020u && len == 8u) {
                const unsigned hid = static_cast<unsigned>((off - 0x4000u) / 8u);
                std::memcpy(&mtimecmp[hid], tx.get_data_ptr(), 8u);
            } else {
                tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                return;
            }
        } else {
            if (off < 16u && (off % 4u) == 0u && len >= 4u) {
                const unsigned hid = static_cast<unsigned>(off / 4u);
                std::memcpy(tx.get_data_ptr(), &msip[hid], 4u);
            } else if (off == 0xBFF8u && len == 8u) {
                std::memcpy(tx.get_data_ptr(), &now_ticks, 8u);
            } else if (off == 0xBFF8u && len == 4u) {
                uint32_t lo = static_cast<uint32_t>(now_ticks);
                std::memcpy(tx.get_data_ptr(), &lo, 4u);
            } else if (off == 0xBFFCu && len == 4u) {
                uint32_t hi = static_cast<uint32_t>(now_ticks >> 32);
                std::memcpy(tx.get_data_ptr(), &hi, 4u);
            } else {
                tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                return;
            }
        }
        delay += sc_core::sc_time(5, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Dynamic Multi-Source PLIC (supports arbitrary dynamically bound IRQs 1..31) ---
struct PlicTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<PlicTlm> target;
    std::array<uint32_t, 32> priority{};
    std::array<ComposedDevice *, 32> sources{};
    uint32_t enable_mask = 0, threshold = 0, claimed_mask = 0;

    explicit PlicTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &PlicTlm::transport);
    }

    void bind_irq(uint32_t irq_id, ComposedDevice *dev) {
        if (irq_id >= 1u && irq_id < 32u) sources[irq_id] = dev;
    }

    uint32_t active_source_mask() const {
        uint32_t m = 0u;
        for (uint32_t id = 1u; id < 32u; ++id) {
            if (sources[id] && sources[id]->irq_level()) m |= (1u << id);
        }
        return m;
    }

    uint32_t highest_pending_irq() const {
        const uint32_t pending = active_source_mask() & enable_mask & (~claimed_mask);
        uint32_t best_id = 0u, best_prio = threshold;
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
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            if (off < 128u && (off % 4u) == 0u) priority[off / 4u] = data;
            else if (off == 0x2000u) enable_mask = data;
            else if (off == 0x200000u) threshold = data;
            else if (off == 0x200004u) { if (data < 32u) claimed_mask &= ~(1u << data); }
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
        } else {
            if (off < 128u && (off % 4u) == 0u) data = priority[off / 4u];
            else if (off == 0x2000u) data = enable_mask;
            else if (off == 0x200000u) data = threshold;
            else if (off == 0x200004u) {
                data = highest_pending_irq();
                if (data != 0u) claimed_mask |= (1u << data);
            } else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(5, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- LIVE Out-of-Process `usp-mmio32-v1` Unix Socket Proxy Target inside SystemC/TLM-2.0 ---
// Connects directly to ANY `native-model-package-1` compiled IP binary over AF_UNIX!
struct UspMmio32ProxyTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<UspMmio32ProxyTlm> target;
    int fd = -1;
    uint32_t seq = 0;
    uint32_t irq = 0;
    uint64_t socket_tx_count = 0;
    uint32_t tx_latency_ns = 15u;

    UspMmio32ProxyTlm(sc_core::sc_module_name name, const std::string &socket_path,
                      uint32_t lat_ns = 15u)
        : sc_module(name), target("target"), tx_latency_ns(lat_ns) {
        target.register_b_transport(this, &UspMmio32ProxyTlm::transport);
        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        if (socket_path.size() >= sizeof(addr.sun_path)) {
            std::cerr << "usp-mmio32-v1 socket path too long: " << socket_path << "\n";
            std::exit(2);
        }
        std::strcpy(addr.sun_path, socket_path.c_str());
        for (int attempt = 0; attempt < 250; ++attempt) {
            fd = socket(AF_UNIX, SOCK_STREAM, 0);
            if (fd >= 0 &&
                connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == 0) {
                break;
            }
            if (fd >= 0) { close(fd); fd = -1; }
            usleep(20000);
        }
        if (fd < 0) {
            std::cerr << "Failed to connect live usp-mmio32-v1 plugin socket: "
                      << socket_path << "\n";
            std::exit(2);
        }
        timeval tv{5, 0};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
        uint32_t dummy = 0u;
        (void)rpc(2u, 0u, 0u, dummy);  // RESET
    }

    ~UspMmio32ProxyTlm() override {
        if (fd >= 0) close(fd);
    }

    uint32_t irq_level() const override { return irq; }
    const char *runtime_backend() const override { return "usp-mmio32-v1-live-plugin"; }

    bool io_exact(void *buf, size_t n, bool writing) {
        auto *p = static_cast<char *>(buf);
        for (size_t done = 0; done < n;) {
            ssize_t rc = writing ? send(fd, p + done, n - done, MSG_NOSIGNAL)
                                 : recv(fd, p + done, n - done, 0);
            if (rc < 0 && errno == EINTR) continue;
            if (rc <= 0) return false;
            done += static_cast<size_t>(rc);
        }
        return true;
    }

    bool rpc(uint32_t op, uint32_t offset, uint32_t data, uint32_t &out_val) {
        uint32_t s = ++seq;
        std::array<uint32_t, 5> req = {
            htole32(0x31505355u), htole32(s), htole32(op), htole32(offset), htole32(data)
        };
        std::array<uint32_t, 5> res{};
        if (!io_exact(req.data(), 20u, true) || !io_exact(res.data(), 20u, false)) {
            return false;
        }
        if (le32toh(res[0]) != 0x31505355u || le32toh(res[1]) != s) return false;
        const uint32_t status = le32toh(res[2]);
        out_val = le32toh(res[3]);
        irq = le32toh(res[4]) ? 1u : 0u;
        socket_tx_count++;
        return status == 0u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        uint32_t data = 0u, val = 0u;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        const uint32_t op = tx.is_write() ? 1u : 0u;
        const uint32_t off = static_cast<uint32_t>(tx.get_address());
        if (!rpc(op, off, data, val)) {
            tx.set_response_status(tx.is_write() ? tlm::TLM_COMMAND_ERROR_RESPONSE
                                                 : tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        if (!tx.is_write()) std::memcpy(tx.get_data_ptr(), &val, 4u);
        delay += sc_core::sc_time(
            (tx.is_write() && off == 0u && data == 1u) ? (tx_latency_ns + 15u) : tx_latency_ns,
            sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Universal Generic Verilator 5.032 RTL Wrapper Template ---
// Binds ANY clean-room SV RTL module exposing the standard MMIO32 RTL ports!
template <typename VDut>
struct VerilatorMmioWrapper : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<VerilatorMmioWrapper<VDut>> target;
    VDut dut;
    uint32_t irq = 0u;
    uint64_t cycles = 0ULL;
    uint32_t clk_period_ns = 4u;
    int busy_reg_addr = -1;
    uint32_t max_busy_ticks = 2048u;

    VerilatorMmioWrapper(sc_core::sc_module_name name, uint32_t period_ns = 4u,
                         int busy_addr = -1, uint32_t max_ticks = 2048u)
        : sc_module(name), target("target"), clk_period_ns(period_ns),
          busy_reg_addr(busy_addr), max_busy_ticks(max_ticks) {
        target.register_b_transport(this, &VerilatorMmioWrapper::transport);
        dut.clk_i = 0; dut.rst_ni = 0; dut.req_valid_i = 0;
        dut.req_write_i = 0; dut.req_addr_i = 0; dut.req_wdata_i = 0;
        tick();
        dut.rst_ni = 1;
        tick();
    }

    uint32_t irq_level() const override { return irq; }
    uint64_t rtl_cycles() const override { return cycles; }
    const char *runtime_backend() const override { return "verilator-rtl"; }

    void tick() {
        dut.clk_i = 0; dut.eval();
        dut.clk_i = 1; dut.eval();
        dut.clk_i = 0; dut.eval();
        irq = dut.irq_o ? 1u : 0u;
        cycles = dut.rtl_cycles_o;
    }

    uint32_t peek_reg(uint8_t addr) {
        dut.req_valid_i = 1; dut.req_write_i = 0; dut.req_addr_i = addr; dut.req_wdata_i = 0;
        dut.eval();
        uint32_t v = dut.rsp_rdata_o;
        dut.req_valid_i = 0; dut.eval();
        return v;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u || tx.get_address() > 0xFFu) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        const uint8_t off = static_cast<uint8_t>(tx.get_address());
        uint32_t data = 0u;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        dut.req_valid_i = 1; dut.req_write_i = tx.is_write() ? 1 : 0;
        dut.req_addr_i = off; dut.req_wdata_i = data;
        dut.eval();
        if (dut.rsp_err_o) {
            dut.req_valid_i = 0; dut.eval();
            tx.set_response_status(tx.is_write() ? tlm::TLM_COMMAND_ERROR_RESPONSE
                                                 : tlm::TLM_ADDRESS_ERROR_RESPONSE);
            return;
        }
        uint32_t ticks = 1u;
        if (!tx.is_write()) {
            uint32_t r = dut.rsp_rdata_o;
            std::memcpy(tx.get_data_ptr(), &r, 4u);
            tick();
            dut.req_valid_i = 0; dut.eval();
        } else {
            tick();
            dut.req_valid_i = 0; dut.eval();
            if (busy_reg_addr >= 0 && off == 0x00u && (data == 1u || data == 3u)) {
                while (ticks < max_busy_ticks &&
                       peek_reg(static_cast<uint8_t>(busy_reg_addr)) != 0u) {
                    tick();
                    ticks++;
                }
            }
        }
        delay += sc_core::sc_time(static_cast<double>(ticks * clk_period_ns + 10u), sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- L2 Cache & Multi-Master Snoop Controller (supports both Verilator Vl2_cache_rtl & TLM) ---
struct L2CacheBase : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<L2CacheBase> target;
    uint32_t hits = 0, misses = 0, snoops = 0;
    uint64_t cycles = 0;
    explicit L2CacheBase(sc_core::sc_module_name name) : sc_module(name), target("target") {}
    virtual bool lookup(uint64_t phys_addr, bool is_dma, bool is_write) = 0;
    uint64_t rtl_cycles() const override { return cycles; }
};

struct L2CacheTlm : L2CacheBase {
    std::array<uint32_t, 64> tags{};
    uint64_t valid = 0ULL;

    explicit L2CacheTlm(sc_core::sc_module_name name) : L2CacheBase(name) {
        target.register_b_transport(this, &L2CacheTlm::transport);
    }

    bool lookup(uint64_t phys_addr, bool is_dma, bool is_write) override {
        const unsigned idx = static_cast<unsigned>((phys_addr >> 6) & 63u);
        const uint32_t tag = static_cast<uint32_t>((phys_addr >> 12) & 0xFFFFFu);
        const bool hit = ((valid >> idx) & 1ULL) && tags[idx] == tag;
        if (is_dma) {
            if (is_write && hit) valid &= ~(1ULL << idx);
            snoops++;
        } else if (hit) {
            hits++;
        } else {
            misses++;
            valid |= (1ULL << idx);
            tags[idx] = tag;
        }
        return hit;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            if (off == 0x0Cu && (data & 1u)) valid = 0ULL;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
        } else {
            if (off == 0x00u) data = hits;
            else if (off == 0x04u) data = misses;
            else if (off == 0x08u) data = snoops;
            else if (off == 0x0Cu) data = 512u * 1024u;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

#ifdef USP_HAS_VL2_CACHE_RTL
struct L2CacheRtl : L2CacheBase {
    Vl2_cache_rtl dut;

    explicit L2CacheRtl(sc_core::sc_module_name name) : L2CacheBase(name) {
        target.register_b_transport(this, &L2CacheRtl::transport);
        dut.clk_i = 0; dut.rst_ni = 0; dut.lookup_valid_i = 0;
        dut.lookup_addr_i = 0; dut.lookup_is_dma_i = 0; dut.lookup_is_write_i = 0;
        dut.flush_i = 0;
        tick();
        dut.rst_ni = 1;
        tick();
    }

    const char *runtime_backend() const override { return "verilator-rtl"; }

    void tick() {
        dut.clk_i = 0; dut.eval();
        dut.clk_i = 1; dut.eval();
        dut.clk_i = 0; dut.eval();
        hits = dut.hits_o; misses = dut.misses_o; snoops = dut.snoops_o;
        cycles = dut.rtl_cycles_o;
    }

    bool lookup(uint64_t phys_addr, bool is_dma, bool is_write) override {
        dut.lookup_valid_i = 1;
        dut.lookup_addr_i = static_cast<uint32_t>(phys_addr);
        dut.lookup_is_dma_i = is_dma ? 1 : 0;
        dut.lookup_is_write_i = is_write ? 1 : 0;
        dut.eval();
        bool hit = dut.lookup_hit_o != 0;
        tick();
        dut.lookup_valid_i = 0; dut.eval();
        return hit;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            if (off == 0x0Cu && (data & 1u)) {
                dut.flush_i = 1; tick(); dut.flush_i = 0; dut.eval();
            } else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
        } else {
            if (off == 0x00u) data = hits;
            else if (off == 0x04u) data = misses;
            else if (off == 0x08u) data = snoops;
            else if (off == 0x0Cu) data = 512u * 1024u;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};
#endif

// --- Behavioral TLM Counterparts for RTL↔TLM Calibration ---
struct HwachaVec4Tlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<HwachaVec4Tlm> target;
    std::array<int32_t, 4> va{}, vb{}, vc{};
    int32_t acc = 0;
    uint32_t vlen = 4u, ops_done = 0u, power_ops = 0u, irq = 0u;

    explicit HwachaVec4Tlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &HwachaVec4Tlm::transport);
    }
    uint32_t irq_level() const override { return irq; }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            if (off == 0x00u) {
                if (data == 1u) {
                    int32_t sum = 0;
                    for (size_t i = 0; i < 4; ++i) { vc[i] = va[i] * vb[i]; sum += vc[i]; }
                    acc += sum; ops_done++; irq = 1u;
                } else if (data == 2u) {
                    vc.fill(0); acc = 0; ops_done = 0; power_ops = 0; irq = 0;
                } else if (data == 3u) {
                    int32_t sum = 0;
                    for (size_t i = 0; i < 4; ++i) { vc[i] = va[i] * va[i] + vb[i] * vb[i]; sum += vc[i]; }
                    acc += sum; ops_done++; power_ops++; irq = 1u;
                } else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
            } else if (off == 0x0Cu) vlen = data;
            else if (off == 0x10u) { if (data & 1u) irq = 0u; }
            else if (off >= 0x20u && off <= 0x2Cu) va[(off - 0x20u) / 4u] = static_cast<int32_t>(data);
            else if (off >= 0x30u && off <= 0x3Cu) vb[(off - 0x30u) / 4u] = static_cast<int32_t>(data);
            else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
        } else {
            if (off == 0x00u) data = ops_done;
            else if (off == 0x04u) data = (irq << 1) | 1u;
            else if (off == 0x08u) data = static_cast<uint32_t>(acc);
            else if (off == 0x0Cu) data = vlen;
            else if (off == 0x10u) data = irq;
            else if (off == 0x14u) data = 4u;
            else if (off >= 0x20u && off <= 0x2Cu) data = static_cast<uint32_t>(va[(off - 0x20u) / 4u]);
            else if (off >= 0x30u && off <= 0x3Cu) data = static_cast<uint32_t>(vb[(off - 0x30u) / 4u]);
            else if (off >= 0x40u && off <= 0x4Cu) data = static_cast<uint32_t>(vc[(off - 0x40u) / 4u]);
            else if (off == 0x50u) data = power_ops;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(14, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

struct Tuner32PhTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<Tuner32PhTlm> target;
    static constexpr std::array<int16_t, 32> kCos = {
        1024, 1004, 946, 851, 724, 569, 392, 200, 0, -200, -392, -569, -724, -851, -946, -1004,
        -1024, -1004, -946, -851, -724, -569, -392, -200, 0, 200, 392, 569, 724, 851, 946, 1004
    };
    static constexpr std::array<int16_t, 32> kSin = {
        0, 200, 392, 569, 724, 851, 946, 1004, 1024, 1004, 946, 851, 724, 569, 392, 200,
        0, -200, -392, -569, -724, -851, -946, -1004, -1024, -1004, -946, -851, -724, -569, -392, -200
    };
    uint32_t phase_idx = 0, phase_step = 0, in_iq = 0, out_iq = 0, samples_done = 0, irq = 0;

    explicit Tuner32PhTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &Tuner32PhTlm::transport);
    }
    uint32_t irq_level() const override { return irq; }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            if (off == 0x00u) {
                if (data == 1u) {
                    int32_t ii = static_cast<int16_t>(in_iq & 0xFFFFu);
                    int32_t iq = static_cast<int16_t>((in_iq >> 16) & 0xFFFFu);
                    int32_t c = kCos[phase_idx & 31u];
                    int32_t s = kSin[phase_idx & 31u];
                    int32_t oi = (ii * c + iq * s) >> 10;
                    int32_t oq = (iq * c - ii * s) >> 10;
                    out_iq = (static_cast<uint32_t>(static_cast<uint16_t>(oq)) << 16) |
                             static_cast<uint32_t>(static_cast<uint16_t>(oi));
                    phase_idx = (phase_idx + phase_step) & 31u;
                    samples_done++; irq = 1u;
                } else if (data == 2u) {
                    phase_idx = 0; out_iq = 0; samples_done = 0; irq = 0;
                } else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
            } else if (off == 0x04u) phase_step = data & 31u;
            else if (off == 0x08u) in_iq = data;
            else if (off == 0x10u) { if (data & 1u) irq = 0u; }
            else if (off == 0x14u) phase_idx = data & 31u;
            else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
        } else {
            if (off == 0x00u || off == 0x18u) data = samples_done;
            else if (off == 0x04u) data = phase_step;
            else if (off == 0x08u) data = in_iq;
            else if (off == 0x0Cu) data = out_iq;
            else if (off == 0x10u) data = irq;
            else if (off == 0x14u) data = phase_idx;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(14, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

struct FirDecim8Tlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<FirDecim8Tlm> target;
    std::array<int16_t, 136> coeff{};
    std::array<int16_t, 136> delay_buf{};
    uint32_t num_taps = 136, decim_ratio = 8, shift_right = 10, coeff_idx = 0;
    uint32_t wr_ptr = 0, decim_cnt = 0, in_count = 0, out_count = 0, irq = 0;
    int16_t in_sample = 0;
    int32_t out_sample = 0;

    explicit FirDecim8Tlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &FirDecim8Tlm::transport);
    }
    uint32_t irq_level() const override { return irq; }

    void run_fir(uint32_t start_ptr) {
        int64_t acc = 0;
        uint32_t rp = start_ptr;
        for (uint32_t k = 0; k < num_taps; ++k) {
            acc += static_cast<int64_t>(delay_buf[rp]) * static_cast<int64_t>(coeff[k]);
            rp = (rp == 0u) ? 135u : (rp - 1u);
        }
        out_sample = static_cast<int32_t>(acc >> shift_right);
        out_count++;
        irq = 1u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        uint32_t extra_ticks = 1u;
        if (tx.is_write()) {
            if (off == 0x00u) {
                if (data == 1u) {
                    delay_buf[wr_ptr] = in_sample;
                    in_count++;
                    if (decim_cnt + 1u >= decim_ratio) {
                        decim_cnt = 0u;
                        run_fir(wr_ptr);
                        extra_ticks = num_taps + 1u;
                    } else {
                        decim_cnt++;
                    }
                    wr_ptr = (wr_ptr == 135u) ? 0u : (wr_ptr + 1u);
                } else if (data == 2u) {
                    delay_buf.fill(0); wr_ptr = 0; decim_cnt = 0;
                    out_sample = 0; in_count = 0; out_count = 0; irq = 0;
                } else if (data == 3u) {
                    run_fir((wr_ptr == 0u) ? 135u : (wr_ptr - 1u));
                    extra_ticks = num_taps + 1u;
                } else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
            } else if (off == 0x04u) {
                decim_ratio = (data & 0xFFu) ? (data & 0xFFu) : 1u;
                shift_right = (data >> 8) & 0xFu;
            } else if (off == 0x08u) {
                if ((data & 0xFFu) >= 1u && (data & 0xFFu) <= 136u) num_taps = data & 0xFFu;
            } else if (off == 0x0Cu) in_sample = static_cast<int16_t>(data & 0xFFFFu);
            else if (off == 0x10u) { if (data & 1u) irq = 0u; }
            else if (off == 0x1Cu) { if ((data & 0xFFu) < 136u) coeff_idx = data & 0xFFu; }
            else if (off == 0x20u) coeff[coeff_idx] = static_cast<int16_t>(data & 0xFFFFu);
            else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
        } else {
            if (off == 0x00u) data = out_count;
            else if (off == 0x04u) data = (shift_right << 8) | decim_ratio;
            else if (off == 0x08u) data = num_taps;
            else if (off == 0x0Cu) data = static_cast<uint32_t>(static_cast<int32_t>(in_sample));
            else if (off == 0x10u) data = irq;
            else if (off == 0x14u) data = static_cast<uint32_t>(out_sample);
            else if (off == 0x18u) data = in_count;
            else if (off == 0x1Cu) data = coeff_idx;
            else if (off == 0x20u) data = static_cast<uint32_t>(static_cast<int32_t>(coeff[coeff_idx]));
            else if (off == 0x24u) data = 0u;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(static_cast<double>(extra_ticks * 4u + 10u), sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

struct Polyphase12tTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<Polyphase12tTlm> target;
    static constexpr std::array<int32_t, 12> kTaps = {
        16, 32, 64, 96, 128, 176, 176, 128, 96, 64, 32, 16
    };
    std::array<int32_t, 12> delay_line{};
    uint32_t phase_sel = 0, samples_done = 0, irq = 0;
    int32_t in_sample = 0, out_sample = 0;

    explicit Polyphase12tTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &Polyphase12tTlm::transport);
    }
    uint32_t irq_level() const override { return irq; }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            if (off == 0x00u) {
                if (data == 1u) {
                    for (int i = 11; i > 0; --i) delay_line[static_cast<size_t>(i)] = delay_line[static_cast<size_t>(i - 1)];
                    delay_line[0] = in_sample;
                    int64_t acc = 0;
                    for (size_t i = 0; i < 12; ++i) acc += static_cast<int64_t>(delay_line[i]) * kTaps[i];
                    out_sample = static_cast<int32_t>(acc >> 10);
                    samples_done++; irq = 1u;
                    delay += sc_core::sc_time(30, sc_core::SC_NS);
                } else if (data == 2u) {
                    delay_line.fill(0); phase_sel = 0; in_sample = 0; out_sample = 0; samples_done = 0; irq = 0;
                    delay += sc_core::sc_time(15, sc_core::SC_NS);
                } else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
            } else if (off == 0x04u) { phase_sel = data & 3u; delay += sc_core::sc_time(15, sc_core::SC_NS); }
            else if (off == 0x08u) { in_sample = static_cast<int32_t>(data); delay += sc_core::sc_time(15, sc_core::SC_NS); }
            else if (off == 0x10u) { if (data & 1u) irq = 0u; delay += sc_core::sc_time(15, sc_core::SC_NS); }
            else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
        } else {
            if (off == 0x00u) data = samples_done;
            else if (off == 0x04u) data = phase_sel;
            else if (off == 0x08u) data = static_cast<uint32_t>(in_sample);
            else if (off == 0x0Cu) data = static_cast<uint32_t>(out_sample);
            else if (off == 0x10u) data = irq;
            else if (off == 0x14u) data = 1024u;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
            delay += sc_core::sc_time(15, sc_core::SC_NS);
        }
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

struct Fft128PtTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<Fft128PtTlm> target;
    static constexpr std::array<int16_t, 64> kTwCos = {
        1024, 1023, 1019, 1013, 1004, 993, 980, 964, 946, 926, 903, 878, 851, 822, 792, 759,
        724, 688, 650, 610, 569, 526, 483, 438, 392, 345, 297, 249, 200, 150, 100, 50,
        0, -50, -100, -150, -200, -249, -297, -345, -392, -438, -483, -526, -569, -610, -650, -688,
        -724, -759, -792, -822, -851, -878, -903, -926, -946, -964, -980, -993, -1004, -1013, -1019, -1023
    };
    static constexpr std::array<int16_t, 64> kTwSin = {
        0, 50, 100, 150, 200, 249, 297, 345, 392, 438, 483, 526, 569, 610, 650, 688,
        724, 759, 792, 822, 851, 878, 903, 926, 946, 964, 980, 993, 1004, 1013, 1019, 1023,
        1024, 1023, 1019, 1013, 1004, 993, 980, 964, 946, 926, 903, 878, 851, 822, 792, 759,
        724, 688, 650, 610, 569, 526, 483, 438, 392, 345, 297, 249, 200, 150, 100, 50
    };
    std::array<int16_t, 128> in_re{}, in_im{}, buf_re{}, buf_im{};
    uint32_t wr_idx = 0, rd_idx = 0, ffts_done = 0, peak_bin = 0, peak_mag2 = 0, irq = 0;

    explicit Fft128PtTlm(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &Fft128PtTlm::transport);
    }
    uint32_t irq_level() const override { return irq; }

    static uint32_t bitrev7(uint32_t x) {
        uint32_t r = 0;
        for (int b = 0; b < 7; ++b) r = (r << 1) | ((x >> b) & 1u);
        return r;
    }

    void run_fft() {
        for (uint32_t i = 0; i < 128u; ++i) {
            uint32_t br = bitrev7(i);
            buf_re[i] = in_re[br];
            buf_im[i] = in_im[br];
        }
        for (uint32_t stage = 0; stage < 7u; ++stage) {
            uint32_t half = 1u << stage;
            for (uint32_t bfly = 0; bfly < 64u; ++bfly) {
                uint32_t sub = bfly & (half - 1u);
                uint32_t grp = bfly >> stage;
                uint32_t i0 = (grp << (stage + 1u)) | sub;
                uint32_t i1 = i0 | half;
                uint32_t tw = sub << (6u - stage);
                int32_t ur = buf_re[i0], ui = buf_im[i0];
                int32_t vr = buf_re[i1], vi = buf_im[i1];
                int32_t wc = kTwCos[tw], ws = kTwSin[tw];
                int32_t tr = (vr * wc + vi * ws) >> 10;
                int32_t ti = (vi * wc - vr * ws) >> 10;
                buf_re[i0] = static_cast<int16_t>((ur + tr) >> 1);
                buf_im[i0] = static_cast<int16_t>((ui + ti) >> 1);
                buf_re[i1] = static_cast<int16_t>((ur - tr) >> 1);
                buf_im[i1] = static_cast<int16_t>((ui - ti) >> 1);
            }
        }
        peak_bin = 0; peak_mag2 = 0;
        for (uint32_t i = 0; i < 128u; ++i) {
            int32_t re = buf_re[i], im = buf_im[i];
            uint32_t m2 = static_cast<uint32_t>(re * re + im * im);
            if (m2 > peak_mag2) { peak_mag2 = m2; peak_bin = i; }
        }
        ffts_done++; irq = 1u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        uint32_t ticks = 1u;
        if (tx.is_write()) {
            if (off == 0x00u) {
                if (data == 1u) { run_fft(); ticks = 705u; }
                else if (data == 2u) {
                    wr_idx = 0; rd_idx = 0; ffts_done = 0; peak_bin = 0; peak_mag2 = 0; irq = 0;
                } else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
            } else if (off == 0x04u) wr_idx = data & 127u;
            else if (off == 0x08u) {
                in_re[wr_idx] = static_cast<int16_t>(data & 0xFFFFu);
                in_im[wr_idx] = static_cast<int16_t>((data >> 16) & 0xFFFFu);
                wr_idx = (wr_idx + 1u) & 127u;
            } else if (off == 0x0Cu) rd_idx = data & 127u;
            else if (off == 0x10u) { if (data & 1u) irq = 0u; }
            else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
        } else {
            if (off == 0x00u) data = ffts_done;
            else if (off == 0x04u) data = wr_idx;
            else if (off == 0x08u) data = (static_cast<uint32_t>(static_cast<uint16_t>(in_im[wr_idx])) << 16) |
                                          static_cast<uint32_t>(static_cast<uint16_t>(in_re[wr_idx]));
            else if (off == 0x0Cu) data = rd_idx;
            else if (off == 0x10u) data = irq;
            else if (off == 0x14u) data = (static_cast<uint32_t>(static_cast<uint16_t>(buf_im[rd_idx])) << 16) |
                                          static_cast<uint32_t>(static_cast<uint16_t>(buf_re[rd_idx]));
            else if (off == 0x18u) data = peak_bin;
            else if (off == 0x1Cu) data = peak_mag2;
            else if (off == 0x20u) data = 0u;
            else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(static_cast<double>(ticks * 4u + 10u), sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Mixed-Signal Behavioral TISAR ADC + 18KB Calibration LUT + AXI4-Stream DSP Chain Engine ---
// Executes real AXI4-Stream dataflow through ALL 5 stages (ADC+LUT -> Tuner -> FIR/8 -> Polyphase
// Plugin -> 128-pt FFT), populating ALL 5 SAM buffers + 512KB Logic Analyzer!
struct TisarAdcStreamTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<TisarAdcStreamTlm> target;
    tlm_utils::simple_initiator_socket<TisarAdcStreamTlm> stream_bus;
    std::array<uint8_t, 18 * 1024> lut_sram{};  // Exact 18KB calibration LUT SRAM
    uint32_t frames_done = 0, sample_len = 1024u, mux_sel = 1u, irq = 0u;
    uint64_t stream_packets_total = 0ULL;
    std::array<uint64_t, 5> stage_packets{};

    // Configurable target addresses populated by generated top-level from IR
    uint64_t patgen_base = 0, la_base = 0;
    uint64_t sam_adc_base = 0, sam_tuner_base = 0, sam_fir_base = 0, sam_poly_base = 0, sam_fft_base = 0;
    uint64_t tuner_base = 0, fir_base = 0, poly_base = 0, fft_base = 0;

    explicit TisarAdcStreamTlm(sc_core::sc_module_name name)
        : sc_module(name), target("target"), stream_bus("stream_bus") {
        target.register_b_transport(this, &TisarAdcStreamTlm::transport);
        // Default linear calibration LUT: 4096 16-bit signed entries (8 KiB) + gain table (10 KiB)
        for (uint32_t code = 0; code < 4096u; ++code) {
            int16_t cal = static_cast<int16_t>(static_cast<int32_t>(code) - 2048);
            std::memcpy(lut_sram.data() + code * 2u, &cal, 2u);
        }
    }

    uint32_t irq_level() const override { return irq; }
    const char *runtime_backend() const override { return "mixed-signal-tlm"; }

    uint32_t bus_rd32(uint64_t addr, sc_core::sc_time &delay) {
        uint32_t val = 0u;
        tlm::tlm_generic_payload tx;
        tx.set_command(tlm::TLM_READ_COMMAND);
        tx.set_address(addr);
        tx.set_data_ptr(reinterpret_cast<unsigned char *>(&val));
        tx.set_data_length(4u); tx.set_streaming_width(4u);
        stream_bus->b_transport(tx, delay);
        return val;
    }

    void bus_wr32(uint64_t addr, uint32_t val, sc_core::sc_time &delay) {
        tlm::tlm_generic_payload tx;
        tx.set_command(tlm::TLM_WRITE_COMMAND);
        tx.set_address(addr);
        tx.set_data_ptr(reinterpret_cast<unsigned char *>(&val));
        tx.set_data_length(4u); tx.set_streaming_width(4u);
        stream_bus->b_transport(tx, delay);
    }

    void run_axi4_stream_pipeline(sc_core::sc_time &delay) {
        const uint32_t n_in = (sample_len == 0u || sample_len > 1024u) ? 1024u : sample_len;
        // Reset downstream DSP chain states cleanly (keep programmed coefficients & tuner step)
        bus_wr32(fir_base + 0x00u, 2u, delay);
        bus_wr32(poly_base + 0x00u, 2u, delay);
        bus_wr32(fft_base + 0x00u, 2u, delay);
        bus_wr32(fft_base + 0x04u, 0u, delay);

        uint32_t fir_out_idx = 0u;
        for (uint32_t n = 0; n < n_in; ++n) {
            // Stage 1: TISAR ADC + 18KB Calibration LUT (muxed with 512KB Pattern Generator)
            uint32_t raw_iq = mux_sel ? bus_rd32(patgen_base + n * 4u, delay) : 0x00000800u;
            uint32_t code_i = raw_iq & 0xFFFu;
            uint32_t code_q = (raw_iq >> 16) & 0xFFFu;
            int16_t cal_i = 0, cal_q = 0;
            std::memcpy(&cal_i, lut_sram.data() + code_i * 2u, 2u);
            std::memcpy(&cal_q, lut_sram.data() + code_q * 2u, 2u);
            uint32_t adc_iq = (static_cast<uint32_t>(static_cast<uint16_t>(cal_q)) << 16) |
                              static_cast<uint32_t>(static_cast<uint16_t>(cal_i));
            bus_wr32(sam_adc_base + n * 4u, adc_iq, delay);
            stage_packets[0]++;

            // Stage 2: AXI4-Stream -> 32-phase Tuner (Verilator RTL or TLM) -> 256KB SAM
            bus_wr32(tuner_base + 0x08u, adc_iq, delay);
            bus_wr32(tuner_base + 0x00u, 1u, delay);
            bus_wr32(tuner_base + 0x10u, 1u, delay);  // ack intermediate stream IRQ
            uint32_t tuned_iq = bus_rd32(tuner_base + 0x0Cu, delay);
            bus_wr32(sam_tuner_base + n * 4u, tuned_iq, delay);
            stage_packets[1]++;

            // Stage 3: AXI4-Stream -> 136-tap prog FIR & /8 Decimator (Verilator RTL or TLM)
            int32_t tuned_i = static_cast<int16_t>(tuned_iq & 0xFFFFu);
            bus_wr32(fir_base + 0x0Cu, static_cast<uint32_t>(tuned_i), delay);
            uint32_t prev_fir_cnt = bus_rd32(fir_base + 0x00u, delay);
            bus_wr32(fir_base + 0x00u, 1u, delay);
            uint32_t cur_fir_cnt = bus_rd32(fir_base + 0x00u, delay);
            if (cur_fir_cnt > prev_fir_cnt && fir_out_idx < 128u) {
                bus_wr32(fir_base + 0x10u, 1u, delay);
                uint32_t fir_out = bus_rd32(fir_base + 0x14u, delay);
                bus_wr32(sam_fir_base + fir_out_idx * 4u, fir_out, delay);
                stage_packets[2]++;

                // Stage 4: AXI4-Stream -> 12-tap Fixed Polyphase Filter (LIVE usp-mmio32-v1 Plugin!)
                bus_wr32(poly_base + 0x08u, fir_out, delay);
                bus_wr32(poly_base + 0x00u, 1u, delay);
                bus_wr32(poly_base + 0x10u, 1u, delay);
                uint32_t poly_out = bus_rd32(poly_base + 0x0Cu, delay);
                bus_wr32(sam_poly_base + fir_out_idx * 4u, poly_out, delay);
                stage_packets[3]++;

                // Stage 5: AXI4-Stream -> Feed into 128-point FFT input buffer
                uint32_t fft_in_iq = poly_out & 0xFFFFu;  // real I channel, Q = 0
                bus_wr32(fft_base + 0x08u, fft_in_iq, delay);
                fir_out_idx++;
            }
        }

        // Trigger Stage 5: 128-point Radix-2 Cooley-Tukey FFT (Verilator RTL or TLM)
        bus_wr32(fft_base + 0x00u, 1u, delay);
        for (uint32_t k = 0; k < 128u; ++k) {
            bus_wr32(fft_base + 0x0Cu, k, delay);
            uint32_t bin_iq = bus_rd32(fft_base + 0x14u, delay);
            bus_wr32(sam_fft_base + k * 4u, bin_iq, delay);
            bus_wr32(la_base + k * 4u, bin_iq, delay);  // Logic Analyzer MUX capture
            stage_packets[4]++;
        }
        stream_packets_total = stage_packets[0] + stage_packets[1] + stage_packets[2] +
                               stage_packets[3] + stage_packets[4];
        frames_done++;
        irq = 1u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        const unsigned len = tx.get_data_length();
        // 18KB Calibration LUT SRAM window at offset 0x1000..0x57FF
        if (off >= 0x1000u && off + len <= 0x1000u + lut_sram.size()) {
            if (tx.is_write()) std::memcpy(lut_sram.data() + (off - 0x1000u), tx.get_data_ptr(), len);
            else std::memcpy(tx.get_data_ptr(), lut_sram.data() + (off - 0x1000u), len);
            delay += sc_core::sc_time(8, sc_core::SC_NS);
            tx.set_response_status(tlm::TLM_OK_RESPONSE);
            return;
        }
        if (len != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            switch (off) {
                case 0x00u:
                    if (data == 0x10u || data == 1u) run_axi4_stream_pipeline(delay);
                    else if (data == 2u) { frames_done = 0; irq = 0; }
                    else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
                    break;
                case 0x04u: sample_len = data; break;
                case 0x08u: mux_sel = data & 1u; break;
                case 0x10u: if (data & 1u) irq = 0u; break;
                default: tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return;
            }
        } else {
            switch (off) {
                case 0x00u: data = frames_done; break;
                case 0x04u: data = sample_len; break;
                case 0x08u: data = mux_sel; break;
                case 0x10u: data = irq; break;
                case 0x14u: data = static_cast<uint32_t>(stream_packets_total); break;
                case 0x18u: data = static_cast<uint32_t>(lut_sram.size()); break;
                default: tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(12, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Bus-Master DMA Accelerator (TileLink <-> CDC <-> AXI4 Data XBar) ---
struct DmaAccelTlm : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<DmaAccelTlm> target;
    tlm_utils::simple_initiator_socket<DmaAccelTlm> dma_master;
    uint32_t src_lo = 0, dst_lo = 0, len_bytes = 0, status = 0, irq = 0;
    uint64_t bytes_transferred = 0, bus_errors = 0;

    explicit DmaAccelTlm(sc_core::sc_module_name name)
        : sc_module(name), target("target"), dma_master("dma_master") {
        target.register_b_transport(this, &DmaAccelTlm::transport);
    }
    uint32_t irq_level() const override { return irq; }

    void execute_transfer(sc_core::sc_time &delay) {
        status = 0u;
        bool had_err = false;
        const uint32_t words = len_bytes / 4u;
        for (uint32_t i = 0; i < words; ++i) {
            uint32_t word = 0u;
            tlm::tlm_generic_payload rtx;
            rtx.set_command(tlm::TLM_READ_COMMAND);
            rtx.set_address(static_cast<uint64_t>(src_lo) + i * 4ULL);
            rtx.set_data_ptr(reinterpret_cast<unsigned char *>(&word));
            rtx.set_data_length(4u); rtx.set_streaming_width(4u);
            dma_master->b_transport(rtx, delay);
            if (!rtx.is_response_ok()) { had_err = true; bus_errors++; break; }

            tlm::tlm_generic_payload wtx;
            wtx.set_command(tlm::TLM_WRITE_COMMAND);
            wtx.set_address(static_cast<uint64_t>(dst_lo) + i * 4ULL);
            wtx.set_data_ptr(reinterpret_cast<unsigned char *>(&word));
            wtx.set_data_length(4u); wtx.set_streaming_width(4u);
            dma_master->b_transport(wtx, delay);
            if (!wtx.is_response_ok()) { had_err = true; bus_errors++; break; }
            bytes_transferred += 4ULL;
        }
        status = had_err ? 3u : 1u;
        irq = 1u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            switch (off) {
                case 0x00u: if (data == 1u) execute_transfer(delay); break;
                case 0x04u: src_lo = data; break;
                case 0x08u: dst_lo = data; break;
                case 0x0Cu: len_bytes = data; break;
                case 0x14u: if (data & 1u) irq = 0u; break;
                default: tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return;
            }
        } else {
            switch (off) {
                case 0x04u: data = src_lo; break;
                case 0x08u: data = dst_lo; break;
                case 0x0Cu: data = len_bytes; break;
                case 0x10u: data = status; break;
                case 0x14u: data = irq; break;
                default: tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Independent JTAG-to-AXI4 Debug / Host Master (DSP Clock Domain) ---
// Drives AXI4 Control XBar & AXI4 Data XBar directly to inject Pattern Gen RF waveforms and
// read back Logic Analyzer captures independently of the Rocket CPU!
struct JtagToAxi4Master : sc_core::sc_module, ComposedDevice {
    tlm_utils::simple_target_socket<JtagToAxi4Master> target;
    tlm_utils::simple_initiator_socket<JtagToAxi4Master> axi4_master;
    uint32_t target_addr = 0, wdata = 0, rdata = 0, tx_done = 0, irq = 0;

    explicit JtagToAxi4Master(sc_core::sc_module_name name)
        : sc_module(name), target("target"), axi4_master("axi4_master") {
        target.register_b_transport(this, &JtagToAxi4Master::transport);
    }
    uint32_t irq_level() const override { return irq; }

    void host_inject_axi4(uint64_t addr, uint32_t val, bool is_write, sc_core::sc_time &delay) {
        uint32_t buf = val;
        tlm::tlm_generic_payload mtx;
        mtx.set_command(is_write ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
        mtx.set_address(addr);
        mtx.set_data_ptr(reinterpret_cast<unsigned char *>(&buf));
        mtx.set_data_length(4u); mtx.set_streaming_width(4u);
        axi4_master->b_transport(mtx, delay);
        if (!is_write) rdata = buf;
        tx_done++;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4u) { tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return; }
        const uint64_t off = tx.get_address();
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4u);
        if (tx.is_write()) {
            switch (off) {
                case 0x00u:
                    if (data == 1u) { host_inject_axi4(target_addr, wdata, true, delay); irq = 1u; }
                    else if (data == 2u) { host_inject_axi4(target_addr, 0u, false, delay); irq = 1u; }
                    else { tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }
                    break;
                case 0x04u: target_addr = data; break;
                case 0x08u: wdata = data; break;
                case 0x10u: if (data & 1u) irq = 0u; break;
                default: tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return;
            }
        } else {
            switch (off) {
                case 0x00u: data = tx_done; break;
                case 0x04u: data = target_addr; break;
                case 0x08u: data = wdata; break;
                case 0x0Cu: data = rdata; break;
                case 0x10u: data = irq; break;
                default: tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4u);
        }
        delay += sc_core::sc_time(12, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

// --- Generic Data-Driven Multi-Fabric, Multi-Clock, Multi-Master SystemC Interconnect ---
struct MultiFabricInterconnect : sc_core::sc_module {
    tlm_utils::multi_passthrough_target_socket<MultiFabricInterconnect> in;
    tlm_utils::multi_passthrough_initiator_socket<MultiFabricInterconnect> out;

    struct MasterInfo {
        std::string name;
        int home_fabric;
        int home_clock;
        bool is_dma;
        uint64_t hits = 0;
    };

    struct FabricInfo {
        std::string name;
        std::string protocol;
        int clock_id;
        uint32_t latency_ns;
        uint64_t hits = 0;
    };

    struct RouteEntry {
        std::string name;
        uint64_t base;
        uint64_t size;
        int fabric_id;
        int clock_id;
        int out_slot;
        bool is_ram;
        bool log_mmio;
        uint64_t hits = 0;
    };

    std::vector<MasterInfo> masters;
    std::vector<FabricInfo> fabrics;
    std::vector<RouteEntry> routes;
    uint32_t cdc_latency_ns = 12u;
    uint64_t total_transactions = 0, mmio_transactions = 0, cdc_crossings = 0;
    L2CacheBase *l2 = nullptr;
    PlicTlm *plic = nullptr;
    VcdWriter *vcd = nullptr;
    std::ofstream trace;

    MultiFabricInterconnect(sc_core::sc_module_name name, const std::string &trace_path,
                            uint32_t cdc_lat_ns = 12u)
        : sc_module(name), in("in"), out("out"), cdc_latency_ns(cdc_lat_ns), trace(trace_path) {
        in.register_b_transport(this, &MultiFabricInterconnect::b_transport);
    }

    void b_transport(int master_id, tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t addr = tx.get_address();
        const int m_clock = (master_id >= 0 && static_cast<size_t>(master_id) < masters.size())
                                ? masters[static_cast<size_t>(master_id)].home_clock
                                : 0;
        const bool is_dma = (master_id >= 0 && static_cast<size_t>(master_id) < masters.size())
                                ? masters[static_cast<size_t>(master_id)].is_dma
                                : false;
        const char *m_name = (master_id >= 0 && static_cast<size_t>(master_id) < masters.size())
                                 ? masters[static_cast<size_t>(master_id)].name.c_str()
                                 : "master";
        if (master_id >= 0 && static_cast<size_t>(master_id) < masters.size()) {
            masters[static_cast<size_t>(master_id)].hits++;
        }

        for (auto &r : routes) {
            if (addr >= r.base && addr < r.base + r.size) {
                total_transactions++;
                r.hits++;
                uint32_t lat = 0u;
                const char *fname = "fabric";
                if (r.fabric_id >= 0 && static_cast<size_t>(r.fabric_id) < fabrics.size()) {
                    fabrics[static_cast<size_t>(r.fabric_id)].hits++;
                    lat += fabrics[static_cast<size_t>(r.fabric_id)].latency_ns;
                    fname = fabrics[static_cast<size_t>(r.fabric_id)].name.c_str();
                }
                const bool crossed_cdc = (m_clock != r.clock_id);
                if (crossed_cdc) {
                    cdc_crossings++;
                    lat += cdc_latency_ns;
                }
                bool l2_hit = true;
                if (r.is_ram && l2) {
                    l2_hit = l2->lookup(addr, is_dma, tx.is_write());
                    lat += l2_hit ? 4u : 36u;
                }
                delay += sc_core::sc_time(lat, sc_core::SC_NS);

                const uint64_t off = addr - r.base;
                tx.set_address(off);
                out[r.out_slot]->b_transport(tx, delay);
                tx.set_address(addr);

                if (r.log_mmio || is_dma || crossed_cdc) {
                    if (r.log_mmio || is_dma) mmio_transactions++;
                    const uint64_t now_ps = sc_core::sc_time_stamp().value();
                    if (r.log_mmio && mmio_transactions <= 4000u) {
                        uint32_t sample = 0;
                        const unsigned clen = tx.get_data_length() < 4u ? tx.get_data_length() : 4u;
                        std::memcpy(&sample, tx.get_data_ptr(), clen);
                        trace << "{\"master\":\"" << m_name << "\",\"target\":\"" << r.name
                              << "\",\"fabric\":\"" << fname << "\",\"cdc\":"
                              << (crossed_cdc ? "true" : "false") << ",\"addr\":" << addr
                              << ",\"offset\":" << off << ",\"op\":\"" << (tx.is_write() ? "W" : "R")
                              << "\",\"value\":" << sample << ",\"status\":\""
                              << (tx.is_response_ok() ? "OK" : "ERROR")
                              << "\",\"systemc_time_ps\":" << now_ps << "}\n";
                    }
                    if (vcd && (r.log_mmio || is_dma)) {
                        vcd->record(now_ps, static_cast<uint32_t>(addr), tx.is_write(), crossed_cdc,
                                    l2_hit, plic ? plic->active_source_mask() : 0u);
                    }
                }
                return;
            }
        }
        tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
    }
};

// --- RV64IMA_Zicsr + FPU CSR SystemC/TLM-2.0 CPU Initiator ---
struct Rv64CpuMaster : sc_core::sc_module {
    tlm_utils::simple_initiator_socket<Rv64CpuMaster> bus;
    unsigned hart_id = 0u;
    std::array<uint64_t, 32> x{};
    uint64_t pc = 0x80000000ULL;
    uint64_t mstatus = 0x00006000ULL;  // FS=Dirty enabled for FPU/Vector CSR compatibility
    uint64_t mie = 0, mtvec = 0, mscratch = 0, mepc = 0, mcause = 0, mtval = 0, fcsr = 0;
    uint64_t pmpcfg0 = 0;
    std::array<uint64_t, 4> pmpaddr{};
    uint64_t instructions = 0, traps_taken = 0, atomic_ops = 0;
    bool timed_out = false;
    uint64_t max_steps = 800000ULL;

    FinisherTlm *finisher = nullptr;
    ClintTlm *clint = nullptr;
    PlicTlm *plic = nullptr;

    SC_HAS_PROCESS(Rv64CpuMaster);

    Rv64CpuMaster(sc_core::sc_module_name name, unsigned hid = 0u)
        : sc_module(name), bus("bus"), hart_id(hid) {
        SC_THREAD(run);
    }

    bool bus_rw(uint64_t addr, void *ptr, unsigned len, bool is_write) {
        tlm::tlm_generic_payload tx;
        tx.set_command(is_write ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
        tx.set_address(addr);
        tx.set_data_ptr(static_cast<unsigned char *>(ptr));
        tx.set_data_length(len); tx.set_streaming_width(len);
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
        bus->b_transport(tx, delay);
        if (delay > sc_core::SC_ZERO_TIME) wait(delay);
        return tx.is_response_ok();
    }

    uint64_t read_csr(uint32_t csr) const {
        switch (csr) {
            case 0x001u: return fcsr & 0x1Fu;
            case 0x002u: return (fcsr >> 5) & 0x7u;
            case 0x003u: return fcsr;
            case 0x300u: return mstatus;
            case 0x301u: return 0x800000000014112DULL;  // RV64IMAFD + Vector/RoCC bit
            case 0x304u: return mie;
            case 0x305u: return mtvec;
            case 0x340u: return mscratch;
            case 0x341u: return mepc;
            case 0x342u: return mcause;
            case 0x343u: return mtval;
            case 0x344u: {
                uint64_t mip = 0ULL;
                if (hart_id == 0u && plic && plic->has_pending_irq()) mip |= (1ULL << 11);
                if (clint && clint->has_msip(hart_id)) mip |= (1ULL << 3);
                return mip;
            }
            case 0x3A0u: return pmpcfg0;
            case 0x3B0u: return pmpaddr[0];
            case 0x3B1u: return pmpaddr[1];
            case 0xB00u: case 0xB02u: return instructions;
            case 0xF14u: return static_cast<uint64_t>(hart_id);
            default: return 0ULL;
        }
    }

    void write_csr(uint32_t csr, uint64_t val) {
        switch (csr) {
            case 0x001u: fcsr = (fcsr & ~0x1FULL) | (val & 0x1FULL); break;
            case 0x002u: fcsr = (fcsr & ~0xE0ULL) | ((val & 7ULL) << 5); break;
            case 0x003u: fcsr = val & 0xFFULL; break;
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
            default: break;
        }
    }

    void take_trap(uint64_t cause, uint64_t tval, uint64_t fault_pc) {
        traps_taken++;
        mepc = fault_pc; mcause = cause; mtval = tval;
        const bool old_mie = (mstatus & 8ULL) != 0ULL;
        mstatus = (mstatus & ~(1ULL << 7)) | (old_mie ? (1ULL << 7) : 0ULL);
        mstatus &= ~8ULL;
        pc = mtvec & ~3ULL;
    }

    bool step_one() {
        if (finisher && finisher->halted) return false;
        if ((mstatus & 8ULL) && (mie & (1ULL << 3)) && clint && clint->has_msip(hart_id)) {
            take_trap(0x8000000000000003ULL, 0ULL, pc);
        } else if (hart_id == 0u && (mstatus & 8ULL) && (mie & (1ULL << 11)) && plic &&
                   plic->has_pending_irq()) {
            take_trap(0x800000000000000bULL, 0ULL, pc);
        }
        uint32_t insn = 0;
        if (!bus_rw(pc, &insn, 4u, false)) { take_trap(1ULL, pc, pc); return true; }
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
                const uint64_t imm = (((insn >> 31) & 1u) << 20) | (((insn >> 21) & 0x3FFu) << 1) |
                                     (((insn >> 20) & 1u) << 11) | (((insn >> 12) & 0xFFu) << 12);
                x[rd] = next_pc;
                next_pc = cur_pc + static_cast<uint64_t>(sext(imm, 21));
                break;
            }
            case 0x67u: {
                const uint64_t target = (x[rs1] + static_cast<uint64_t>(sext(insn >> 20, 12))) & ~1ULL;
                x[rd] = next_pc; next_pc = target; break;
            }
            case 0x63u: {
                const uint64_t imm = (((insn >> 31) & 1u) << 12) | (((insn >> 7) & 1u) << 11) |
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
                if (!bus_rw(addr, &buf, len, false)) { take_trap(5ULL, addr, cur_pc); trapped = true; break; }
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
                if (!bus_rw(addr, &val, 1u << (funct3 & 3u), true)) { take_trap(7ULL, addr, cur_pc); trapped = true; }
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
                    case 5u: x[rd] = (insn & 0x40000000u)
                                         ? static_cast<uint64_t>(static_cast<int64_t>(x[rs1]) >> shamt)
                                         : (x[rs1] >> shamt); break;
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
                    case 5u: r = (insn & 0x40000000u)
                                     ? static_cast<uint32_t>(static_cast<int32_t>(a) >> shamt)
                                     : (a >> shamt); break;
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
                        case 4u: x[rd] = (b == 0) ? UINT64_MAX : static_cast<uint64_t>(static_cast<int64_t>(a) / static_cast<int64_t>(b)); break;
                        case 5u: x[rd] = (b == 0) ? UINT64_MAX : (a / b); break;
                        case 6u: x[rd] = (b == 0) ? a : static_cast<uint64_t>(static_cast<int64_t>(a) % static_cast<int64_t>(b)); break;
                        case 7u: x[rd] = (b == 0) ? a : (a % b); break;
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
                        case 5u: x[rd] = (funct7 == 0x20u)
                                             ? static_cast<uint64_t>(static_cast<int64_t>(a) >> sh)
                                             : (a >> sh); break;
                        case 6u: x[rd] = a | b; break;
                        case 7u: x[rd] = a & b; break;
                        default: break;
                    }
                }
                break;
            }
            case 0x3Bu: {
                const uint32_t a = static_cast<uint32_t>(x[rs1]), b = static_cast<uint32_t>(x[rs2]);
                uint32_t r = 0;
                if (funct7 == 1u) {
                    switch (funct3) {
                        case 0u: r = a * b; break;
                        case 4u: r = (b == 0) ? UINT32_MAX : static_cast<uint32_t>(static_cast<int32_t>(a) / static_cast<int32_t>(b)); break;
                        case 5u: r = (b == 0) ? UINT32_MAX : (a / b); break;
                        case 6u: r = (b == 0) ? a : static_cast<uint32_t>(static_cast<int32_t>(a) % static_cast<int32_t>(b)); break;
                        case 7u: r = (b == 0) ? a : (a % b); break;
                        default: break;
                    }
                } else {
                    const uint32_t sh = b & 0x1Fu;
                    switch (funct3) {
                        case 0u: r = (funct7 == 0x20u) ? (a - b) : (a + b); break;
                        case 1u: r = a << sh; break;
                        case 5u: r = (funct7 == 0x20u)
                                         ? static_cast<uint32_t>(static_cast<int32_t>(a) >> sh)
                                         : (a >> sh); break;
                        default: break;
                    }
                }
                x[rd] = static_cast<uint64_t>(sext(r, 32));
                break;
            }
            case 0x0Fu: break;
            case 0x2Fu: {
                const uint32_t funct5 = (funct7 >> 2) & 0x1Fu;
                const uint64_t addr = x[rs1];
                const unsigned len = (funct3 == 3u) ? 8u : 4u;
                atomic_ops++;
                uint64_t cur = 0;
                if (!bus_rw(addr, &cur, len, false)) { take_trap(5ULL, addr, cur_pc); trapped = true; break; }
                uint64_t orig = (len == 4u) ? static_cast<uint64_t>(sext(cur, 32)) : cur;
                if (funct5 == 0x02u) { x[rd] = orig; break; }
                uint64_t src = (len == 4u) ? static_cast<uint64_t>(sext(x[rs2], 32)) : x[rs2];
                uint64_t res = (funct5 == 0x00u) ? (orig + src) : src;
                if (!bus_rw(addr, &res, len, true)) { take_trap(7ULL, addr, cur_pc); trapped = true; break; }
                x[rd] = (funct5 == 0x03u) ? 0ULL : orig;
                break;
            }
            case 0x73u: {
                const uint32_t csr = (insn >> 20) & 0xFFFu;
                if (funct3 == 0u) {
                    if (insn == 0x30200073u) {
                        const bool mpie = (mstatus & (1ULL << 7)) != 0ULL;
                        mstatus = (mstatus & ~8ULL) | (mpie ? 8ULL : 0ULL) | (1ULL << 7);
                        next_pc = mepc;
                    } else if (insn == 0x10500073u) {
                        wait(sc_core::sc_time(100, sc_core::SC_NS));
                    } else { take_trap(11ULL, 0ULL, cur_pc); trapped = true; }
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
            default: take_trap(2ULL, insn, cur_pc); trapped = true; break;
        }
        x[0] = 0ULL;
        if (!trapped) pc = next_pc;
        wait(sc_core::sc_time(20, sc_core::SC_NS));
        return true;
    }

    void run() {
        if (hart_id != 0u) {
            while (!(finisher && finisher->halted)) {
                if (!step_one()) return;
            }
            return;
        }
        for (uint64_t step = 0; step < max_steps; ++step) {
            if (!step_one()) { sc_core::sc_stop(); return; }
        }
        timed_out = true;
        sc_core::sc_stop();
    }
};

inline bool load_elf64(const std::string &path, TlmMemory &dram, uint64_t dram_base,
                       uint64_t &entry_out) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    std::vector<uint8_t> data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (data.size() < 64u || data[0] != 0x7Fu || data[1] != 'E' || data[2] != 'L' ||
        data[3] != 'F' || data[4] != 2u || data[5] != 1u) return false;
    entry_out = load_le64(data.data() + 24);
    const uint64_t phoff = load_le64(data.data() + 32);
    const uint16_t phentsize = static_cast<uint16_t>(data[54] | (data[55] << 8));
    const uint16_t phnum = static_cast<uint16_t>(data[56] | (data[57] << 8));
    for (uint16_t i = 0; i < phnum; ++i) {
        const uint64_t off = phoff + static_cast<uint64_t>(i) * phentsize;
        if (off + 56u > data.size()) return false;
        if (load_le32(data.data() + off) != 1u) continue;
        const uint64_t p_offset = load_le64(data.data() + off + 8);
        const uint64_t p_paddr = load_le64(data.data() + off + 24);
        const uint64_t p_filesz = load_le64(data.data() + off + 32);
        const uint64_t p_memsz = load_le64(data.data() + off + 40);
        if (p_paddr < dram_base || (p_paddr - dram_base) + p_memsz > dram.mem.size() ||
            p_offset + p_filesz > data.size()) return false;
        const uint64_t dst = p_paddr - dram_base;
        std::memcpy(dram.mem.data() + dst, data.data() + p_offset, p_filesz);
        if (p_memsz > p_filesz) std::memset(dram.mem.data() + dst + p_filesz, 0, p_memsz - p_filesz);
    }
    return true;
}

}  // namespace usp

#endif  // USP_COMPOSER_RUNTIME_HPP
