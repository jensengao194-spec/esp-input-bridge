#include <Arduino.h>
#include <ArduinoJson.h>
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>
#include <atomic>
#include <Preferences.h>
#include "report_map.h"
#include "pairing_policy.h"
#include <esp_random.h>

#if CONFIG_IDF_TARGET_ESP32S3
#if ARDUINO_USB_MODE != 0
#error "S3 firmware requires native USB OTG mode (ARDUINO_USB_MODE=0)"
#endif
#include <USB.h>
#include <USBHID.h>
static constexpr char CHIP_NAME[]="ESP32-S3";
static USBHID usbHid;
class BridgeUSBDevice : public USBHIDDevice {
public:
    BridgeUSBDevice() { USBHID::addDevice(this,sizeof(REPORT_MAP)); }
    uint16_t _onGetDescriptor(uint8_t* dst) override {
        memcpy(dst,REPORT_MAP,sizeof(REPORT_MAP));
        return sizeof(REPORT_MAP);
    }
} usbDevice;
static bool usbReady() { return USB && usbHid.ready(); }
#elif CONFIG_IDF_TARGET_ESP32C3
static constexpr char CHIP_NAME[]="ESP32-C3";
static bool usbReady() { return false; }
#else
#error "ESP Input Bridge supports ESP32-C3 and ESP32-S3 only"
#endif

#if MYNEWT_VAL_BLE_SM_LEGACY || !MYNEWT_VAL_BLE_SM_SC_ONLY
#error "Secure Connections Only is required; legacy pairing must be disabled"
#endif

enum class Output { None, BLE, USB };
static Output activeOutput=Output::None;
static const char* outputName() {
    return activeOutput==Output::USB ? "usb" : activeOutput==Output::BLE ? "ble" : "none";
}

#if __has_include("secrets.h")
#include "secrets.h"
#else
#define CONTROL_TOKEN "CHANGE_ME"
#endif

static constexpr char SERVICE[] = "c3a00001-7e45-4f9b-9d83-9bb45a2c1000";
static constexpr char COMMAND[] = "c3a00002-7e45-4f9b-9d83-9bb45a2c1000";
static constexpr char REPLY[]   = "c3a00003-7e45-4f9b-9d83-9bb45a2c1000";
static constexpr uint16_t NONE = BLE_HS_CONN_HANDLE_NONE;
static constexpr size_t MAX_LINE = 511;
static constexpr uint32_t LEASE_MS = 30000;

static NimBLECharacteristic *keyboardReport, *mouseReport, *replyReport;
static std::atomic<uint16_t> keyboardPeer{NONE}, mousePeer{NONE};
static std::atomic<bool> peerLost{false};
static uint32_t armedUntil = 0;
static bool armed = false;
static std::atomic<uint16_t> owner{NONE}; // NONE represents the USB serial controller.
static Preferences preferences;
static String controlToken;
static PairingPolicy pairing;
static std::atomic<uint32_t> pairingCode{0}, nextGeneration{1};
static uint32_t authBlockedUntil=0;

static bool trusted(const NimBLEConnInfo& info) {
    return PairingPolicy::trusted(info.isEncrypted(),info.isAuthenticated(),info.isBonded(),info.getSecKeySize());
}

static bool tokenMatches(const char* supplied) {
    size_t n=strnlen(supplied,97), expected=controlToken.length();
    unsigned difference=n^expected;
    for (size_t i=0;i<96;i++)
        difference|=(i<n ? static_cast<uint8_t>(supplied[i]) : 0) ^
                    (i<expected ? static_cast<uint8_t>(controlToken[i]) : 0);
    return expected>=32 && expected<=96 && n<=96 && difference==0;
}

struct Message { char line[MAX_LINE+1]; uint16_t peer; uint32_t generation; };
static QueueHandle_t queue;
struct Parser {
    char data[MAX_LINE+1]{};
    size_t len = 0;
    bool overflow = false;
    uint32_t updated = 0;
    void feed(uint8_t byte, uint16_t peer, uint32_t generation=0) {
        if (len && millis()-updated > 2000) { len=0; overflow=true; }
        updated=millis();
        if (byte=='\n') {
            if (!overflow && len) {
                data[len]=0;
                Message message{};
                memcpy(message.line, data, len+1);
                message.peer=peer;
                message.generation=generation;
                // Queue full => no ACK. Host times out and stops; never execute a dropped command.
                xQueueSend(queue, &message, 0);
            }
            len=0; overflow=false;
        } else if (!overflow && byte!='\r') {
            if (len>=MAX_LINE) { overflow=true; len=0; }
            else data[len++]=byte;
        }
    }
};
static Parser serialParser;
struct PeerParser {
    std::atomic<uint16_t> peer{NONE};
    std::atomic<uint32_t> generation{0}, connectedAt{0};
    Parser parser;
};
static PeerParser bleParsers[3]; // Never mix fragments from different controllers.

static uint32_t generationOf(uint16_t peer) {
    for (auto& p:bleParsers) if (p.peer.load()==peer) return p.generation.load();
    return 0;
}

static bool bleReady() {
    uint16_t peer=keyboardPeer.load();
    if (peer==NONE || mousePeer.load()!=peer) return false;
    return trusted(NimBLEDevice::getServer()->getPeerInfoByHandle(peer));
}

static bool hidReady() { return bleReady() || usbReady(); }
static bool activeReady() {
    return activeOutput==Output::USB ? usbReady() : activeOutput==Output::BLE && bleReady();
}

static bool sendKeyboard(const uint8_t* report, size_t len) {
#if CONFIG_IDF_TARGET_ESP32S3
    if (activeOutput==Output::USB) return usbReady() && usbHid.SendReport(1,report,len);
#endif
    return activeOutput==Output::BLE && bleReady() && keyboardReport->notify(report,len,keyboardPeer.load());
}

static bool sendMouse(const uint8_t* report, size_t len) {
#if CONFIG_IDF_TARGET_ESP32S3
    if (activeOutput==Output::USB) return usbReady() && usbHid.SendReport(2,report,len);
#endif
    return activeOutput==Output::BLE && bleReady() && mouseReport->notify(report,len,mousePeer.load());
}

static bool releaseAll() {
    uint8_t k[8]={}, m[4]={};
    bool ok=true;
    uint16_t kp=keyboardPeer.load(), mp=mousePeer.load();
    if (kp!=NONE) ok=keyboardReport->notify(k, sizeof(k), kp) && ok;
    if (mp!=NONE) ok=mouseReport->notify(m, sizeof(m), mp) && ok;
#if CONFIG_IDF_TARGET_ESP32S3
    if (usbReady()) {
        ok=usbHid.SendReport(1,k,sizeof(k)) && ok;
        ok=usbHid.SendReport(2,m,sizeof(m)) && ok;
    }
#endif
    return ok;
}

class InputCallbacks : public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic* c, NimBLEConnInfo& info, uint16_t value) override {
        auto& slot = c==keyboardReport ? keyboardPeer : mousePeer;
        if (value & 1) {
            // One HID target. Never broadcast input to every BLE connection.
            uint16_t other = c==keyboardReport ? mousePeer.load() : keyboardPeer.load();
            if ((slot.load()!=NONE && slot.load()!=info.getConnHandle()) ||
                (other!=NONE && other!=info.getConnHandle())) {
                NimBLEDevice::getServer()->disconnect(info.getConnHandle());
                return;
            }
            slot=info.getConnHandle();
        } else if (slot.load()==info.getConnHandle()) {
            slot=NONE; peerLost=true;
        }
    }
} inputCallbacks;

class CommandCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* c, NimBLEConnInfo& info) override {
        if (!trusted(info)) return;
        PeerParser* slot=nullptr;
        for (auto& p:bleParsers) if (p.peer.load()==info.getConnHandle()) slot=&p;
        if (!slot) return;
        auto value=c->getValue();
        for (size_t i=0; i<value.size(); i++) slot->parser.feed(value.data()[i],info.getConnHandle(),slot->generation.load());
    }
} commandCallbacks;

class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer* server, NimBLEConnInfo& info) override {
        uint16_t peer=info.getConnHandle();
        if (!NimBLEDevice::isBonded(info.getIdAddress()) && !pairing.claim(peer,millis())) {
            server->disconnect(peer);
            NimBLEDevice::startAdvertising();
            return;
        }
        for (auto& p:bleParsers) if (p.peer.load()==NONE) {
            p.parser=Parser{};
            p.connectedAt=millis();
            p.generation=nextGeneration.fetch_add(1);
            p.peer=peer;
            break;
        }
        NimBLEDevice::startSecurity(peer);
        NimBLEDevice::startAdvertising(); // Allow a separate BLE controller and HID target.
    }
    void onDisconnect(NimBLEServer*, NimBLEConnInfo& info, int) override {
        bool affected=keyboardPeer.load()==info.getConnHandle() || mousePeer.load()==info.getConnHandle() ||
                      owner.load()==info.getConnHandle();
        if (keyboardPeer.load()==info.getConnHandle()) keyboardPeer=NONE;
        if (mousePeer.load()==info.getConnHandle()) mousePeer=NONE;
        for (auto& p:bleParsers) if (p.peer.load()==info.getConnHandle()) { p.peer=NONE; p.parser=Parser{}; }
        if (pairing.owns(info.getConnHandle())) pairing.close(); // One attempt per USB window.
        // Rejected strangers must not cancel an unrelated legitimate controller.
        if (affected) peerLost=true;
        NimBLEDevice::startAdvertising();
    }
    void onAuthenticationComplete(NimBLEConnInfo& info) override {
        if (!trusted(info)) NimBLEDevice::getServer()->disconnect(info.getConnHandle());
        if (pairing.owns(info.getConnHandle())) pairing.close();
    }
    uint32_t onPassKeyDisplay() override { return pairingCode.load(); }
    void onConfirmPassKey(NimBLEConnInfo& info, uint32_t) override {
        NimBLEDevice::injectConfirmPasskey(info,false); // No silent numeric confirmation.
    }
} serverCallbacks;

struct Cached { char id[33]{}; uint32_t hash=0, generation=0; uint16_t peer=NONE; String response; };
static Cached cache[32];
static size_t cacheIndex=0;

static uint32_t fingerprint(const char* s) {
    uint32_t h=2166136261u;
    while (*s) { h^=static_cast<uint8_t>(*s++); h*=16777619u; }
    return h;
}

static void emit(const String& text, uint16_t peer) {
    if (peer==NONE) { Serial.println(text); return; }
    String framed=text+"\n";
    for (size_t i=0;i<framed.length();i+=20) {
        size_t n=std::min(static_cast<size_t>(20), framed.length()-i);
        if (!replyReport->notify(reinterpret_cast<const uint8_t*>(framed.c_str()+i), n, peer)) break;
        delay(10);
    }
}

static bool integer(JsonVariantConst v, int lo, int hi) {
    return v.is<int>() && !v.is<bool>() && v.as<int>()>=lo && v.as<int>()<=hi;
}

static const char* perform(JsonDocument& d, uint16_t peer) {
    const char* op=d["op"] | "";
    if (!strcmp(op,"ping")) return nullptr;
    if (!strcmp(op,"configure")) {
        if (peer!=NONE) return "usb_setup_only";
        if (armed) return "disarm_before_setup";
        const char* token=d["token"] | "";
        size_t n=strlen(token);
        if (n<32 || n>96) return "bad_token_length";
        for (size_t i=0;i<n;i++) if (token[i]<33 || token[i]>126) return "bad_token_character";
        if (controlToken.length()>=32 && !tokenMatches(d["current_token"] | "")) return "current_token_required";
        if (!preferences.putString("token",token)) return "storage_failed";
        controlToken=token;
        return nullptr;
    }
    if (!strcmp(op,"pair") || !strcmp(op,"forget_bonds")) {
        if (peer!=NONE) return "usb_setup_only";
        if (armed) return "disarm_before_setup";
        if (!tokenMatches(d["token"] | "")) return "bad_token";
        if (!strcmp(op,"forget_bonds")) {
            pairing.close();
            for (auto h:NimBLEDevice::getServer()->getPeerDevices()) NimBLEDevice::getServer()->disconnect(h);
            return NimBLEDevice::deleteAllBonds() ? nullptr : "storage_failed";
        }
        if (NimBLEDevice::getNumBonds()>=3) return "bond_slots_full";
        if (pairing.active(millis())) return "pairing_already_open";
        uint32_t code=esp_random()%1000000;
        pairingCode=code;
        if (!pairing.open(millis())) return "pairing_already_open";
        return nullptr;
    }
    // Always permit emergency release/disarm, independent of lease ownership.
    if (!strcmp(op,"release") || !strcmp(op,"disarm")) {
        if (!strcmp(op,"disarm")) armed=false;
        bool ok=releaseAll();
        if (!strcmp(op,"disarm")) activeOutput=Output::None;
        if (!strcmp(op,"disarm")) owner=NONE;
        return ok ? nullptr : "release_uncertain";
    }
    if (!strcmp(op,"arm")) {
        const char* token=d["token"] | "";
        if (controlToken.length()<32) return "configure_token_first";
        if (static_cast<int32_t>(authBlockedUntil-millis())>0) return "auth_backoff";
        if (!tokenMatches(token)) { authBlockedUntil=millis()+2000; return "bad_token"; }
        if (pairing.active(millis())) return "pairing_in_progress";
        if (armed && static_cast<int32_t>(armedUntil-millis())>0 && owner!=peer) return "controller_busy";
        const char* requested=d["output"] | "auto";
        if (strcmp(requested,"auto") && strcmp(requested,"ble") && strcmp(requested,"usb")) return "bad_output";
#if CONFIG_IDF_TARGET_ESP32C3
        if (!strcmp(requested,"usb")) return "unsupported_output";
#endif
        Output chosen=Output::None;
        if (!strcmp(requested,"usb") || (!strcmp(requested,"auto") && usbReady())) chosen=Output::USB;
        else chosen=Output::BLE;
        if (!(chosen==Output::USB ? usbReady() : bleReady())) return "hid_not_ready";
        // Keep one output for the lease. Never switch targets after a disconnect.
        if (armed && activeOutput!=chosen) {
            if (!releaseAll()) { armed=false; return "release_uncertain"; }
        }
        activeOutput=chosen;
        owner=peer; armed=true; armedUntil=millis()+LEASE_MS;
        return nullptr;
    }
    if (!armed || static_cast<int32_t>(armedUntil-millis())<=0) return "not_armed";
    if (owner!=peer) return "controller_busy";
    if (!activeReady()) { armed=false; return "hid_not_ready"; }
    armedUntil=millis()+LEASE_MS;
    if (!strcmp(op,"key")) {
        if (!integer(d["mod"],0,255) || !d["keys"].is<JsonArray>()) return "bad_key";
        JsonArray keys=d["keys"].as<JsonArray>();
        if (keys.size()>6) return "too_many_keys";
        uint8_t report[8]={static_cast<uint8_t>(d["mod"].as<int>()),0};
        size_t i=2;
        for (JsonVariant key:keys) {
            if (!integer(key,4,101)) return "bad_usage";
            for (size_t j=2;j<i;j++) if (report[j]==key.as<int>()) return "duplicate_key";
            report[i++]=key.as<int>();
        }
        bool ok=sendKeyboard(report,sizeof(report));
        delay(35); // Bounded pulse. No persistent key-down operation exists.
        uint8_t zero[8]={};
        bool released=sendKeyboard(zero,sizeof(zero));
        delay(20);
        if (!ok || !released) { armed=false; return "hid_delivery_uncertain"; }
        return nullptr;
    }
    if (!strcmp(op,"move") || !strcmp(op,"scroll") || !strcmp(op,"click")) {
        int8_t report[4]={};
        if (!strcmp(op,"move")) {
            if (!integer(d["dx"],-127,127) || !integer(d["dy"],-127,127)) return "bad_move";
            report[1]=d["dx"].as<int>(); report[2]=d["dy"].as<int>();
        } else if (!strcmp(op,"scroll")) {
            if (!integer(d["steps"],-20,20)) return "bad_scroll";
            report[3]=d["steps"].as<int>();
        } else {
            if (!integer(d["button"],1,4) || (d["button"].as<int>()!=1 && d["button"].as<int>()!=2 && d["button"].as<int>()!=4))
                return "bad_button";
            report[0]=d["button"].as<int>();
        }
        bool ok=sendMouse(reinterpret_cast<uint8_t*>(report),sizeof(report));
        delay(35);
        if (report[0]) {
            uint8_t zero[4]={};
            ok=sendMouse(zero,sizeof(zero)) && ok;
            delay(20);
        }
        if (!ok) { armed=false; return "hid_delivery_uncertain"; }
        return nullptr;
    }
    return "unknown_op";
}

static void handle(const Message& msg) {
    // A queued BLE command must not survive loss of its encrypted connection.
    if (msg.peer!=NONE && (generationOf(msg.peer)!=msg.generation ||
        !trusted(NimBLEDevice::getServer()->getPeerInfoByHandle(msg.peer)))) return;
    JsonDocument d;
    auto err=deserializeJson(d,msg.line,DeserializationOption::NestingLimit(4));
    if (err || !d.is<JsonObject>()) return;
    const char* id=d["id"] | "";
    size_t len=strlen(id);
    if (len!=32) return;
    for (size_t i=0;i<len;i++) if (!isxdigit(static_cast<unsigned char>(id[i]))) return;
    uint32_t hash=fingerprint(msg.line);
    for (auto& entry:cache) if (entry.peer==msg.peer && entry.generation==msg.generation && !strcmp(entry.id,id)) {
        if (entry.hash==hash) emit(entry.response,msg.peer);
        else {
            JsonDocument conflict;
            conflict["v"]=1; conflict["id"]=id; conflict["ok"]=false; conflict["error"]="id_conflict";
            String out; serializeJson(conflict,out); emit(out,msg.peer);
        }
        return;
    }
    const char* error=integer(d["v"],1,1) ? perform(d,msg.peer) : "bad_version";
    JsonDocument ack;
    ack["v"]=1; ack["id"]=id; ack["ok"]=(error==nullptr); ack["hid_ready"]=hidReady();
    if (!strcmp(d["op"] | "","ping") || !strcmp(d["op"] | "","arm")) {
        ack["chip"]=CHIP_NAME;
        ack["firmware"]="0.2.0";
        JsonArray outputs=ack["outputs"].to<JsonArray>();
        outputs.add("ble");
#if CONFIG_IDF_TARGET_ESP32S3
        outputs.add("usb");
#endif
        ack["ble_ready"]=bleReady(); ack["usb_ready"]=usbReady();
        ack["active_output"]=outputName();
        ack["token_configured"]=(controlToken.length()>=32);
        ack["ble_security"]="sc_mitm_bonded";
        ack["pairing_open"]=pairing.active(millis());
        ack["bond_count"]=NimBLEDevice::getNumBonds();
    }
    if (!error && !strcmp(d["op"] | "","pair")) {
        char code[7]; snprintf(code,sizeof(code),"%06lu",static_cast<unsigned long>(pairingCode.load()));
        ack["passkey"]=code;
        ack["expires_in_seconds"]=120;
    }
    if (error) ack["error"]=error;
    String out; serializeJson(ack,out);
    auto& entry=cache[cacheIndex++%32];
    strlcpy(entry.id,id,sizeof(entry.id)); entry.hash=hash; entry.response=out;
    entry.peer=msg.peer; entry.generation=msg.generation;
    emit(out,msg.peer);
}

void setup() {
    Serial.begin(115200);
    if (!preferences.begin("esp-input",false)) abort();
    controlToken=preferences.getString("token","");
    if (controlToken.length()<32 && strlen(CONTROL_TOKEN)>=32 &&
        strcmp(CONTROL_TOKEN,"REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS")) controlToken=CONTROL_TOKEN;
#if CONFIG_IDF_TARGET_ESP32S3
    usbHid.begin();
    USB.productName("ESP Input Bridge S3");
    USB.manufacturerName("ESP Input Bridge Project");
    USB.begin();
#endif
    queue=xQueueCreate(8,sizeof(Message));
    if (!queue) abort();
    NimBLEDevice::init("ESP Input Bridge");
    NimBLEDevice::setSecurityAuth(true,true,true); // Bonding + MITM + LE Secure Connections only.
    NimBLEDevice::setSecurityIOCap(BLE_HS_IO_DISPLAY_ONLY); // USB setup shows the one-time code.
    auto* server=NimBLEDevice::createServer();
    server->setCallbacks(&serverCallbacks);
    auto* hid=new NimBLEHIDDevice(server);
    hid->setManufacturer("ESP Input Bridge Project");
    hid->setHidInfo(0,0x01);
    hid->setReportMap(REPORT_MAP,sizeof(REPORT_MAP));
    keyboardReport=hid->getInputReport(1);
    mouseReport=hid->getInputReport(2);
    hid->getOutputReport(1); // Keyboard LED output report.
    keyboardReport->setCallbacks(&inputCallbacks);
    mouseReport->setCallbacks(&inputCallbacks);
    uint8_t k[8]={}, m[4]={};
    keyboardReport->setValue(k,sizeof(k)); mouseReport->setValue(m,sizeof(m));
    hid->setBatteryLevel(100);
    hid->startServices();
    auto* service=server->createService(SERVICE);
    auto* cmd=service->createCharacteristic(COMMAND,NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_AUTHEN,20);
    cmd->setCallbacks(&commandCallbacks);
    replyReport=service->createCharacteristic(REPLY,NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::READ_AUTHEN);
    service->start();
    auto* ad=NimBLEDevice::getAdvertising();
    ad->setAppearance(GENERIC_HID);
    ad->addServiceUUID(hid->getHidService()->getUUID());
    // 128-bit custom service plus name belongs in scan response (31-byte legacy limit).
    NimBLEAdvertisementData scan;
    scan.setName("ESP Input Bridge");
    ad->setScanResponseData(scan);
    ad->enableScanResponse(true);
    ad->start();
    Serial.printf("ESP Input Bridge %s ready (token required)\n",CHIP_NAME);
}

void loop() {
    if (pairing.pending()!=NONE && !pairing.active(millis())) {
        NimBLEDevice::getServer()->disconnect(pairing.pending());
        pairing.close();
    }
    for (auto& p:bleParsers) {
        uint16_t peer=p.peer.load();
        // Do not leave an unauthenticated connection occupying a slot indefinitely.
        uint32_t timeout=pairing.owns(peer) ? 120000 : 10000;
        if (peer!=NONE && millis()-p.connectedAt.load()>timeout &&
            !trusted(NimBLEDevice::getServer()->getPeerInfoByHandle(peer)))
            NimBLEDevice::getServer()->disconnect(peer);
    }
    if (peerLost.exchange(false) || (armed && (static_cast<int32_t>(armedUntil-millis())<=0 || !activeReady()))) {
        armed=false; releaseAll();
        activeOutput=Output::None;
        owner=NONE;
    }
    // Bound serial ingestion so malformed streams cannot starve the lease watchdog.
    for (int i=0;i<128 && Serial.available();i++) serialParser.feed(Serial.read(),NONE);
    Message msg{};
    if (xQueueReceive(queue,&msg,0)==pdTRUE) handle(msg);
    delay(1);
}
