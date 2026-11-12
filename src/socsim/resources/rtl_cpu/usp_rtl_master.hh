// SPDX-License-Identifier: Apache-2.0
#ifndef USP_RTL_MASTER_HH
#define USP_RTL_MASTER_HH
#include "sim/sim_object.hh"
#include "mem/port.hh"
#include "sim/eventq.hh"
#include "params/UspRtlMaster.hh"
namespace gem5 {
class BaseCPU;
class UspRtlMaster : public SimObject {
    class BusPort : public RequestPort {
      public:
        BusPort(const std::string& n, SimObject* o): RequestPort(n,o) {}
        bool recvTimingResp(PacketPtr) override { panic("RTL master is atomic only"); }
        void recvReqRetry() override { panic("RTL master is atomic only"); }
    } bus;
    BaseCPU* cpu;
    RequestorID requestor;
    std::string socketPath;
    int listener=-1, fd=-1;
    uint32_t sequence=0;
    EventFunctionWrapper serviceEvent;
    void service();
    void reply(uint32_t data, uint32_t status=0);
    void transfer(void* data, size_t size, bool writing);
  public:
    UspRtlMaster(const UspRtlMasterParams& p);
    ~UspRtlMaster();
    Port& getPort(const std::string& name, PortID id=InvalidPortID) override;
    void startup() override;
};
}
#endif
