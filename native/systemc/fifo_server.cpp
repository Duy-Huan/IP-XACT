// SPDX-License-Identifier: Apache-2.0
// Original transaction adapter; OpenTitan RTL is compiled separately from pinned upstream.
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_target_socket.h>
#include <tlm_utils/simple_initiator_socket.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <endian.h>
#include <cerrno>
#include <cstring>
#include <deque>
#include <fstream>
#include <stdexcept>
#ifdef USP_RTL
#include "Vusp_fifo.h"
#include "verilated.h"
#endif
using namespace sc_core;
static_assert(SC_VERSION_MAJOR == 3 && SC_VERSION_MINOR == 0 && SC_VERSION_PATCH == 1,
              "This adapter pins SystemC 3.0.1");
struct Target : sc_module {
    tlm_utils::simple_target_socket<Target> socket;
#ifndef USP_RTL
    std::deque<uint32_t> fifo;
#else
    Vusp_fifo dut;
    void cycle() { dut.clk_i = 0; dut.eval(); dut.clk_i = 1; dut.eval(); dut.clk_i = 0; dut.eval(); }
#endif
    bool pending = false;
    Target(sc_module_name name) : sc_module(name), socket("target") {
        socket.register_b_transport(this, &Target::transport);
#ifdef USP_RTL
        reset();
#endif
    }
    void reset() {
#ifndef USP_RTL
        fifo.clear();
#else
        dut.rst_ni = 0; dut.wvalid_i = 0; dut.rready_i = 0; cycle();
        dut.rst_ni = 1; cycle(); cycle();
#endif
        pending = false;
    }
    void transport(tlm::tlm_generic_payload &tx, sc_time &delay) {
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
        if (tx.get_data_length() != 4 || tx.get_streaming_width() != 4 || tx.get_byte_enable_ptr()) {
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return;
        }
        uint32_t data; std::memcpy(&data, tx.get_data_ptr(), 4);
        auto address = tx.get_address();
        if (address == 8 && tx.is_write()) { reset(); }
        else if (address == 4 && tx.is_read()) { data = pending; }
        else if (address == 0 && tx.is_write()) {
#ifndef USP_RTL
            if (fifo.size() == 4) { tx.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE); return; }
            fifo.push_back(data);
#else
            if (!dut.wready_o) { tx.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE); return; }
            dut.wvalid_i = 1; dut.wdata_i = data; cycle(); dut.wvalid_i = 0; dut.eval();
#endif
        } else if (address == 0 && tx.is_read()) {
#ifndef USP_RTL
            if (fifo.empty()) { tx.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE); return; }
            data = fifo.front(); fifo.pop_front();
#else
            if (!dut.rvalid_o) { tx.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE); return; }
            data = dut.rdata_o; dut.rready_i = 1; cycle(); dut.rready_i = 0; dut.eval();
#endif
        } else { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return; }
#ifndef USP_RTL
        pending = !fifo.empty();
#else
        pending = dut.rvalid_o;
#endif
        std::memcpy(tx.get_data_ptr(), &data, 4);
        delay += sc_time(10, SC_NS);
    }
};
struct Initiator : sc_module {
    tlm_utils::simple_initiator_socket<Initiator> socket;
    Initiator(sc_module_name name) : sc_module(name), socket("initiator") {}
};
static bool transfer(int fd, void *buffer, size_t bytes, bool writing) {
    size_t position = 0;
    while (position < bytes) {
        ssize_t count = writing ? send(fd, (char *)buffer + position, bytes-position, MSG_NOSIGNAL)
                                : recv(fd, (char *)buffer + position, bytes-position, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        position += count;
    }
    return true;
}
int sc_main(int argc, char **argv) {
    if (argc == 2 && !std::strcmp(argv[1], "--version")) { std::cout << SC_VERSION << std::endl; return 0; }
    if (argc != 3 || std::strlen(argv[1]) >= sizeof(sockaddr_un::sun_path)) return 2;
#ifdef USP_RTL
    Verilated::commandArgs(argc, argv);
#endif
    sc_set_time_resolution(1, SC_PS);
    Target target("model"); Initiator initiator("bus"); initiator.socket.bind(target.socket);
    sc_start(SC_ZERO_TIME);
    int server = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un address{}; address.sun_family = AF_UNIX; std::strcpy(address.sun_path, argv[1]);
    if (server < 0 || bind(server, (sockaddr *)&address, sizeof(address)) || listen(server,1)) return 3;
    int client = accept(server, nullptr, nullptr); if (client < 0) return 4;
    std::ofstream trace(argv[2]);
    uint32_t expected_sequence = 1;
    uint32_t request[5];
    while (transfer(client, request, sizeof(request), false)) {
        for (auto &value : request) value = le32toh(value);
        uint32_t data = request[4];
        uint32_t status = 0;
        if (request[0] != 0x31505355 || request[1] != expected_sequence++ || request[2] > 2) status = 1;
        tlm::tlm_generic_payload tx;
        sc_time delay = SC_ZERO_TIME;
        tx.set_address(request[2] == 2 ? 8 : request[3]);
        tx.set_command(request[2] == 0 ? tlm::TLM_READ_COMMAND : tlm::TLM_WRITE_COMMAND);
        tx.set_data_ptr(reinterpret_cast<unsigned char *>(&data)); tx.set_data_length(4); tx.set_streaming_width(4);
        if (!status) { initiator.socket->b_transport(tx, delay); status = tx.is_response_error(); sc_start(delay, SC_RUN_TO_TIME); }
        uint32_t response[5] = {0x31505355, request[1], status, data, target.pending};
        trace << "{\"seq\":" << request[1] << ",\"op\":" << request[2] << ",\"offset\":" << request[3]
              << ",\"data\":" << request[4] << ",\"status\":" << status << ",\"value\":" << data
              << ",\"irq\":" << target.pending << ",\"time_ps\":" << sc_time_stamp().value() << "}\n";
        trace.flush();
        for (auto &value : response) value = htole32(value);
        if (!transfer(client, response, sizeof(response), true)) break;
    }
    close(client); close(server); unlink(argv[1]); return 0;
}
