#pragma once
#include <esp_ota_ops.h>
#include <esp_app_desc.h>
#include <mbedtls/base64.h>
#include <mbedtls/sha256.h>
#include "bridge_version.h"

#if !CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
#error "OTA requires a rollback-enabled SDK and bootloader"
#endif

// Called only from loop(), never from the BLE stack task. Ownership includes
// connection generation, so a reused BLE connection handle cannot resume a write.
class BridgeOTA {
    struct DataPacket { uint16_t peer, length; uint32_t generation; uint8_t data[512]; };
    QueueHandle_t dataQueue=nullptr;
    std::atomic<bool> overflow{false};
    uint8_t sessionId[16]{};
    esp_ota_handle_t handle=0;
    const esp_partition_t* target=nullptr;
    mbedtls_sha256_context sha{};
    uint16_t peer=0xffff;
    uint32_t generation=0, updated=0, restartAt=0, pendingAt=0;
    size_t expected=0, received=0;
    String session, expectedHash;
    bool pending=false;
    static String hex(const uint8_t* bytes, size_t len) {
        String out; out.reserve(len*2);
        const char* digits="0123456789abcdef";
        for (size_t i=0;i<len;i++) { out+=digits[bytes[i]>>4]; out+=digits[bytes[i]&15]; }
        return out;
    }
public:
    bool busy() const { return handle || restartAt; }
    bool needsConfirmation() const { return pending; }
    void setup() {
        dataQueue=xQueueCreate(16,sizeof(DataPacket));
        if (!dataQueue) ::abort();
        esp_ota_img_states_t state;
        pending=esp_ota_get_state_partition(esp_ota_get_running_partition(),&state)==ESP_OK &&
                state==ESP_OTA_IMG_PENDING_VERIFY;
        pendingAt=millis();
    }
    void abort() {
        if (handle) esp_ota_abort(handle);
        handle=0; session="";
        mbedtls_sha256_free(&sha);
    }
    void enqueue(const uint8_t* data, size_t length, uint16_t from, uint32_t gen) {
        if (!dataQueue || length<21 || length>512) return;
        DataPacket packet{}; packet.peer=from; packet.generation=gen; packet.length=length;
        memcpy(packet.data,data,length);
        if (xQueueSend(dataQueue,&packet,0)!=pdTRUE) overflow=true;
    }
    void drain() {
        if (overflow.exchange(false) && handle) abort();
        DataPacket packet;
        // One bounded host window has at most 8 packets; the queue has 16 slots.
        for (unsigned i=0;i<16 && xQueueReceive(dataQueue,&packet,0)==pdTRUE;i++) {
            if (!handle || packet.peer!=peer || packet.generation!=generation || memcmp(packet.data,sessionId,16)) continue;
            uint32_t offset=uint32_t(packet.data[16]) | uint32_t(packet.data[17])<<8 |
                            uint32_t(packet.data[18])<<16 | uint32_t(packet.data[19])<<24;
            size_t n=packet.length-20;
            if (offset!=received || received+n>expected || esp_ota_write(handle,packet.data+20,n)!=ESP_OK ||
                mbedtls_sha256_update(&sha,packet.data+20,n)) { abort(); continue; }
            received+=n; updated=millis();
        }
    }
    void tick(bool connected) {
        if (handle && (!connected || millis()-updated>30000)) abort();
        drain();
        if (restartAt && static_cast<int32_t>(millis()-restartAt)>=0) ESP.restart();
        // Only an authenticated post-boot health check confirms a new image.
        if (pending && millis()-pendingAt>90000) esp_ota_mark_app_invalid_rollback_and_reboot();
    }
    uint16_t owner() const { return peer; }
    uint32_t ownerGeneration() const { return generation; }
    void status(JsonDocument& ack) {
        auto* running=esp_ota_get_running_partition();
        auto* next=esp_ota_get_next_update_partition(nullptr);
        ack["running_slot"]=running->label;
        ack["next_slot"]=next ? next->label : "";
        ack["slot_bytes"]=next ? next->size : 0;
        ack["pending_verify"]=pending;
        ack["ota_active"]=bool(handle);
        ack["received"]=received;
        ack["ota_chunk_bytes"]=240;
        ack["ota_binary"]=true;
        ack["elf_sha256"]=hex(esp_app_get_description()->app_elf_sha256,32);
        ack["firmware"]=BRIDGE_FIRMWARE_VERSION;
        ack["chip"]=CHIP_NAME;
    }
    const char* perform(JsonDocument& d, uint16_t from, uint32_t gen, bool authenticated) {
        const char* op=d["op"] | "";
        if (!strcmp(op,"ota_status")) return nullptr;
        if (!strcmp(op,"ota_begin")) {
            if (!authenticated) return "bad_token";
            if (busy()) return "ota_busy";
            if (pending) return "confirm_firmware_first";
            xQueueReset(dataQueue); overflow=false;
            if (!d["size"].is<unsigned>() || d["size"].is<bool>()) return "bad_ota_size";
            size_t size=d["size"].as<unsigned>();
            String digest=d["sha256"] | "";
            if (digest.length()!=64) return "bad_sha256";
            for (char c:digest) if (!isxdigit(static_cast<unsigned char>(c))) return "bad_sha256";
            target=esp_ota_get_next_update_partition(nullptr);
            if (!target || target->address==esp_ota_get_running_partition()->address || size<256 || size>target->size) return "bad_ota_size";
            if (esp_ota_begin(target,OTA_WITH_SEQUENTIAL_WRITES,&handle)!=ESP_OK) { handle=0; return "ota_begin_failed"; }
            mbedtls_sha256_init(&sha);
            if (mbedtls_sha256_starts(&sha,0)) { abort(); return "sha_failed"; }
            esp_fill_random(sessionId,sizeof(sessionId));
            session=hex(sessionId,sizeof(sessionId));
            digest.toLowerCase(); expectedHash=digest;
            expected=size; received=0; peer=from; generation=gen; updated=millis();
            return nullptr;
        }
        if (!strcmp(op,"ota_confirm")) {
            if (!authenticated) return "bad_token";
            if (pending && esp_ota_mark_app_valid_cancel_rollback()!=ESP_OK) return "confirm_failed";
            pending=false; return nullptr;
        }
        if (!strcmp(op,"ota_rollback")) {
            if (!authenticated) return "bad_token";
            if (busy()) return "ota_busy";
            target=esp_ota_get_next_update_partition(nullptr);
            esp_app_desc_t previous;
            if (!target || esp_ota_get_partition_description(target,&previous)!=ESP_OK) return "rollback_unavailable";
            if (pending) {
                // Leave the image pending; reboot causes bootloader rollback.
                restartAt=millis()+1500; return nullptr;
            }
            if (esp_ota_set_boot_partition(target)!=ESP_OK) return "rollback_unavailable";
            restartAt=millis()+1500; return nullptr;
        }
        if (!handle || from!=peer || gen!=generation || session!=(d["session"] | "")) return "bad_ota_session";
        if (!strcmp(op,"ota_sync")) {
            drain();
            if (!handle) return "ota_stream_aborted";
            if (!d["offset"].is<unsigned>() || d["offset"].is<bool>() || d["offset"].as<unsigned>()!=received) {
                abort(); return "ota_stream_offset";
            }
            updated=millis(); return nullptr;
        }
        updated=millis();
        if (!strcmp(op,"ota_abort")) { abort(); return nullptr; }
        if (!strcmp(op,"ota_chunk")) {
            if (!d["offset"].is<unsigned>() || d["offset"].is<bool>() || d["offset"].as<unsigned>()!=received) return "bad_ota_offset";
            const char* encoded=d["data"] | "";
            size_t n=0; uint8_t data[240];
            if (mbedtls_base64_decode(data,sizeof(data),&n,reinterpret_cast<const uint8_t*>(encoded),strlen(encoded)) ||
                !n || received+n>expected) return "bad_ota_chunk";
            if (esp_ota_write(handle,data,n)!=ESP_OK || mbedtls_sha256_update(&sha,data,n)) {
                abort(); return "ota_write_failed";
            }
            received+=n; return nullptr;
        }
        if (!strcmp(op,"ota_finish")) {
            if (received!=expected) return "ota_incomplete";
            uint8_t digest[32];
            if (mbedtls_sha256_finish(&sha,digest) || hex(digest,32)!=expectedHash) { abort(); return "sha256_mismatch"; }
            esp_err_t result=esp_ota_end(handle);
            handle=0; session=""; mbedtls_sha256_free(&sha);
            if (result!=ESP_OK) return "ota_image_invalid";
            if (esp_ota_set_boot_partition(target)!=ESP_OK) return "ota_select_failed";
            restartAt=millis()+1500; return nullptr;
        }
        return "unknown_ota_op";
    }
    void reply(JsonDocument& ack, const char* op) {
        if (!strcmp(op,"ota_begin")) {
            ack["session"]=session; ack["target_slot"]=target->label; ack["chunk_bytes"]=240;
            ack["binary_window"]=8;
        }
        if (!strcmp(op,"ota_chunk") || !strcmp(op,"ota_sync")) ack["received"]=received;
        if (!strcmp(op,"ota_rollback")) {
            esp_app_desc_t previous;
            if (target && esp_ota_get_partition_description(target,&previous)==ESP_OK) {
                ack["target_slot"]=target->label;
                ack["elf_sha256"]=hex(previous.app_elf_sha256,32);
            }
        }
    }
};
static BridgeOTA ota;
