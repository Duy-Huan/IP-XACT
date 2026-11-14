// SPDX-License-Identifier: Apache-2.0
#include "usp_dma.hh"
#include "base/logging.hh"
#include "mem/packet_access.hh"
#include "sim/cur_tick.hh"
namespace gem5 {
UspDma::UspDma(const UspDmaParams &p) : DmaDevice(p), base(p.base),
    ramBase(p.ram_base), ramSize(p.ram_size), platform(p.platform), irq(p.irq),
    trace(p.trace_file),
    readDone([this] {
        record("read_done");
        dmaWrite(dst, length, &writeDone, buffer.data());
    }, name()+".read_done"),
    writeDone([this] {
        ++transfers; status = 2; record("write_done");
        platform->postPciInt(irq);
    }, name()+".write_done") {
    fatal_if(!trace, "DMA trace cannot be opened");
}
AddrRangeList UspDma::getAddrRanges() const { return {RangeSize(base, 4096)}; }
bool UspDma::permitted(Addr a) const {
    return length && length <= buffer.size() && a >= ramBase &&
        a - ramBase <= ramSize && length <= ramSize - (a - ramBase);
}
void UspDma::record(const char *phase) {
    uint32_t checksum = 2166136261U;
    if (length <= buffer.size())
        for (unsigned i=0; i<length; ++i) checksum = (checksum ^ buffer[i])*16777619U;
    trace << "{\"tick\":" << curTick() << ",\"phase\":\"" << phase
          << "\",\"src\":" << src << ",\"dst\":" << dst
          << ",\"bytes\":" << length << ",\"checksum\":" << checksum << "}\n";
    trace.flush();
}
Tick UspDma::access(PacketPtr pkt, bool writing) {
    fatal_if(pkt->getSize()!=4 || (pkt->getAddr()&3), "DMA requires aligned 32-bit registers");
    auto off = pkt->getAddr()-base;
    uint32_t value = writing ? pkt->getLE<uint32_t>() : 0;
    if (!writing) {
        switch(off) {
          case 0: value=src; break; case 4: value=src>>32; break;
          case 8: value=dst; break; case 12: value=dst>>32; break;
          case 16: value=length; break; case 20: value=status; break;
          case 24: value=transfers; break; case 28: value=errors; break;
          default: fatal("Undefined DMA register");
        }
        pkt->setLE<uint32_t>(value);
    } else if (status==1) {
        // An in-flight descriptor is immutable. Never overwrite an active buffer.
        ++errors; record("busy_reject");
    } else {
        switch(off) {
          case 0: src=(src&0xffffffff00000000ULL)|value; break;
          case 4: src=(src&0xffffffffULL)|(uint64_t(value)<<32); break;
          case 8: dst=(dst&0xffffffff00000000ULL)|value; break;
          case 12: dst=(dst&0xffffffffULL)|(uint64_t(value)<<32); break;
          case 16: length=value; break;
          case 20:
            if (value==0) { status=0; platform->clearPciInt(irq); record("ack"); }
            else if (value==1) {
                if (!permitted(src) || !permitted(dst)) {
                    status=4; ++errors; record("range_reject"); platform->postPciInt(irq);
                } else {
                    status=1; record("start"); dmaRead(src,length,&readDone,buffer.data());
                }
            } else fatal("Invalid DMA control");
            break;
          default: fatal("Read-only or undefined DMA register");
        }
    }
    pkt->makeAtomicResponse();
    return 20000; // Explicit 20ns register latency at the 1ps gem5 tick.
}
void UspDma::serialize(CheckpointOut &) const { fatal("Live coherent DMA checkpoint unsupported"); }
void UspDma::unserialize(CheckpointIn &) { fatal("Live coherent DMA restore unsupported"); }
}
