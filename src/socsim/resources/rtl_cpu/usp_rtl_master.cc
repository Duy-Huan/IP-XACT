// SPDX-License-Identifier: Apache-2.0
#include "usp_rtl_master.hh"
#include "cpu/base.hh"
#include "arch/riscv/interrupts.hh"
#include "mem/packet.hh"
#include "sim/system.hh"
#include "sim/sim_exit.hh"
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <endian.h>
#include <cstring>
#include <cerrno>
namespace gem5 {
UspRtlMaster::UspRtlMaster(const UspRtlMasterParams& p)
 :SimObject(p),bus(name()+".port",this),cpu(p.cpu),
  requestor(p.system->getRequestorId(this)),socketPath(p.socket_path),
  serviceEvent([this]{service();},name()+".service") {}
Port& UspRtlMaster::getPort(const std::string& n, PortID id) {
    return n=="port"?bus:SimObject::getPort(n,id);
}
UspRtlMaster::~UspRtlMaster() {
    if(fd>=0)close(fd);
    if(listener>=0)close(listener);
    unlink(socketPath.c_str());
}
void UspRtlMaster::transfer(void* data,size_t size,bool writing) {
    size_t pos=0;
    while(pos<size) {
        ssize_t n=writing?send(fd,(char*)data+pos,size-pos,MSG_NOSIGNAL):recv(fd,(char*)data+pos,size-pos,0);
        if(n<0&&errno==EINTR)continue;
        fatal_if(n<=0,"RTL CPU connection lost or timed out");pos+=n;
    }
}
void UspRtlMaster::startup() {
    sockaddr_un a{};a.sun_family=AF_UNIX;
    fatal_if(socketPath.size()>=sizeof(a.sun_path),"RTL socket path too long");
    std::strcpy(a.sun_path,socketPath.c_str());listener=socket(AF_UNIX,SOCK_STREAM,0);
    fatal_if(listener<0||bind(listener,(sockaddr*)&a,sizeof(a))||listen(listener,1),"RTL socket setup failed");
    schedule(serviceEvent,curTick()+1);
}
void UspRtlMaster::reply(uint32_t data,uint32_t status) {
    auto* interrupts=dynamic_cast<RiscvISA::Interrupts*>(cpu->getInterruptController(0));
    fatal_if(!interrupts,"RTL master needs RISC-V IRQ sink");
    uint32_t response[6]={0x32505355,sequence,status,data,uint32_t(interrupts->readIP()),0};
    for(auto& v:response)v=htole32(v);
    transfer(response,sizeof(response),true);
}
void UspRtlMaster::service() {
    if(fd<0) {
        fd=accept(listener,nullptr,nullptr);fatal_if(fd<0,"RTL accept failed");
        timeval t{10,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&t,sizeof(t));
        setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&t,sizeof(t));
    }
    uint32_t req[6];transfer(req,sizeof(req),false);
    for(auto& v:req)v=le32toh(v);
    fatal_if(req[0]!=0x32505355||req[1]!=sequence+1,"Invalid RTL sequence");sequence=req[1];
    if(req[2]==2) {
        fatal_if(req[4]==0||req[4]>1000000,"Invalid RTL time advance");
        // Reply after native RTC events, then yield before blocking for more work.
        auto* event=new EventFunctionWrapper([this]{reply(0);schedule(serviceEvent,curTick()+1);},
                                             name()+".advance",true);
        schedule(*event,curTick()+Tick(req[4])*1000);return;
    }
    fatal_if(req[2]>1||(req[5]!=1&&req[5]!=2&&req[5]!=4)||req[3]%req[5],"Invalid RTL bus request");
    auto request=std::make_shared<Request>(req[3],req[5],0,requestor);
    Packet packet(request,req[2]?MemCmd::WriteReq:MemCmd::ReadReq);
    uint32_t data=htole32(req[4]);packet.dataStatic((uint8_t*)&data);
    bus.sendAtomic(&packet);reply(le32toh(data),packet.isError()?1:0);
    schedule(serviceEvent,curTick()+1);
}
}
