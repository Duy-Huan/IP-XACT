// SPDX-License-Identifier: Apache-2.0
// Original functional model for the explicit usp-mmio32-v1 adapter example.
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_initiator_socket.h>
#include <tlm_utils/simple_target_socket.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <endian.h>
#include <array>
#include <cstring>
#include <fstream>

struct Counter : sc_core::sc_module {
    tlm_utils::simple_target_socket<Counter> target;
    uint32_t value = 0, irq = 0;
    Counter(sc_core::sc_module_name name) : sc_module(name), target("target") {
        target.register_b_transport(this, &Counter::transport);
    }
    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {
        uint32_t data;
        std::memcpy(&data, tx.get_data_ptr(), 4);
        auto offset = tx.get_address();
        if ((offset != 0 && offset != 4) || tx.get_data_length() != 4) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return;
        }
        if (tx.is_write()) {
            if (offset == 0) { value = data; irq = 1; }
            else if (data == 1) irq = 0;
        } else {
            data = offset == 0 ? value : irq;
            std::memcpy(tx.get_data_ptr(), &data, 4);
        }
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }
};
struct Initiator : sc_core::sc_module {
    tlm_utils::simple_initiator_socket<Initiator> socket;
    Initiator(sc_core::sc_module_name name) : sc_module(name), socket("socket") {}
};

static bool transfer(int fd, void *buffer, bool writing) {
    auto *bytes = static_cast<char *>(buffer);
    for (unsigned n = 0; n < 20;) {
        ssize_t rc = writing ? send(fd, bytes + n, 20 - n, MSG_NOSIGNAL)
                             : recv(fd, bytes + n, 20 - n, 0);
        if (rc < 0 && errno == EINTR) continue;
        if (rc <= 0) return false;
        n += rc;
    }
    return true;
}
int sc_main(int argc, char **argv) {
    if (argc != 3) return 2;
    sc_core::sc_set_time_resolution(1, sc_core::SC_PS);
    Counter model("counter");
    Initiator host("host");
    host.socket.bind(model.target);
    sc_core::sc_start(sc_core::SC_ZERO_TIME);
    std::ofstream trace(argv[2]);
    int server = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{}; addr.sun_family = AF_UNIX;
    if (std::strlen(argv[1]) >= sizeof(addr.sun_path) || !trace || server < 0) return 2;
    std::strcpy(addr.sun_path, argv[1]);
    if (bind(server, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) || listen(server, 1)) return 2;
    int fd = accept(server, nullptr, nullptr);
    if (fd < 0) return 2;
    std::array<uint32_t, 5> req{}, res{};
    while (transfer(fd, req.data(), false)) {
        for (auto &word : req) word = le32toh(word);
        uint32_t op = req[2], offset = req[3], data = req[4], value = data, status = 0;
        if (req[0] != 0x31505355 || op > 2) return 2;
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
        if (op == 2) model.value = model.irq = 0;
        else {
            tlm::tlm_generic_payload tx;
            tx.set_command(op ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
            tx.set_address(offset); tx.set_data_ptr(reinterpret_cast<unsigned char *>(&value));
            tx.set_data_length(4); tx.set_streaming_width(4);
            host.socket->b_transport(tx, delay);
            status = tx.is_response_ok() ? 0 : 1;
        }
        sc_core::sc_start(delay == sc_core::SC_ZERO_TIME ? sc_core::sc_time(1, sc_core::SC_NS) : delay);
        trace << "{\"op\":" << op << ",\"offset\":" << offset << ",\"data\":" << data
              << ",\"value\":" << value << ",\"status\":" << status << ",\"irq\":" << model.irq
              << ",\"systemc_time_ps\":" << sc_core::sc_time_stamp().value() << "}\n" << std::flush;
        res = {htole32(0x31505355), htole32(req[1]), htole32(status), htole32(value), htole32(model.irq)};
        if (!transfer(fd, res.data(), true)) return 2;
    }
    close(fd); close(server); unlink(argv[1]); return 0;
}
