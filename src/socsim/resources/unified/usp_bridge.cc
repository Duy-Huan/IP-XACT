// SPDX-License-Identifier: Apache-2.0
#include "usp_bridge.hh"
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
UspBridge::UspBridge(const UspBridgeParams &p) : PioDevice(p),
    platform(p.platform), gcd(p.gcd_base), gpio(p.gpio_base),
    finish(p.finish_base), irqs{p.gcd_irq, p.gpio_irq}, latency(p.pio_latency) {
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    fatal_if(p.socket_path.empty() || p.socket_path.size() >= sizeof(address.sun_path),
             "USP invalid local SystemC socket");
    std::strcpy(address.sun_path, p.socket_path.c_str());
    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    fatal_if(fd < 0, "USP socket creation failed");
    timeval timeout{2, 0};
    fatal_if(setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
             setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)),
             "USP socket timeout setup failed");
    fatal_if(connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)),
             "USP SystemC bridge connection failed");
}
UspBridge::~UspBridge() { if (fd >= 0) close(fd); }
void UspBridge::startup() { PioDevice::startup(); call(2, 0, 0); }
AddrRangeList UspBridge::getAddrRanges() const {
    return {RangeSize(gcd, 0x1000), RangeSize(gpio, 0x1000), RangeSize(finish, 0x1000)};
}
uint32_t UspBridge::call(uint32_t op, uint32_t offset, uint32_t value) {
    uint32_t req[5] = {htole32(0x31505355), htole32(++sequence), htole32(op),
                       htole32(offset), htole32(value)}, res[5];
    auto transfer = [this](void *buffer, size_t size, bool writing) {
        for (size_t pos = 0; pos < size;) {
            auto *bytes = static_cast<char *>(buffer);
            ssize_t n = writing ? send(fd, bytes + pos, size - pos, MSG_NOSIGNAL)
                                : recv(fd, bytes + pos, size - pos, 0);
            if (n < 0 && errno == EINTR) continue;
            fatal_if(n <= 0, "USP SystemC bridge disconnected or timed out");
            pos += n;
        }
    };
    transfer(req, sizeof(req), true); transfer(res, sizeof(res), false);
    fatal_if(le32toh(res[0]) != 0x31505355 || res[1] != req[1] || res[2] != 0 ||
             le32toh(res[4]) > 3, "USP SystemC transaction rejected");
    uint32_t next = le32toh(res[4]);
    for (unsigned i = 0; i < 2; ++i) {
        if ((levels ^ next) & (1U << i)) {
            if (next & (1U << i)) platform->postPciInt(irqs[i]);
            else platform->clearPciInt(irqs[i]);
        }
    }
    levels = next;
    return le32toh(res[3]);
}
Tick UspBridge::access(PacketPtr pkt, bool writing) {
    Addr address = pkt->getAddr();
    fatal_if(pkt->getSize() != 4 || (address & 3),
             "USP bridge accepts aligned 32-bit accesses only");
    if (address >= finish && address < finish + 0x1000) {
        fatal_if(address != finish, "USP unsupported finisher access");
        // Linux's syscon poweroff performs read/modify/write at this register.
        // Match the supported SiFive test read value, without exiting on reads.
        if (!writing) pkt->setLE<uint32_t>(0);
        else {
            uint32_t code = pkt->getLE<uint32_t>();
            exitSimLoop("USP guest finisher", code == 0x5555 ? 0 : 1);
        }
    } else {
        uint32_t offset;
        if (address >= gcd && address < gcd + 0x1000) offset = address - gcd;
        else if (address >= gpio && address < gpio + 0x1000) offset = 0x1000 + address - gpio;
        else fatal("USP address outside declared IP windows");
        uint32_t value = call(writing ? 1 : 0, offset, writing ? pkt->getLE<uint32_t>() : 0);
        if (!writing) pkt->setLE<uint32_t>(value);
    }
    pkt->makeAtomicResponse();
    return latency;
}
void UspBridge::serialize(CheckpointOut &) const {
    fatal("USP live SystemC checkpoint is unsupported");
}
void UspBridge::unserialize(CheckpointIn &) {
    fatal("USP live SystemC checkpoint restore is unsupported");
}
}
