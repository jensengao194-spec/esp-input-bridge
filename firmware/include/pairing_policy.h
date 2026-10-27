#pragma once
#include <atomic>
#include <cstdint>

// Shared by the firmware and native tests. One attempt per physical setup window.
class PairingPolicy {
    std::atomic<uint32_t> deadline{0};
    std::atomic<uint16_t> candidate{0xffff};
public:
    bool open(uint32_t now, uint32_t duration=120000) {
        if (active(now)) return false;
        candidate=0xffff;
        deadline=now+duration;
        return true;
    }
    bool active(uint32_t now) const {
        uint32_t d=deadline.load();
        return d && static_cast<int32_t>(d-now)>0;
    }
    bool claim(uint16_t peer, uint32_t now) {
        if (!active(now)) return false;
        uint16_t empty=0xffff;
        return candidate.compare_exchange_strong(empty,peer);
    }
    bool owns(uint16_t peer) const { return candidate.load()==peer && peer!=0xffff; }
    uint16_t pending() const { return candidate.load(); }
    void close() { deadline=0; candidate=0xffff; }
    static bool trusted(bool encrypted, bool authenticated, bool bonded, unsigned keySize) {
        return encrypted && authenticated && bonded && keySize==16;
    }
};
