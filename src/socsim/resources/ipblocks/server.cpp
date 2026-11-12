// SPDX-License-Identifier: Apache-2.0
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_target_socket.h>
#include <tlm_utils/simple_initiator_socket.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <endian.h>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include "Vusp_ipblocks.h"
#include "verilated.h"
using namespace sc_core;
static_assert(SC_VERSION_MAJOR == 3 && SC_VERSION_MINOR == 0 && SC_VERSION_PATCH == 1);

struct Target : sc_module {
    tlm_utils::simple_target_socket<Target> socket;
    Vusp_ipblocks dut;
    uint64_t cycles = 0;
    unsigned pending() const { return dut.cy_irq_o | (dut.ot_irq_o << 1); }
    void cycle() {
        dut.clk_i=0; dut.eval(); dut.clk_i=1; dut.eval(); dut.clk_i=0; dut.eval(); ++cycles;
    }
    void reset() {
        dut.rst_ni=0; dut.cy_valid_i=0; dut.ot_valid_i=0;
        dut.ot_ready_i=0; dut.ot_corrupt_i=0; dut.data_i=0;
        cycle(); cycle(); dut.rst_ni=1; cycle(); cycle();
    }
    Target(sc_module_name name) : sc_module(name), socket("target") {
        socket.register_b_transport(this, &Target::transport); reset();
    }
    void transport(tlm::tlm_generic_payload &tx, sc_time &delay) {
        uint64_t before=cycles;
        uint32_t data; std::memcpy(&data, tx.get_data_ptr(), 4);
        unsigned op=tx.get_command(); // read/write; reset and corruption are extensions below
        uint64_t address=tx.get_address();
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
        if (address == 0x2000) { reset(); }
        else if ((address & 3) || address >= 0x2000) {
            tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
        } else if (address < 0x1000) {
            if (address > 12) { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); }
            else {
                dut.cy_valid_i=1; dut.cy_write_i=(op==tlm::TLM_WRITE_COMMAND);
                dut.cy_addr_i=address; dut.data_i=data; dut.eval();
                bool error=dut.cy_error_o;
                if (tx.is_read()) data=dut.cy_data_o;
                cycle(); dut.cy_valid_i=0; cycle();
                if (error) tx.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE);
            }
        } else {
            unsigned offset=address & 0xfff;
            // The IP decodes six local address bits. Enforce its exact 64-byte
            // aperture here instead of permitting higher-address aliases.
            if (offset >= 64) { tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); }
            else {
                dut.ot_addr_i=offset; dut.data_i=data;
                dut.ot_write_i=tx.is_write(); dut.ot_valid_i=1; dut.ot_ready_i=0; dut.eval();
                unsigned budget=0;
                while (!dut.ot_accept_o) { if (++budget>32) throw std::runtime_error("TL A timeout"); cycle(); }
                cycle(); dut.ot_valid_i=0; dut.eval();
                while (!dut.ot_valid_o) { if (++budget>32) throw std::runtime_error("TL D timeout"); cycle(); }
                uint32_t response=dut.ot_data_o; bool error=dut.ot_error_o;
                // Exercise real response backpressure; response must remain stable.
                for (unsigned i=0;i<3;++i) {
                    cycle();
                    if (!dut.ot_valid_o || dut.ot_data_o!=response || dut.ot_error_o!=error || !dut.ot_integrity_o)
                        throw std::runtime_error("TL response/integrity changed under backpressure");
                }
                if (tx.is_read()) data=response;
                dut.ot_ready_i=1; cycle(); dut.ot_ready_i=0; cycle();
                dut.ot_corrupt_i=0;
                if (error) tx.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE);
            }
        }
        std::memcpy(tx.get_data_ptr(), &data, 4);
        delay += sc_time((cycles-before)*10, SC_NS);
    }
};
struct Initiator : sc_module {
    tlm_utils::simple_initiator_socket<Initiator> socket;
    Initiator(sc_module_name n) : sc_module(n), socket("initiator") {}
};
static bool transfer(int fd, void *buffer, size_t bytes, bool writing) {
    size_t pos=0;
    while(pos<bytes) {
        ssize_t n=writing ? send(fd,(char*)buffer+pos,bytes-pos,MSG_NOSIGNAL)
                          : recv(fd,(char*)buffer+pos,bytes-pos,0);
        if(n<0 && errno==EINTR) continue;
        if(n<=0) return false;
        pos+=n;
    }
    return true;
}
int sc_main(int argc, char **argv) {
    if(argc!=3 || std::strlen(argv[1])>=sizeof(sockaddr_un::sun_path)) return 2;
    Verilated::commandArgs(argc,argv); sc_set_time_resolution(1,SC_PS);
    Target target("rtl"); Initiator initiator("bus"); initiator.socket.bind(target.socket); sc_start(SC_ZERO_TIME);
    int server=socket(AF_UNIX,SOCK_STREAM,0);
    sockaddr_un address{}; address.sun_family=AF_UNIX; std::strcpy(address.sun_path,argv[1]);
    if(server<0 || bind(server,(sockaddr*)&address,sizeof(address)) || listen(server,1)) return 3;
    int client=accept(server,nullptr,nullptr); if(client<0) return 4;
    std::ofstream trace(argv[2]); uint32_t expected=1, request[5];
    while(transfer(client,request,sizeof(request),false)) {
        for(auto &v:request) v=le32toh(v);
        uint32_t data=request[4], status=0;
        uint32_t sequence=expected++;
        if(request[0]!=0x31505355 || request[1]!=sequence || request[2]>3 ||
           (request[2]!=2 && request[3]>=0x2000) ||
           (request[2]==3 && request[3]<0x1000)) status=1;
        tlm::tlm_generic_payload tx; sc_time delay=SC_ZERO_TIME;
        tx.set_address(request[2]==2 ? 0x2000 : request[3]);
        tx.set_command(request[2]==1 ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
        tx.set_data_ptr((unsigned char*)&data); tx.set_data_length(4); tx.set_streaming_width(4);
        target.dut.ot_corrupt_i=(request[2]==3);
        if(!status) {
            try { initiator.socket->b_transport(tx,delay); status=tx.is_response_error(); sc_start(delay,SC_RUN_TO_TIME); }
            catch(const std::exception &e) { std::cerr<<e.what()<<std::endl; status=2; }
        }
        uint32_t response[5]={0x31505355,request[1],status,data,target.pending()};
        trace<<"{\"seq\":"<<request[1]<<",\"op\":"<<request[2]<<",\"offset\":"<<request[3]
             <<",\"data\":"<<request[4]<<",\"value\":"<<data<<",\"status\":"<<status
             <<",\"irq\":"<<target.pending()<<",\"alert\":"<<(unsigned)target.dut.ot_alert_o
             <<",\"cycles\":"<<target.cycles<<",\"time_ps\":"<<sc_time_stamp().value()<<"}\n";
        trace.flush();
        for(auto &v:response) v=htole32(v);
        if(!transfer(client,response,sizeof(response),true)) break;
        if(status==2) { close(client); close(server); unlink(argv[1]); return 5; }
    }
    close(client); close(server); unlink(argv[1]); return 0;
}
