// SPDX-License-Identifier: Apache-2.0
#ifndef USP_GEM5_BRIDGE_HH
#define USP_GEM5_BRIDGE_HH
#include "dev/io_device.hh"
#include "dev/platform.hh"
#include "params/UspBridge.hh"
namespace gem5 {
class UspBridge : public PioDevice {
    int fd = -1;
    uint32_t sequence = 0, levels = 0;
    Platform *platform;
    Addr gcd, gpio, finish;
    unsigned irqs[2];
    Tick latency;
    uint32_t call(uint32_t op, uint32_t offset, uint32_t value);
    Tick access(PacketPtr pkt, bool writing);
  public:
    UspBridge(const UspBridgeParams &p);
    ~UspBridge();
    void startup() override;
    AddrRangeList getAddrRanges() const override;
    Tick read(PacketPtr pkt) override { return access(pkt, false); }
    Tick write(PacketPtr pkt) override { return access(pkt, true); }
    void serialize(CheckpointOut &) const override;
    void unserialize(CheckpointIn &) override;
};
}
#endif
