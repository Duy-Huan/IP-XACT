// SPDX-License-Identifier: Apache-2.0
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_target_socket.h>
#include <tlm_utils/simple_initiator_socket.h>
#include "VUspRocket.h"
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
#include <array>
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
 uint64_t accesses=0; bool inject=false; uint32_t fault_address=0;
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
  if(inject&&address<=fault_address&&uint64_t(address)+width>fault_address)allowed=false;
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
// AXI data is 64 bits. The existing physical-memory transport carries aligned
// 1/2/4-byte operations; full RAM beats are explicitly decomposed, never truncated.
struct Burst {
 bool active=false; uint32_t address=0; unsigned id=0,len=0,size=0,index=0,burst=1;
 uint32_t at() const {
  uint64_t a=uint64_t(address)+(burst?uint64_t(index)*(1u<<size):0);
  if(a>0xffffffffULL)throw std::runtime_error("AXI address overflow");return uint32_t(a);
 }
 void begin(uint32_t a,unsigned i,unsigned l,unsigned s,unsigned b,bool lock) {
  if(s>3||b>1||lock||a% (1u<<s)||
     (b&&((a&4095)+uint64_t(l+1)*(1u<<s)>4096)))
    throw std::runtime_error("unsupported/malformed AXI burst");
  active=true;address=a;id=i;len=l;size=s;index=0;burst=b;
 }
};
struct Axi {
 Initiator& memory; Burst read,write;
 bool rvalid=false,bvalid=false;uint64_t rdata=0;unsigned rresp=0,bresp=0,rid=0,bid=0;bool rlast=false;
 uint64_t ar=0,aw=0,rbeats=0,wbeats=0,bbeats=0,errors=0,stalls=0,max_burst=0;
 unsigned latency=0;
 uint64_t request_stalls=0;
 std::array<uint64_t,8> previous_ar{},previous_aw{};
 std::array<uint64_t,3> previous_w{};
 bool held_ar=false,held_aw=false,held_w=false;
 std::ofstream trace;
 Axi(Initiator& m,const char* path):memory(m),trace(path){}
 void log(const char* channel,uint64_t cycle,uint32_t address,unsigned id,uint64_t data,unsigned response=0) {
  trace<<"{\"channel\":\""<<channel<<"\",\"cycle\":"<<cycle<<",\"address\":"<<address
       <<",\"id\":"<<id<<",\"data\":"<<data<<",\"response\":"<<response<<"}\n";
 }
 uint64_t load(uint32_t address,unsigned width,bool& error) {
  uint64_t data=0;error=false;
  for(unsigned off=0;off<width;) {
   unsigned count=width-off>=4?4:width-off>=2?2:1;bool bad=false;
   data|=uint64_t(memory.access(false,address+off,0,count,bad))<<(8*off);error|=bad;off+=count;
  }
  return data;
 }
 bool store(uint32_t address,uint64_t data,unsigned mask,unsigned size) {
  unsigned legal=((1u<<(1u<<size))-1)<<(address&7);
  if(mask&~legal)throw std::runtime_error("AXI strobe outside transfer");
  bool error=false;uint32_t base=address&~7u;
  for(unsigned lane=0;lane<8;) {
   if(!(mask&(1u<<lane))){lane++;continue;}
   unsigned count=lane%4==0&&lane+4<=8&&((mask>>lane)&15)==15?4:
                  lane%2==0&&lane+2<=8&&((mask>>lane)&3)==3?2:1;
   bool bad=false;memory.access(true,base+lane,uint32_t(data>>(8*lane)),count,bad);error|=bad;lane+=count;
  }
  return error;
 }
 void drive(VUspRocket& cpu,bool reset,uint64_t cycle,bool finishing=false) {
  bool available=cycle%(latency+1)==0;
  cpu.mem_0_ar_ready=!reset&&!finishing&&!read.active&&!rvalid&&available;
  cpu.mem_0_aw_ready=!reset&&!finishing&&!write.active&&!bvalid&&available;
  cpu.mem_0_w_ready=!reset&&write.active&&!bvalid&&available;
  cpu.mem_0_r_valid=!reset&&rvalid;cpu.mem_0_r_bits_data=rdata;
  cpu.mem_0_r_bits_id=rid;cpu.mem_0_r_bits_resp=rresp;cpu.mem_0_r_bits_last=rlast;
  cpu.mem_0_b_valid=!reset&&bvalid;cpu.mem_0_b_bits_id=bid;cpu.mem_0_b_bits_resp=bresp;
 }
 void accept(const VUspRocket& cpu,uint64_t cycle) {
  std::array<uint64_t,8> ar_payload={cpu.mem_0_ar_bits_addr,cpu.mem_0_ar_bits_id,
    cpu.mem_0_ar_bits_len,cpu.mem_0_ar_bits_size,cpu.mem_0_ar_bits_burst,
    cpu.mem_0_ar_bits_lock,cpu.mem_0_ar_bits_cache,cpu.mem_0_ar_bits_prot};
  std::array<uint64_t,8> aw_payload={cpu.mem_0_aw_bits_addr,cpu.mem_0_aw_bits_id,
    cpu.mem_0_aw_bits_len,cpu.mem_0_aw_bits_size,cpu.mem_0_aw_bits_burst,
    cpu.mem_0_aw_bits_lock,cpu.mem_0_aw_bits_cache,cpu.mem_0_aw_bits_prot};
  std::array<uint64_t,3> w_payload={cpu.mem_0_w_bits_data,cpu.mem_0_w_bits_strb,cpu.mem_0_w_bits_last};
  if((held_ar&&(!cpu.mem_0_ar_valid||previous_ar!=ar_payload))||
     (held_aw&&(!cpu.mem_0_aw_valid||previous_aw!=aw_payload))||
     (held_w&&(!cpu.mem_0_w_valid||previous_w!=w_payload)))
    throw std::runtime_error("AXI payload/VALID changed while blocked");
  held_ar=cpu.mem_0_ar_valid&&!cpu.mem_0_ar_ready;
  held_aw=cpu.mem_0_aw_valid&&!cpu.mem_0_aw_ready;
  held_w=cpu.mem_0_w_valid&&!cpu.mem_0_w_ready;
  request_stalls+=held_ar+held_aw+held_w;
  previous_ar=ar_payload;previous_aw=aw_payload;previous_w=w_payload;
  bool ar_fire=cpu.mem_0_ar_valid&&cpu.mem_0_ar_ready;
  bool aw_fire=cpu.mem_0_aw_valid&&cpu.mem_0_aw_ready;
  bool w_fire=cpu.mem_0_w_valid&&cpu.mem_0_w_ready;
  if(cpu.mem_0_r_valid&&!cpu.mem_0_r_ready)stalls++;
  if(cpu.mem_0_b_valid&&!cpu.mem_0_b_ready)stalls++;
  if(rvalid&&cpu.mem_0_r_ready){log("R",cycle,read.at(),rid,rdata,rresp);rbeats++;rvalid=false;if(rlast)read.active=false;else read.index++;}
  if(bvalid&&cpu.mem_0_b_ready){log("B",cycle,write.at(),bid,0,bresp);bbeats++;bvalid=false;}
  if(ar_fire){
   read.begin(cpu.mem_0_ar_bits_addr,cpu.mem_0_ar_bits_id,cpu.mem_0_ar_bits_len,
              cpu.mem_0_ar_bits_size,cpu.mem_0_ar_bits_burst,cpu.mem_0_ar_bits_lock);
   ar++;max_burst=std::max(max_burst,uint64_t(read.len+1));log("AR",cycle,read.address,read.id,read.len+1);
  }
  if(aw_fire){
   write.begin(cpu.mem_0_aw_bits_addr,cpu.mem_0_aw_bits_id,cpu.mem_0_aw_bits_len,
               cpu.mem_0_aw_bits_size,cpu.mem_0_aw_bits_burst,cpu.mem_0_aw_bits_lock);
   aw++;bresp=0;bid=write.id;max_burst=std::max(max_burst,uint64_t(write.len+1));
   log("AW",cycle,write.address,write.id,write.len+1);
  }
  if(w_fire){
   if(bool(cpu.mem_0_w_bits_last)!=(write.index==write.len))throw std::runtime_error("AXI WLAST mismatch");
   bool bad=store(write.at(),cpu.mem_0_w_bits_data,cpu.mem_0_w_bits_strb,write.size);
   bresp|=bad?3:0;errors+=bad;wbeats++;log("W",cycle,write.at(),write.id,cpu.mem_0_w_bits_data,bad?3:0);
   if(write.index==write.len){write.active=false;bvalid=true;}else write.index++;
  }
  if(read.active&&!rvalid&&cycle%(latency+1)==0){
   bool bad=false;rdata=load(read.at(),1u<<read.size,bad)<<(8*(read.at()&7));
   rresp=bad?3:0;errors+=bad;rid=read.id;rlast=read.index==read.len;rvalid=true;
  }
 }
};
int sc_main(int argc,char** argv) {
 if(argc!=11&&argc!=13)return 2;
 try {
  Verilated::commandArgs(argc,argv);sc_set_time_resolution(1,SC_PS);
  uint32_t boot=std::stoul(argv[5],nullptr,0);uint64_t limit=std::stoull(argv[6]),period=std::stoull(argv[7]);
  Bus bus("engine",argv[1],argv[2],argv[3],std::stoul(argv[8],nullptr,0));
  std::ifstream ranges(argv[9]);uint64_t base,size;
  while(ranges>>base>>size){if(base+size>1ULL<<32)throw std::runtime_error("physical map exceeds AXI32");bus.regions.push_back({uint32_t(base),size});}
  if(bus.regions.empty())throw std::runtime_error("empty physical map");
  unsigned quantum=std::stoul(argv[10]);if(quantum<1||quantum>64)throw std::runtime_error("invalid time quantum");
  Initiator cpu_bus("cpu_bus");cpu_bus.socket.bind(bus.target);sc_start(SC_ZERO_TIME);
  Axi axi(cpu_bus,(std::string(argv[4])+".axi.jsonl").c_str());
  if(argc==13){
   axi.latency=std::stoul(argv[11]);if(axi.latency>16)throw std::runtime_error("invalid AXI latency");
   if(std::string(argv[12])!="none"){
    uint64_t fault=std::stoull(argv[12],nullptr,0);
    if(fault>0xffffffffULL)throw std::runtime_error("fault address overflow");
    bus.inject=true;bus.fault_address=uint32_t(fault);
   }
  }
  VUspRocket cpu;cpu.reset_vector=boot;cpu.clock=0;cpu.reset=1;cpu.interrupts=0;axi.drive(cpu,true,0);cpu.eval();
  uint64_t retired=0,traps=0,edges=0;unsigned drain=0;
  std::ofstream commits(std::string(argv[4])+".commit.jsonl");
  for(uint64_t cycle=0;cycle<limit;cycle++){
   bool reset=cycle<16;cpu.clock=0;cpu.reset=reset;
   uint32_t ip=bus.link.irqs;
   cpu.interrupts=((ip>>3)&1)*2|((ip>>7)&1)*4|((ip>>11)&1)*8|((ip>>9)&1)*16;
   axi.drive(cpu,reset,cycle,bus.done);cpu.eval();
   if(!reset)axi.accept(cpu,cycle);
   // Inputs remain unchanged through the sampling edge. New slave responses
   // become visible only in the following cycle, and remain stable while stalled.
   cpu.clock=1;cpu.eval();edges++;
   if(!reset&&(cpu.retired||cpu.trapped)){
    retired+=bool(cpu.retired);traps+=bool(cpu.trapped);
    commits<<"{\"cycle\":"<<cycle<<",\"pc\":"<<cpu.pc<<",\"instruction\":"<<cpu.instruction
           <<",\"trap\":"<<bool(cpu.trapped)<<"}\n";
   }
   if(bus.done){if(++drain>=8&&!axi.read.active&&!axi.rvalid&&!axi.write.active&&!axi.bvalid)break;}
   else{if((cycle+1)%quantum==0)bus.link.call(2,0,period*quantum,0);sc_start(sc_time(period,SC_NS),SC_RUN_TO_TIME);}
  }
  cpu.final();std::ofstream result(argv[4]);
  result<<"{\"source\":\"Rocket commit trace and actual clock edges\",\"cycles\":"<<edges
        <<",\"instructions\":"<<retired<<",\"traps\":"<<traps<<",\"signature\":"<<bus.signature<<",\"finished\":"<<bus.done
        <<",\"axi_ar\":"<<axi.ar<<",\"axi_aw\":"<<axi.aw<<",\"axi_r_beats\":"<<axi.rbeats<<",\"axi_w_beats\":"<<axi.wbeats
        <<",\"axi_b\":"<<axi.bbeats<<",\"axi_errors\":"<<axi.errors<<",\"axi_stalls\":"<<axi.stalls<<",\"axi_request_stalls\":"<<axi.request_stalls<<",\"axi_latency\":"<<axi.latency<<",\"max_burst_beats\":"<<axi.max_burst<<"}\n";
  return bus.done&&bus.signature==0x5555&&retired>0&&axi.errors==0?0:5;
 }catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 3;}
}
