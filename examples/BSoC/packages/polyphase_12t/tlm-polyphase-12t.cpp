// SPDX-License-Identifier: Apache-2.0
// Original SystemC/TLM-2.0 12-Tap Fixed Poly-Phase Channelizing Filter Plugin (bsoc.polyphase_12t).
// Speaks the universal `usp-mmio32-v1` Unix-socket IPC contract so the EXACT SAME compiled IP
// package binary binds live into SystemC/TLM-2.0 (BSoC AXI4 Control XBar), QEMU, and gem5.
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_initiator_socket.h>
#include <tlm_utils/simple_target_socket.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <endian.h>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fstream>

struct Polyphase12Tap : sc_core::sc_module {
    tlm_utils::simple_target_socket<Polyphase12Tap> target;
    static constexpr std::array<int32_t, 12> kTaps = {
        16, 32, 64, 96, 128, 176, 176, 128, 96, 64, 32, 16
    };
    std::array<int32_t, 12> delay_line{};
    uint32_t phase_sel = 0;
    int32_t in_sample = 0;
    int32_t out_sample = 0;
    uint32_t samples_done = 0;
    uint32_t irq = 0;

    explicit Polyphase12Tap(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &Polyphase12Tap::transport);
    }

    void reset_state() {
        delay_line.fill(0);
        phase_sel = 0;
        in_sample = 0;
        out_sample = 0;
        samples_done = 0;
        irq = 0;
    }

    void step_filter() {
        for (int i = 11; i > 0; --i) {
            delay_line[static_cast<size_t>(i)] = delay_line[static_cast<size_t>(i - 1)];
        }
        delay_line[0] = in_sample;
        int64_t acc = 0;
        for (size_t i = 0; i < 12; ++i) {
            acc += static_cast<int64_t>(delay_line[i]) * static_cast<int64_t>(kTaps[i]);
        }
        out_sample = static_cast<int32_t>(acc >> 10);
        samples_done++;
        irq = 1u;
    }

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        if (tx.get_data_length() != 4) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }
        uint32_t data = 0;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        const uint64_t offset = tx.get_address();

        if (tx.is_write()) {
            switch (offset) {
                case 0x00:
                    if (data == 1u) {
                        step_filter();
                        delay += sc_core::sc_time(30, sc_core::SC_NS);
                    } else if (data == 2u) {
                        reset_state();
                        delay += sc_core::sc_time(10, sc_core::SC_NS);
                    } else {
                        tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                        return;
                    }
                    break;
                case 0x04:
                    phase_sel = data & 3u;
                    delay += sc_core::sc_time(10, sc_core::SC_NS);
                    break;
                case 0x08:
                    in_sample = static_cast<int32_t>(data);
                    delay += sc_core::sc_time(10, sc_core::SC_NS);
                    break;
                case 0x0C:
                case 0x14:
                    tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                    return;
                case 0x10:
                    if (data & 1u) irq = 0u;
                    delay += sc_core::sc_time(10, sc_core::SC_NS);
                    break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
        } else {
            switch (offset) {
                case 0x00: data = samples_done; break;
                case 0x04: data = phase_sel; break;
                case 0x08: data = static_cast<uint32_t>(in_sample); break;
                case 0x0C: data = static_cast<uint32_t>(out_sample); break;
                case 0x10: data = irq; break;
                case 0x14: data = 1024u; break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
            std::memcpy(tx.get_data_ptr(), &data, 4);
            delay += sc_core::sc_time(10, sc_core::SC_NS);
        }
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};

struct Initiator : sc_core::sc_module {
    tlm_utils::simple_initiator_socket<Initiator> socket;
    explicit Initiator(sc_core::sc_module_name name) : sc_module(name), socket("socket") {}
};

static bool transfer(int fd, void *buffer, bool writing) {
    auto *bytes = static_cast<char *>(buffer);
    for (unsigned n = 0; n < 20;) {
        ssize_t rc = writing ? send(fd, bytes + n, 20 - n, MSG_NOSIGNAL)
                             : recv(fd, bytes + n, 20 - n, 0);
        if (rc < 0 && errno == EINTR) continue;
        if (rc <= 0) return false;
        n += static_cast<unsigned>(rc);
    }
    return true;
}

int sc_main(int argc, char **argv) {
    if (argc != 3) return 2;
    sc_core::sc_set_time_resolution(1, sc_core::SC_PS);
    Polyphase12Tap model("polyphase_12t");
    Initiator host("host");
    host.socket.bind(model.target);
    sc_core::sc_start(sc_core::SC_ZERO_TIME);

    std::ofstream trace(argv[2]);
    int server = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (std::strlen(argv[1]) >= sizeof(addr.sun_path) || !trace || server < 0) return 2;
    std::strcpy(addr.sun_path, argv[1]);
    if (bind(server, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) || listen(server, 1)) {
        return 2;
    }
    int fd = accept(server, nullptr, nullptr);
    if (fd < 0) return 2;

    std::array<uint32_t, 5> req{}, res{};
    while (transfer(fd, req.data(), false)) {
        for (auto &word : req) word = le32toh(word);
        const uint32_t op = req[2];
        const uint32_t offset = req[3];
        const uint32_t data = req[4];
        uint32_t value = data;
        uint32_t status = 0;
        if (req[0] != 0x31505355u || op > 2u) return 2;
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
        if (op == 2u) {
            model.reset_state();
            value = 0u;
        } else {
            tlm::tlm_generic_payload tx;
            tx.set_command(op ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
            tx.set_address(offset);
            tx.set_data_ptr(reinterpret_cast<unsigned char *>(&value));
            tx.set_data_length(4);
            tx.set_streaming_width(4);
            host.socket->b_transport(tx, delay);
            status = tx.is_response_ok() ? 0u : 1u;
            if (status != 0u) value = 0u;
        }
        sc_core::sc_start(
            delay == sc_core::SC_ZERO_TIME ? sc_core::sc_time(1, sc_core::SC_NS) : delay);
        trace << "{\"op\":" << op << ",\"offset\":" << offset << ",\"data\":" << data
              << ",\"value\":" << value << ",\"status\":" << status << ",\"irq\":" << model.irq
              << ",\"systemc_time_ps\":" << sc_core::sc_time_stamp().value() << "}\n"
              << std::flush;
        res = {htole32(0x31505355u), htole32(req[1]), htole32(status), htole32(value),
               htole32(model.irq)};
        if (!transfer(fd, res.data(), true)) return 2;
    }
    close(fd);
    close(server);
    unlink(argv[1]);
    return 0;
}
