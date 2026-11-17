// SPDX-License-Identifier: Apache-2.0
#ifndef USP_NATIVE_SOC_DEVICE_HH
#define USP_NATIVE_SOC_DEVICE_HH
#include "dev/io_device.hh"
#include "dev/platform.hh"
#include "params/NativeSocDevice.hh"
namespace gem5 {
class NativeSocDevice : public PioDevice {
    int fd = -1;
    uint32_t sequence = 0, level = 0;
    Platform *platform;
    Addr base, span;
    unsigned kind, irq;
    Tick latency;
    uint32_t call(uint32_t op, uint32_t offset, uint32_t data);
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
