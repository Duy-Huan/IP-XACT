// SPDX-License-Identifier: Apache-2.0
// USP `usp-bus-v2` SystemC/TLM-2.0 Out-of-Process IP Plugin (with `usp-mmio32-v1` compat).
// Demonstrates:
//   1. Wire-level backward compatibility with legacy 20-byte `usp-mmio32-v1` frames
//   2. 64-bit multi-beat burst reads & writes (`USP_OP_BURST_READ` / `USP_OP_BURST_WRITE`)
//   3. Reverse bus-master DMA (`USP_OP_DMA_READ` / `USP_OP_DMA_WRITE`) autonomously reading
//      and writing SoC/QEMU RAM through MultiFabricInterconnect + L2$ snoop coherency
//   4. Quantum time synchronization (`USP_OP_TIME_SYNC`) with SystemC / QEMU / gem5
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_initiator_socket.h>
#include <tlm_utils/simple_target_socket.h>

#include <endian.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <functional>
#include <vector>

#include "usp_bus_protocol.hpp"

static bool io_all(int fd, void *buf, size_t len, bool writing) {
    auto *p = static_cast<uint8_t *>(buf);
    for (size_t done = 0; done < len;) {
        ssize_t rc = writing ? send(fd, p + done, len - done, MSG_NOSIGNAL)
                             : recv(fd, p + done, len - done, 0);
        if (rc < 0 && errno == EINTR) continue;
        if (rc <= 0) return false;
        done += static_cast<size_t>(rc);
    }
    return true;
}

struct DmaBurstV2Model : sc_core::sc_module {
    tlm_utils::simple_target_socket<DmaBurstV2Model> target;
    std::array<uint64_t, 64> scratch{};
    uint32_t done_count = 0;
    uint32_t irq = 0;
    uint64_t dma_src = 0;
    uint64_t dma_dst = 0;
    uint32_t dma_len = 0;
    uint32_t dma_bytes_done = 0;
    uint64_t xor_key = 0x5A5A5A5A5A5A5A5AULL;
    uint64_t dma_checksum = 0;
    uint32_t quantum_syncs = 0;
    uint64_t last_sync_ps = 0;

    std::function<bool(uint64_t, uint64_t, uint32_t)> dma_callback;

    explicit DmaBurstV2Model(sc_core::sc_module_name nm) : sc_module(nm), target("target") {
        target.register_b_transport(this, &DmaBurstV2Model::transport);
    }

    void reset_state() {
        scratch.fill(0ULL);
        done_count = 0;
        irq = 0;
        dma_src = 0;
        dma_dst = 0;
        dma_len = 0;
        dma_bytes_done = 0;
        xor_key = 0x5A5A5A5A5A5A5A5AULL;
        dma_checksum = 0;
        quantum_syncs = 0;
        last_sync_ps = 0;
    }

    static void update_u64_lo_hi(uint64_t &reg, uint64_t val, uint32_t len, bool high_half) {
        if (len == 8u) {
            reg = val;
        } else if (high_half) {
            reg = (reg & 0x00000000FFFFFFFFULL) | ((val & 0xFFFFFFFFULL) << 32);
        } else {
            reg = (reg & 0xFFFFFFFF00000000ULL) | (val & 0xFFFFFFFFULL);
        }
    }

    static uint64_t read_u64_lo_hi(uint64_t reg, uint32_t len, bool high_half) {
        if (len == 8u) return reg;
        return high_half ? ((reg >> 32) & 0xFFFFFFFFULL) : (reg & 0xFFFFFFFFULL);
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        const uint64_t off = tx.get_address();
        const uint32_t len = tx.get_data_length();

        // 64-bit multi-beat burst scratchpad window [0x100, 0x300)
        if (off >= 0x100ULL && off < 0x300ULL) {
            if (len == 0u || (off & 3u) != 0u || off + len > 0x300ULL) {
                tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                return;
            }
            auto *base = reinterpret_cast<uint8_t *>(scratch.data()) + (off - 0x100ULL);
            if (tx.is_write()) {
                std::memcpy(base, tx.get_data_ptr(), len);
            } else {
                std::memcpy(tx.get_data_ptr(), base, len);
            }
            delay += sc_core::sc_time(8 + (len / 8u) * 2u, sc_core::SC_NS);
            tx.set_response_status(tlm::TLM_OK_RESPONSE);
            return;
        }

        if (len != 4u && len != 8u) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }

        uint64_t val = 0;
        std::memcpy(&val, tx.get_data_ptr(), len);

        if (tx.is_write()) {
            switch (off) {
                case 0x00:
                    if ((val & 1ULL) != 0ULL) {
                        if (!dma_callback || dma_len == 0u || (dma_len & 7u) != 0u ||
                            dma_len > USP_BUS_V2_MAX_BURST_BYTES) {
                            tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                            return;
                        }
                        if (!dma_callback(dma_src, dma_dst, dma_len)) {
                            tx.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE);
                            return;
                        }
                        done_count++;
                        irq = 1u;
                    } else if ((val & 2ULL) != 0ULL) {
                        reset_state();
                    } else {
                        tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                        return;
                    }
                    break;
                case 0x04:
                    if (val & 1ULL) irq = 0u;
                    break;
                case 0x08: update_u64_lo_hi(dma_src, val, len, false); break;
                case 0x0C: update_u64_lo_hi(dma_src, val, 4u, true); break;
                case 0x10: update_u64_lo_hi(dma_dst, val, len, false); break;
                case 0x14: update_u64_lo_hi(dma_dst, val, 4u, true); break;
                case 0x18: dma_len = static_cast<uint32_t>(val); break;
                case 0x1C:
                    tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                    return;
                case 0x20: update_u64_lo_hi(xor_key, val, len, false); break;
                case 0x24: update_u64_lo_hi(xor_key, val, 4u, true); break;
                case 0x28:
                case 0x2C:
                case 0x30:
                case 0x38:
                case 0x3C:
                    tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                    return;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
        } else {
            switch (off) {
                case 0x00: val = done_count; break;
                case 0x04: val = irq; break;
                case 0x08: val = read_u64_lo_hi(dma_src, len, false); break;
                case 0x0C: val = read_u64_lo_hi(dma_src, 4u, true); break;
                case 0x10: val = read_u64_lo_hi(dma_dst, len, false); break;
                case 0x14: val = read_u64_lo_hi(dma_dst, 4u, true); break;
                case 0x18: val = dma_len; break;
                case 0x1C: val = dma_bytes_done; break;
                case 0x20: val = read_u64_lo_hi(xor_key, len, false); break;
                case 0x24: val = read_u64_lo_hi(xor_key, 4u, true); break;
                case 0x28: val = read_u64_lo_hi(dma_checksum, len, false); break;
                case 0x2C: val = read_u64_lo_hi(dma_checksum, 4u, true); break;
                case 0x30: val = quantum_syncs; break;
                case 0x38: val = read_u64_lo_hi(last_sync_ps, len, false); break;
                case 0x3C: val = read_u64_lo_hi(last_sync_ps, 4u, true); break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
            std::memcpy(tx.get_data_ptr(), &val, len);
        }
        delay += sc_core::sc_time(12, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

struct Initiator : sc_core::sc_module {
    tlm_utils::simple_initiator_socket<Initiator> socket;
    explicit Initiator(sc_core::sc_module_name nm) : sc_module(nm), socket("socket") {}
};

int sc_main(int argc, char **argv) {
    if (argc != 3) return 2;
    sc_core::sc_set_time_resolution(1, sc_core::SC_PS);
    DmaBurstV2Model model("dma_burst_v2");
    Initiator host("host");
    host.socket.bind(model.target);
    sc_core::sc_start(sc_core::SC_ZERO_TIME);

    std::ofstream trace(argv[2]);
    int srv = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (std::strlen(argv[1]) >= sizeof(addr.sun_path) || !trace || srv < 0) return 2;
    std::strcpy(addr.sun_path, argv[1]);
    unlink(argv[1]);
    if (bind(srv, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) || listen(srv, 1)) return 2;
    int fd = accept(srv, nullptr, nullptr);
    if (fd < 0) return 2;

    uint32_t dma_seq = 0x80000000u;

    // Reverse bus-master DMA callback: sends USP_OP_DMA_READ and USP_OP_DMA_WRITE back to SoC!
    model.dma_callback = [&](uint64_t src_addr, uint64_t dst_addr, uint32_t nbytes) -> bool {
        // 1. Autonomous DMA_READ from SoC/QEMU RAM
        UspBusV2Frame rd_req{};
        rd_req.magic = USP_BUS_V2_MAGIC;
        rd_req.seq = ++dma_seq;
        rd_req.op = USP_OP_DMA_READ;
        rd_req.length = nbytes;
        rd_req.flags = USP_FLAG_BURST64;
        rd_req.addr = src_addr;
        rd_req.time_ps = sc_core::sc_time_stamp().value();
        if (!io_all(fd, &rd_req, sizeof(rd_req), true)) return false;

        UspBusV2Frame rd_rsp{};
        if (!io_all(fd, &rd_rsp, sizeof(rd_rsp), false)) return false;
        if (rd_rsp.magic != USP_BUS_V2_MAGIC || rd_rsp.status != USP_STATUS_OK ||
            rd_rsp.length != nbytes) {
            return false;
        }
        std::vector<uint8_t> buf(nbytes, 0);
        if ((rd_rsp.flags & USP_FLAG_HAS_PAYLOAD) != 0u && nbytes > 0u) {
            if (!io_all(fd, buf.data(), nbytes, false)) return false;
        }

        // 2. Transform every 64-bit word: out = (in ^ xor_key) + 1ULL; compute 64-bit XOR checksum
        uint64_t cksum = 0ULL;
        const size_t nwords = nbytes / 8u;
        for (size_t i = 0; i < nwords; ++i) {
            uint64_t w = 0;
            std::memcpy(&w, buf.data() + i * 8u, 8u);
            w = (w ^ model.xor_key) + 1ULL;
            cksum ^= w;
            std::memcpy(buf.data() + i * 8u, &w, 8u);
        }
        model.dma_checksum = cksum;

        // 3. Autonomous DMA_WRITE transformed payload back to SoC/QEMU RAM
        UspBusV2Frame wr_req{};
        wr_req.magic = USP_BUS_V2_MAGIC;
        wr_req.seq = ++dma_seq;
        wr_req.op = USP_OP_DMA_WRITE;
        wr_req.length = nbytes;
        wr_req.flags = USP_FLAG_HAS_PAYLOAD | USP_FLAG_BURST64;
        wr_req.addr = dst_addr;
        wr_req.time_ps = sc_core::sc_time_stamp().value();
        if (!io_all(fd, &wr_req, sizeof(wr_req), true)) return false;
        if (!io_all(fd, buf.data(), nbytes, true)) return false;

        UspBusV2Frame wr_rsp{};
        if (!io_all(fd, &wr_rsp, sizeof(wr_rsp), false)) return false;
        if (wr_rsp.magic != USP_BUS_V2_MAGIC || wr_rsp.status != USP_STATUS_OK) return false;

        model.dma_bytes_done += nbytes * 2u;
        sc_core::sc_start(sc_core::sc_time(nbytes, sc_core::SC_NS));
        trace << "{\"op\":" << USP_OP_DMA_WRITE << ",\"offset\":" << dst_addr
              << ",\"length\":" << nbytes << ",\"status\":0,\"irq\":1,\"systemc_time_ps\":"
              << sc_core::sc_time_stamp().value() << "}\n"
              << std::flush;
        return true;
    };

    uint32_t magic_raw = 0;
    while (io_all(fd, &magic_raw, 4u, false)) {
        const uint32_t magic = le32toh(magic_raw);
        if (magic == USP_MMIO32_V1_MAGIC) {
            // Backward-compatible 20-byte `usp-mmio32-v1` wire frame!
            std::array<uint32_t, 4> tail{};
            if (!io_all(fd, tail.data(), 16u, false)) return 2;
            const uint32_t seq = le32toh(tail[0]);
            const uint32_t op = le32toh(tail[1]);
            const uint32_t off = le32toh(tail[2]);
            uint32_t val = le32toh(tail[3]);
            uint32_t status = USP_STATUS_OK;
            sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
            if (op == USP_OP_RESET) {
                model.reset_state();
                val = 0u;
            } else if (op == USP_OP_READ || op == USP_OP_WRITE) {
                tlm::tlm_generic_payload tx;
                tx.set_command(op == USP_OP_WRITE ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
                tx.set_address(off);
                tx.set_data_ptr(reinterpret_cast<unsigned char *>(&val));
                tx.set_data_length(4u);
                tx.set_streaming_width(4u);
                host.socket->b_transport(tx, delay);
                status = tx.is_response_ok() ? USP_STATUS_OK : USP_STATUS_BUS_ERR;
                if (status != USP_STATUS_OK) val = 0u;
            } else {
                status = USP_STATUS_CMD_ERR;
                val = 0u;
            }
            sc_core::sc_start(
                delay == sc_core::SC_ZERO_TIME ? sc_core::sc_time(1, sc_core::SC_NS) : delay);
            trace << "{\"proto\":\"v1\",\"op\":" << op << ",\"offset\":" << off
                  << ",\"value\":" << val << ",\"status\":" << status << ",\"irq\":" << model.irq
                  << ",\"systemc_time_ps\":" << sc_core::sc_time_stamp().value() << "}\n"
                  << std::flush;
            std::array<uint32_t, 5> res{
                htole32(USP_MMIO32_V1_MAGIC), htole32(seq), htole32(status),
                htole32(val), htole32(model.irq)};
            if (!io_all(fd, res.data(), 20u, true)) return 2;
            continue;
        }

        if (magic != USP_BUS_V2_MAGIC) return 2;

        UspBusV2Frame req{};
        req.magic = magic;
        if (!io_all(fd, reinterpret_cast<uint8_t *>(&req) + 4u, sizeof(req) - 4u, false)) return 2;

        std::vector<uint8_t> payload;
        if ((req.flags & USP_FLAG_HAS_PAYLOAD) != 0u && req.length > 0u) {
            if (req.length > USP_BUS_V2_MAX_BURST_BYTES) return 2;
            payload.resize(req.length);
            if (!io_all(fd, payload.data(), req.length, false)) return 2;
        }

        UspBusV2Frame rsp{};
        rsp.magic = USP_BUS_V2_MAGIC;
        rsp.seq = req.seq;
        rsp.op = req.op | USP_OP_RESPONSE;
        rsp.status = USP_STATUS_OK;
        rsp.addr = req.addr;
        std::vector<uint8_t> rsp_payload;
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;

        if (req.op == USP_OP_RESET) {
            model.reset_state();
            rsp.data = 0ULL;
        } else if (req.op == USP_OP_TIME_SYNC) {
            const uint64_t now_ps = sc_core::sc_time_stamp().value();
            if (req.time_ps > now_ps) {
                sc_core::sc_start(
                    sc_core::sc_time(req.time_ps - now_ps, sc_core::SC_PS));
            } else {
                sc_core::sc_start(sc_core::sc_time(1, sc_core::SC_NS));
            }
            model.quantum_syncs++;
            model.last_sync_ps = sc_core::sc_time_stamp().value();
            rsp.data = model.quantum_syncs;
            rsp.flags = USP_FLAG_QUANTUM_BARRIER;
        } else if (req.op == USP_OP_READ || req.op == USP_OP_WRITE) {
            const uint32_t n = (req.length == 8u) ? 8u : 4u;
            uint64_t val = req.data;
            tlm::tlm_generic_payload tx;
            tx.set_command(req.op == USP_OP_WRITE ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
            tx.set_address(req.addr);
            tx.set_data_ptr(reinterpret_cast<unsigned char *>(&val));
            tx.set_data_length(n);
            tx.set_streaming_width(n);
            host.socket->b_transport(tx, delay);
            rsp.status = tx.is_response_ok() ? USP_STATUS_OK : USP_STATUS_BUS_ERR;
            rsp.data = (rsp.status == USP_STATUS_OK) ? val : 0ULL;
            rsp.length = n;
        } else if (req.op == USP_OP_BURST_WRITE) {
            if (payload.empty() || (payload.size() & 7u) != 0u) {
                rsp.status = USP_STATUS_CMD_ERR;
            } else {
                tlm::tlm_generic_payload tx;
                tx.set_command(tlm::TLM_WRITE_COMMAND);
                tx.set_address(req.addr);
                tx.set_data_ptr(payload.data());
                tx.set_data_length(static_cast<unsigned>(payload.size()));
                tx.set_streaming_width(static_cast<unsigned>(payload.size()));
                host.socket->b_transport(tx, delay);
                rsp.status = tx.is_response_ok() ? USP_STATUS_OK : USP_STATUS_BUS_ERR;
                rsp.length = static_cast<uint32_t>(payload.size());
                rsp.flags = USP_FLAG_BURST64;
            }
        } else if (req.op == USP_OP_BURST_READ) {
            if (req.length == 0u || (req.length & 7u) != 0u ||
                req.length > USP_BUS_V2_MAX_BURST_BYTES) {
                rsp.status = USP_STATUS_CMD_ERR;
            } else {
                rsp_payload.assign(req.length, 0);
                tlm::tlm_generic_payload tx;
                tx.set_command(tlm::TLM_READ_COMMAND);
                tx.set_address(req.addr);
                tx.set_data_ptr(rsp_payload.data());
                tx.set_data_length(req.length);
                tx.set_streaming_width(req.length);
                host.socket->b_transport(tx, delay);
                rsp.status = tx.is_response_ok() ? USP_STATUS_OK : USP_STATUS_BUS_ERR;
                if (rsp.status == USP_STATUS_OK) {
                    rsp.length = req.length;
                    rsp.flags = USP_FLAG_HAS_PAYLOAD | USP_FLAG_BURST64;
                } else {
                    rsp_payload.clear();
                    rsp.length = 0u;
                }
            }
        } else {
            rsp.status = USP_STATUS_CMD_ERR;
        }

        if (req.op != USP_OP_TIME_SYNC) {
            sc_core::sc_start(
                delay == sc_core::SC_ZERO_TIME ? sc_core::sc_time(1, sc_core::SC_NS) : delay);
        }
        rsp.irq_mask = model.irq;
        rsp.time_ps = sc_core::sc_time_stamp().value();

        trace << "{\"proto\":\"v2\",\"op\":" << req.op << ",\"offset\":" << req.addr
              << ",\"length\":" << req.length << ",\"value\":" << rsp.data
              << ",\"status\":" << rsp.status << ",\"irq\":" << rsp.irq_mask
              << ",\"systemc_time_ps\":" << rsp.time_ps << "}\n"
              << std::flush;

        if (!io_all(fd, &rsp, sizeof(rsp), true)) return 2;
        if (!rsp_payload.empty() && !io_all(fd, rsp_payload.data(), rsp_payload.size(), true)) {
            return 2;
        }
    }

    close(fd);
    close(srv);
    unlink(argv[1]);
    return 0;
}
