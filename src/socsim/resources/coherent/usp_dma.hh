// SPDX-License-Identifier: Apache-2.0
#ifndef USP_COHERENT_DMA_HH
#define USP_COHERENT_DMA_HH
#include <array>
#include <fstream>
#include "dev/dma_device.hh"
#include "dev/platform.hh"
#include "params/UspDma.hh"
#include "sim/eventq.hh"
namespace gem5 {
class UspDma : public DmaDevice {
    Addr base, ramBase, ramSize, src = 0, dst = 0;
    uint32_t length = 0, status = 0;
    Platform *platform;
    unsigned irq;
    uint64_t transfers = 0, errors = 0;
    std::array<uint8_t, 4096> buffer{};
    std::ofstream trace;
    EventFunctionWrapper readDone, writeDone;
    void record(const char *phase);
    bool permitted(Addr address) const;
    Tick access(PacketPtr pkt, bool write);
  public:
    UspDma(const UspDmaParams &p);
    AddrRangeList getAddrRanges() const override;
    Tick read(PacketPtr pkt) override { return access(pkt, false); }
    Tick write(PacketPtr pkt) override { return access(pkt, true); }
    void serialize(CheckpointOut &) const override;
    void unserialize(CheckpointIn &) override;
};
}
#endif
