// SPDX-License-Identifier: Apache-2.0
#include "native_soc_device.hh"
#include "base/logging.hh"
#include "mem/packet.hh"
#include "mem/packet_access.hh"
#include "sim/cur_tick.hh"
#include "sim/sim_exit.hh"
#include "sim/system.hh"
#include <cerrno>
#include <cstring>
#include <endian.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
namespace gem5 {
namespace {
constexpr uint32_t kUspV1Magic = 0x31505355u;
constexpr uint32_t kUspV2Magic = 0x32425355u;
constexpr uint32_t kUspV2MaxBurst = 4096u;
constexpr uint16_t kOpRead = 0x0000u;
constexpr uint16_t kOpWrite = 0x0001u;
constexpr uint16_t kOpReset = 0x0002u;
constexpr uint16_t kOpBurstRead = 0x0003u;
constexpr uint16_t kOpBurstWrite = 0x0004u;
constexpr uint16_t kOpTimeSync = 0x0005u;
constexpr uint16_t kOpDmaRead = 0x0010u;
constexpr uint16_t kOpDmaWrite = 0x0011u;
constexpr uint16_t kOpResponse = 0x8000u;
constexpr uint32_t kFlagPayload = 1u << 0;
constexpr uint32_t kFlagBurst64 = 1u << 1;
constexpr uint32_t kFlagQuantum = 1u << 3;
struct UspV2Frame {
    uint32_t magic, seq;
    uint16_t op, status;
    uint32_t length, irq_mask, flags;
    uint64_t addr, data, time_ps;
};
static_assert(sizeof(UspV2Frame) == 48, "UspV2Frame must be 48 bytes");
} // namespace

NativeSocDevice::NativeSocDevice(const NativeSocDeviceParams &p) : PioDevice(p),
    platform(p.platform), base(p.base), span(p.span), kind(p.kind), irq(p.irq),
    latency(p.pio_latency), useV2(p.protocol == "usp-bus-v2"), dmaEnabled(p.dma_enabled),
    quantumTicks(static_cast<uint64_t>(p.quantum_ns) * 1000ULL),
    quantumSyncEvent([this] { handleQuantumSync(); }, name() + ".quantum_sync") {
    fatal_if(kind > 2 || !span, "Unsupported native device contract");
    fatal_if(p.protocol != "usp-mmio32-v1" && p.protocol != "usp-bus-v2",
             "Unsupported NativeSocDevice protocol");
    if (kind == 2) return;
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    fatal_if(p.socket_path.empty() || p.socket_path.size() >= sizeof(addr.sun_path),
             "Invalid native SystemC socket");
    std::strcpy(addr.sun_path, p.socket_path.c_str());
    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    timeval timeout{5, 0};
    fatal_if(fd < 0 || setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
             setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) ||
             connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)),
             "Native SystemC instance connection failed");
}
NativeSocDevice::~NativeSocDevice() { if (fd >= 0) close(fd); }

void NativeSocDevice::transfer(void *buffer, size_t size, bool writing) {
    for (size_t pos = 0; pos < size;) {
        auto *bytes = static_cast<char *>(buffer);
        ssize_t n = writing ? send(fd, bytes + pos, size - pos, MSG_NOSIGNAL)
                            : recv(fd, bytes + pos, size - pos, 0);
        if (n < 0 && errno == EINTR) continue;
        fatal_if(n <= 0, "Native SystemC instance disconnected or timed out");
        pos += n;
    }
}

void NativeSocDevice::updateIrq(uint32_t mask) {
    uint32_t next = !!(mask & (useV2 ? 0xFFFFFFFFu : (1U << kind)));
    if (next != level) {
        if (next) platform->postPciInt(irq);
        else platform->clearPciInt(irq);
    }
    level = next;
}

void NativeSocDevice::startup() {
    PioDevice::startup();
    if (kind == 2) return;
    if (useV2) {
        callV2(kOpReset, 0, 0x3FULL, 0, 0, nullptr, nullptr);
        if (quantumTicks > 0) {
            schedule(quantumSyncEvent, curTick() + quantumTicks);
        }
    } else {
        call(2, 0, 0);
    }
}

void NativeSocDevice::handleQuantumSync() {
    if (fd < 0 || !useV2 || quantumTicks == 0) return;
    callV2(kOpTimeSync, curTick(), quantumTicks, 0, kFlagQuantum, nullptr, nullptr);
    schedule(quantumSyncEvent, curTick() + quantumTicks);
}

uint32_t NativeSocDevice::call(uint32_t op, uint32_t offset, uint32_t data) {
    uint32_t req[5] = {htole32(kUspV1Magic), htole32(++sequence), htole32(op),
                       htole32(offset), htole32(data)}, res[5];
    transfer(req, sizeof(req), true);
    transfer(res, sizeof(res), false);
    fatal_if(le32toh(res[0]) != kUspV1Magic || res[1] != req[1] || res[2] ||
             le32toh(res[4]) > 3, "Native SystemC response rejected");
    updateIrq(le32toh(res[4]));
    return le32toh(res[3]);
}

uint64_t NativeSocDevice::callV2(uint16_t op, uint64_t offset, uint64_t data, uint32_t length,
                                 uint32_t flags, const void *txPayload, void *rxPayload) {
    UspV2Frame req{};
    req.magic = kUspV2Magic;
    req.seq = ++sequence;
    req.op = op;
    req.length = length;
    req.flags = flags;
    req.addr = offset;
    req.data = data;
    req.time_ps = curTick();
    transfer(&req, sizeof(req), true);
    if (txPayload && length > 0) {
        transfer(const_cast<void *>(txPayload), length, true);
    }
    for (;;) {
        UspV2Frame msg{};
        transfer(&msg, sizeof(msg), false);
        fatal_if(msg.magic != kUspV2Magic, "Native usp-bus-v2 magic mismatch");
        if (msg.op == kOpDmaRead || msg.op == kOpDmaWrite) {
            const bool isWr = (msg.op == kOpDmaWrite);
            std::vector<uint8_t> buf(msg.length > 0 ? msg.length : 1, 0);
            if (isWr && msg.length > 0) {
                fatal_if(msg.length > kUspV2MaxBurst, "Oversized usp-bus-v2 DMA write");
                transfer(buf.data(), msg.length, false);
            }
            UspV2Frame dres{};
            dres.magic = kUspV2Magic;
            dres.seq = msg.seq;
            dres.op = static_cast<uint16_t>(msg.op | kOpResponse);
            dres.addr = msg.addr;
            dres.time_ps = curTick();
            if (!dmaEnabled || msg.length == 0 || msg.length > kUspV2MaxBurst) {
                dres.status = 3; // DENIED
                transfer(&dres, sizeof(dres), true);
            } else {
                if (isWr) {
                    sys->physProxy.writeBlob(msg.addr, buf.data(), msg.length);
                    dres.status = 0;
                    transfer(&dres, sizeof(dres), true);
                } else {
                    sys->physProxy.readBlob(msg.addr, buf.data(), msg.length);
                    dres.status = 0;
                    dres.length = msg.length;
                    dres.flags = kFlagPayload | kFlagBurst64;
                    transfer(&dres, sizeof(dres), true);
                    transfer(buf.data(), msg.length, true);
                }
            }
            continue;
        }
        fatal_if(msg.op != static_cast<uint16_t>(op | kOpResponse) || msg.seq != req.seq ||
                 msg.status != 0, "Native usp-bus-v2 response rejected");
        if ((msg.flags & kFlagPayload) && msg.length > 0) {
            fatal_if(msg.length > kUspV2MaxBurst || !rxPayload, "Invalid usp-bus-v2 payload");
            transfer(rxPayload, msg.length, false);
        }
        updateIrq(msg.irq_mask);
        return msg.data;
    }
}

Tick NativeSocDevice::access(PacketPtr pkt, bool writing) {
    if (kind == 2) {
        fatal_if(pkt->getSize() != 4 || pkt->getAddr() != base, "Unsupported native finisher offset");
        if (writing) exitSimLoop("Native SoC guest finisher", pkt->getLE<uint32_t>() == 0x5555 ? 0 : 1);
        else pkt->setLE<uint32_t>(0);
        pkt->makeAtomicResponse();
        return latency;
    }
    if (!useV2) {
        fatal_if(pkt->getSize() != 4 || (pkt->getAddr() & 3),
                 "Native usp-mmio32-v1 accepts aligned 32-bit accesses only");
        uint32_t value = call(writing ? 1 : 0, kind * 0x1000 + pkt->getAddr() - base,
                              writing ? pkt->getLE<uint32_t>() : 0);
        if (!writing) pkt->setLE<uint32_t>(value);
    } else {
        const uint32_t sz = pkt->getSize();
        const uint64_t off = pkt->getAddr() - base;
        if (sz > 8 && (sz % 8) == 0 && sz <= kUspV2MaxBurst) {
            if (writing) {
                callV2(kOpBurstWrite, off, 0, sz, kFlagPayload | kFlagBurst64,
                       pkt->getConstPtr<uint8_t>(), nullptr);
            } else {
                callV2(kOpBurstRead, off, 0, sz, kFlagBurst64, nullptr, pkt->getPtr<uint8_t>());
            }
        } else {
            fatal_if(sz != 1 && sz != 2 && sz != 4 && sz != 8, "Unsupported usp-bus-v2 size");
            uint64_t val = 0;
            if (writing) std::memcpy(&val, pkt->getConstPtr<uint8_t>(), sz);
            uint64_t out = callV2(writing ? kOpWrite : kOpRead, off, val, sz,
                                  sz == 8 ? kFlagBurst64 : 0, nullptr, nullptr);
            if (!writing) std::memcpy(pkt->getPtr<uint8_t>(), &out, sz);
        }
    }
    pkt->makeAtomicResponse();
    return latency;
}
void NativeSocDevice::serialize(CheckpointOut &) const {
    fatal("Native live SystemC checkpoint unsupported");
}
void NativeSocDevice::unserialize(CheckpointIn &) {
    fatal("Native live SystemC restore unsupported");
}
}
