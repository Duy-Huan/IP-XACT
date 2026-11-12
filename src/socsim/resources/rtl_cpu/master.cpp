// SPDX-License-Identifier: Apache-2.0
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_target_socket.h>
#include <tlm_utils/simple_initiator_socket.h>
#include "Vusp_rtl_cpu.h"
#include "verilated.h"
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <endian.h>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <cstring>
#include <cerrno>
#include <vector>
using namespace sc_core;
class Link {
 int fd=-1; bool binary; uint32_t seq=0;
 std::string line() {
  std::string s;char c;
  while(s.size()<4096) {
   if(recv(fd,&c,1,0)!=1)throw std::runtime_error("engine disconnected");
   if(c=='\n')return s;s+=c;
  }
  throw std::runtime_error("oversized engine frame");
 }
 void bytes(void* buf,size_t size,bool writing) {
  size_t pos=0;
  while(pos<size) {
   ssize_t n=writing?send(fd,(char*)buf+pos,size-pos,MSG_NOSIGNAL):recv(fd,(char*)buf+pos,size-pos,0);
   if(n<0&&errno==EINTR)continue;
   if(n<=0)throw std::runtime_error("engine transfer failed");pos+=n;
  }
 }
 public:
 uint32_t irqs=0;
 Link(const char* engine,const char* path):binary(std::string(engine)=="gem5") {
  sockaddr_un a{};a.sun_family=AF_UNIX;
  if(std::strlen(path)>=sizeof(a.sun_path))throw std::runtime_error("socket path too long");
  std::strcpy(a.sun_path,path);fd=socket(AF_UNIX,SOCK_STREAM,0);
  timeval t{3,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&t,sizeof(t));
  setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&t,sizeof(t));
  if(fd<0||connect(fd,(sockaddr*)&a,sizeof(a)))throw std::runtime_error("engine connect failed");
  if(!binary)text("irq_intercept_in /machine/soc/harts[0]");
 }
 ~Link(){if(fd>=0)close(fd);}
 std::string text(const std::string& cmd) {
  std::string s=cmd+"\n";bytes(s.data(),s.size(),true);
  for(;;) {
   s=line();
   if(s.rfind("IRQ ",0)==0) {
    std::istringstream p(s);std::string tag,level;unsigned number;p>>tag>>level>>number;
    if(number<32) {if(level=="raise")irqs|=1U<<number;else irqs&=~(1U<<number);}continue;
   }
   if(s.rfind("OK",0)!=0)throw std::runtime_error("engine rejected: "+s);
   return s.size()>3?s.substr(3):"";
  }
 }
 uint32_t call(unsigned op,uint32_t address,uint32_t value,unsigned width) {
  if(binary) {
   uint32_t req[6]={0x32505355,++seq,op,address,value,width},res[6];
   for(auto& v:req)v=htole32(v);
   bytes(req,sizeof(req),true);bytes(res,sizeof(res),false);
   for(auto& v:res)v=le32toh(v);
   if(res[0]!=0x32505355||res[1]!=seq||res[2])throw std::runtime_error("gem5 transaction rejected");
   irqs=res[4];return res[3];
  }
  std::ostringstream s;s<<std::hex;
  if(op==2) {s<<"clock_step "<<std::dec<<value;text(s.str());return 0;}
  const char* suffix=width==1?"b":width==2?"w":"l";
  s<<(op==1?"write":"read")<<suffix<<" 0x"<<address;
  if(op==1)s<<" 0x"<<value;
  std::string r=text(s.str());return op==1?0:std::stoul(r,nullptr,0);
 }
};
struct Bus : sc_module {
 tlm_utils::simple_target_socket<Bus> target;
 Link link;std::ofstream trace;uint32_t finish;bool done=false;uint32_t signature=0;
 std::vector<std::pair<uint32_t,uint64_t>> regions;
 uint64_t accesses=0;
 Bus(sc_module_name name,const char* engine,const char* sock,const char* path,uint32_t f)
 :sc_module(name),target("target"),link(engine,sock),trace(path),finish(f) {
  target.register_b_transport(this,&Bus::transport);
 }
 void transport(tlm::tlm_generic_payload& tx,sc_time& delay) {
  uint32_t address=tx.get_address(),data=0;unsigned width=tx.get_data_length();
  std::memcpy(&data,tx.get_data_ptr(),width);
  bool allowed=false;
  for(auto region:regions)
   if(address>=region.first&&uint64_t(address)+width<=uint64_t(region.first)+region.second)allowed=true;
  tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE);
  if(allowed) {
   uint32_t result=link.call(tx.is_write()?1:0,address,data,width);
   if(tx.is_read())data=result;
   tx.set_response_status(tlm::TLM_OK_RESPONSE);
  }
  std::memcpy(tx.get_data_ptr(),&data,width);
  trace<<"{\"index\":"<<++accesses<<",\"write\":"<<tx.is_write()<<",\"address\":"<<address
   <<",\"width\":"<<width<<",\"data\":"<<data<<",\"error\":"<<!allowed<<"}\n";
  if(allowed&&tx.is_write()&&address==finish&&width==4) {signature=data;done=true;}
 }
};
struct Initiator : sc_module {
 tlm_utils::simple_initiator_socket<Initiator> socket;
 Initiator(sc_module_name n):sc_module(n),socket("initiator"){}
 uint32_t access(bool writing,uint32_t addr,uint32_t data,unsigned width,bool& error) {
  tlm::tlm_generic_payload tx;sc_time delay=SC_ZERO_TIME;
  tx.set_address(addr);tx.set_command(writing?tlm::TLM_WRITE_COMMAND:tlm::TLM_READ_COMMAND);
  tx.set_data_ptr((unsigned char*)&data);tx.set_data_length(width);tx.set_streaming_width(width);
  socket->b_transport(tx,delay);error=tx.is_response_error();return data;
 }
};
int sc_main(int argc,char** argv) {
 if(argc!=11)return 2;
 try {
  Verilated::commandArgs(argc,argv);sc_set_time_resolution(1,SC_PS);
  uint32_t boot=std::stoul(argv[5],nullptr,0);
  uint64_t limit=std::stoul(argv[6]),period=std::stoul(argv[7]);
  Bus bus("engine",argv[1],argv[2],argv[3],std::stoul(argv[8],nullptr,0));
  std::ifstream ranges(argv[9]);uint64_t base,size;
  while(ranges>>base>>size)bus.regions.push_back({base,size});
  if(bus.regions.size()!=8)throw std::runtime_error("incomplete engine address map");
  unsigned quantum=std::stoul(argv[10]);
  if(quantum<1||quantum>64)throw std::runtime_error("invalid time quantum");
  Initiator cpu_bus("cpu_bus");cpu_bus.socket.bind(bus.target);sc_start(SC_ZERO_TIME);
  Vusp_rtl_cpu cpu;cpu.boot_i=boot;cpu.clk_i=0;cpu.rst_ni=1;cpu.eval();cpu.rst_ni=0;cpu.eval();
  bool ipending=false,dpending=false,ierror=false,derror=false;
  uint32_t idata=0,ddata=0;uint64_t retired=0,traps=0,edges=0;unsigned drain=0;
  std::ofstream rvfi(std::string(argv[4])+".rvfi.jsonl");
  for(uint64_t cycles=0;cycles<limit;cycles++) {
   cpu.clk_i=0;cpu.rst_ni=cycles>=8;
   cpu.i_gnt_i=0;cpu.d_gnt_i=0;cpu.i_valid_i=ipending;cpu.d_valid_i=dpending;
   cpu.i_data_i=idata;cpu.d_data_i=ddata;cpu.i_error_i=ierror;cpu.d_error_i=derror;
   cpu.irq_sw_i=(bus.link.irqs>>3)&1;cpu.irq_timer_i=(bus.link.irqs>>7)&1;
   cpu.irq_ext_i=(bus.link.irqs>>11)&1;cpu.eval();
   bool nexti=false,nextd=false,nexte=false,nextde=false;uint32_t nextidata=0,nextddata=0;
   if(cpu.rst_ni&&!bus.done) {
    if(cpu.i_req_o&&!ipending) {nextidata=cpu_bus.access(false,cpu.i_addr_o,0,4,nexte);cpu.i_gnt_i=1;nexti=true;}
    if(cpu.d_req_o&&!dpending) {
     unsigned be=cpu.d_be_o,lane=0;while(lane<4&&!(be&(1u<<lane)))lane++;
     unsigned width=be==15?4:(be==3||be==12)?2:1;
     if(lane==4||be!=(((1u<<width)-1)<<lane))throw std::runtime_error("unsupported byte enable");
     uint32_t a=(cpu.d_addr_o&~3u)+lane;
     nextddata=cpu_bus.access(cpu.d_write_o,a,cpu.d_data_o>>(8*lane),width,nextde)<<(8*lane);
     cpu.d_gnt_i=1;nextd=true;
    }
   }
   cpu.eval();cpu.clk_i=1;cpu.eval();edges++;
   if(cpu.rst_ni&&cpu.retired_o) {
    retired++;traps+=cpu.trap_o;
    rvfi<<"{\"cycle\":"<<cycles<<",\"pc\":"<<cpu.pc_o<<",\"instruction\":"<<cpu.instruction_o
        <<",\"trap\":"<<unsigned(cpu.trap_o)<<"}\n";
   }
   if(cpu.alert_o)throw std::runtime_error("RTL CPU alert");
   ipending=nexti;dpending=nextd;idata=nextidata;ddata=nextddata;ierror=nexte;derror=nextde;
   if(bus.done) {if(++drain>=8)break;}
   else {
    if((cycles+1)%quantum==0)bus.link.call(2,0,period*quantum,0);
    sc_start(sc_time(period,SC_NS),SC_RUN_TO_TIME);
   }
  }
  cpu.final();
  std::ofstream result(argv[4]);result<<"{\"cycles\":"<<edges<<",\"rvfi_events\":"<<retired
   <<",\"rvfi_traps\":"<<traps<<",\"signature\":"<<bus.signature<<",\"finished\":"<<bus.done<<"}\n";
  return bus.done&&bus.signature==0x5555&&retired>0?0:5;
 } catch(const std::exception& e) {std::cerr<<e.what()<<std::endl;return 3;}
}
