// SPDX-License-Identifier: Apache-2.0
// Original SystemC/TLM-2.0 fixed-point MAC accelerator package for multi-platform SoC QA.
// NOTE: This is an original synthetic MMIO32 MAC model (user.mac_npu), NOT upstream Chipyard Gemmini RTL.
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

struct MacNpu : sc_core::sc_module {
    tlm_utils::simple_target_socket<MacNpu> target;
    uint32_t op_a = 0;
    uint32_t op_b = 0;
    uint32_t acc = 0;
    uint32_t ops_done = 0;
    uint32_t irq = 0;

    explicit MacNpu(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &MacNpu::transport);
    }

    void reset_state() {
        op_a = 0;
        op_b = 0;
        acc = 0;
        ops_done = 0;
        irq = 0;
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
                case 0x00:  // ctrl_cmd: 1 = execute MAC, 2 = clear accumulator
                    if (data == 1u) {
                        acc = static_cast<uint32_t>(acc + (op_a * op_b));
                        ops_done = static_cast<uint32_t>(ops_done + 1u);
                        irq = 1u;
                        delay += sc_core::sc_time(25, sc_core::SC_NS);
                    } else if (data == 2u) {
                        acc = 0u;
                        ops_done = 0u;
                        irq = 0u;
                        delay += sc_core::sc_time(10, sc_core::SC_NS);
                    } else {
                        tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                        return;
                    }
                    break;
                case 0x04:  // op_a
                    op_a = data;
                    delay += sc_core::sc_time(10, sc_core::SC_NS);
                    break;
                case 0x08:  // op_b
                    op_b = data;
                    delay += sc_core::sc_time(10, sc_core::SC_NS);
                    break;
                case 0x0C:  // acc_result is read-only
                    tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE);
                    return;
                case 0x10:  // irq_status (write-one-clear)
                    if (data & 1u) {
                        irq = 0u;
                    }
                    delay += sc_core::sc_time(10, sc_core::SC_NS);
                    break;
                default:
                    tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
                    return;
            }
        } else {
            switch (offset) {
                case 0x00:
                    data = ops_done;
                    break;
                case 0x04:
                    data = op_a;
                    break;
                case 0x08:
                    data = op_b;
                    break;
                case 0x0C:
                    data = acc;
                    break;
                case 0x10:
                    data = irq;
                    break;
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
    MacNpu model("mac_npu");
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
            if (status != 0u) {
                value = 0u;
            }
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
