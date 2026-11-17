// SPDX-License-Identifier: Apache-2.0
#include "native_soc_device.hh"
#include "base/logging.hh"
#include "mem/packet.hh"
#include "mem/packet_access.hh"
#include "sim/sim_exit.hh"
#include <cerrno>
#include <cstring>
#include <endian.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
namespace gem5 {
NativeSocDevice::NativeSocDevice(const NativeSocDeviceParams &p) : PioDevice(p),
    platform(p.platform), base(p.base), span(p.span), kind(p.kind), irq(p.irq),
    latency(p.pio_latency) {
    fatal_if(kind > 2 || !span, "Unsupported native device contract");
    if (kind == 2) return;
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    fatal_if(p.socket_path.empty() || p.socket_path.size() >= sizeof(addr.sun_path),
             "Invalid native SystemC socket");
    std::strcpy(addr.sun_path, p.socket_path.c_str());
    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    timeval timeout{2, 0};
    fatal_if(fd < 0 || setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
             setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) ||
             connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)),
             "Native SystemC instance connection failed");
}
NativeSocDevice::~NativeSocDevice() { if (fd >= 0) close(fd); }
void NativeSocDevice::startup() {
    PioDevice::startup();
    if (kind != 2) call(2, 0, 0);
}
uint32_t NativeSocDevice::call(uint32_t op, uint32_t offset, uint32_t data) {
    uint32_t req[5] = {htole32(0x31505355), htole32(++sequence), htole32(op),
                       htole32(offset), htole32(data)}, res[5];
    auto transfer = [this](void *buffer, size_t size, bool writing) {
        for (size_t pos = 0; pos < size;) {
            auto *bytes = static_cast<char *>(buffer);
            ssize_t n = writing ? send(fd, bytes + pos, size - pos, MSG_NOSIGNAL)
                                : recv(fd, bytes + pos, size - pos, 0);
            if (n < 0 && errno == EINTR) continue;
            fatal_if(n <= 0, "Native SystemC instance disconnected or timed out");
            pos += n;
        }
    };
    transfer(req, sizeof(req), true); transfer(res, sizeof(res), false);
    fatal_if(le32toh(res[0]) != 0x31505355 || res[1] != req[1] || res[2] ||
             le32toh(res[4]) > 3, "Native SystemC response rejected");
    uint32_t next = !!(le32toh(res[4]) & (1U << kind));
    if (next != level) {
        if (next) platform->postPciInt(irq);
        else platform->clearPciInt(irq);
    }
    level = next;
    return le32toh(res[3]);
}
Tick NativeSocDevice::access(PacketPtr pkt, bool writing) {
    fatal_if(pkt->getSize() != 4 || (pkt->getAddr() & 3),
             "Native source-IP/finisher accepts aligned 32-bit accesses only");
    if (kind == 2) {
        fatal_if(pkt->getAddr() != base, "Unsupported native finisher offset");
        if (writing) exitSimLoop("Native SoC guest finisher", pkt->getLE<uint32_t>() == 0x5555 ? 0 : 1);
        else pkt->setLE<uint32_t>(0);
    } else {
        uint32_t value = call(writing ? 1 : 0, kind * 0x1000 + pkt->getAddr() - base,
                              writing ? pkt->getLE<uint32_t>() : 0);
        if (!writing) pkt->setLE<uint32_t>(value);
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
