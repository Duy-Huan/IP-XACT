// SPDX-License-Identifier: Apache-2.0
#ifndef USP_NATIVE_SOC_DEVICE_HH
#define USP_NATIVE_SOC_DEVICE_HH
#include "dev/io_device.hh"
#include "dev/platform.hh"
#include "params/NativeSocDevice.hh"
#include "sim/eventq.hh"
#include <cstdint>
#include <vector>
namespace gem5 {
class NativeSocDevice : public PioDevice {
    int fd = -1;
    uint32_t sequence = 0, level = 0;
    Platform *platform;
    Addr base, span;
    unsigned kind, irq;
    Tick latency;
    bool useV2 = false;
    bool dmaEnabled = false;
    uint64_t quantumTicks = 0;
    EventFunctionWrapper quantumSyncEvent;
    void transfer(void *buffer, size_t size, bool writing);
    void updateIrq(uint32_t mask);
    uint32_t call(uint32_t op, uint32_t offset, uint32_t data);
    uint64_t callV2(uint16_t op, uint64_t offset, uint64_t data, uint32_t length,
                    uint32_t flags, const void *txPayload, void *rxPayload);
    void handleQuantumSync();
    Tick access(PacketPtr pkt, bool writing);
  public:
    NativeSocDevice(const NativeSocDeviceParams &p);
    ~NativeSocDevice();
    void startup() override;
    AddrRangeList getAddrRanges() const override { return {RangeSize(base, span)}; }
    Tick read(PacketPtr pkt) override { return access(pkt, false); }
    Tick write(PacketPtr pkt) override { return access(pkt, true); }
    void serialize(CheckpointOut &) const override;
    void unserialize(CheckpointIn &) override;
};
}
#endif
