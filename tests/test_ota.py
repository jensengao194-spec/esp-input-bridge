import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from espcomputeruse.ota import inspect_image, upload
from espcomputeruse.protocol import UncertainDelivery, frame
from espcomputeruse.protocol import COMMAND_UUID, OTA_DATA_UUID
from espcomputeruse.transports import BLETransport


@pytest.fixture
def image(tmp_path):
    data = bytearray(1024)
    data[0] = 0xE9
    data[12:14] = (5).to_bytes(2, "little")
    data[32:36] = (0xABCD5432).to_bytes(4, "little")
    data[176:208] = bytes(range(32))
    path = tmp_path / "firmware.bin"
    path.write_bytes(data)
    return path


class Device:
    def __init__(self, chip="ESP32-C3", fail=False):
        self.chip, self.fail, self.ops, self.data = chip, fail, [], bytearray()

    async def request(self, command):
        frame(command)  # Every OTA request must fit the actual wire protocol.
        self.ops.append(command["op"])
        if command["op"] == "ota_status":
            return {"chip": self.chip, "slot_bytes": 1966080, "pending_verify": False}
        if command["op"] == "ota_begin":
            self.expected = command["sha256"]
            return {"session": "a" * 32, "target_slot": "app1", "chunk_bytes": 240}
        if command["op"] == "ota_chunk":
            if self.fail:
                raise UncertainDelivery("disconnected")
            assert command["offset"] == len(self.data)
            self.data.extend(base64.b64decode(command["data"]))
            return {"received": len(self.data)}
        if command["op"] == "ota_finish":
            assert hashlib.sha256(self.data).hexdigest() == self.expected
        return {"ok": True}


async def test_ota_full_image_and_wire_limits(image):
    device = Device()
    result = await upload(device, image, "x" * 96)
    assert bytes(device.data) == image.read_bytes()
    assert result["target_slot"] == "app1"
    assert result["elf_sha256"] == bytes(range(32)).hex()
    assert device.ops[-1] == "ota_finish"


async def test_ota_wrong_chip_never_erases(image):
    device = Device(chip="ESP32-S3")
    with pytest.raises(ValueError, match="chip"):
        await upload(device, image, "x" * 64)
    assert device.ops == ["ota_status"]


async def test_ota_disconnect_never_replays_or_selects_boot(image):
    device = Device(fail=True)
    with pytest.raises(UncertainDelivery):
        await upload(device, image, "x" * 64)
    assert device.ops.count("ota_chunk") == 1
    assert "ota_finish" not in device.ops


def test_ota_rejects_merged_image(tmp_path):
    path = tmp_path / "merged.bin"
    path.write_bytes(b"\xe9" + b"\0" * 1023)
    with pytest.raises(ValueError):
        inspect_image(path)


async def test_binary_stream_windows_and_offsets():
    transport = BLETransport("fake", timeout=1)
    session = "ab" * 16
    data = bytes(range(256)) * 20
    class Client:
        mtu_size = 247
        is_connected = True
        services = SimpleNamespace(get_characteristic=lambda _: SimpleNamespace(max_write_without_response_size=244))
        def __init__(self):
            self.data = bytearray()
            self.command = bytearray()
            self.windows = 0
            self.packets = 0
        async def write_gatt_char(self, uuid, packet, response):
            if uuid == OTA_DATA_UUID:
                assert not response and len(packet) <= 244
                assert packet[:16] == bytes.fromhex(session)
                assert int.from_bytes(packet[16:20], "little") == len(self.data)
                self.data.extend(packet[20:])
                self.packets += 1
            else:
                assert uuid == COMMAND_UUID and response
                self.command.extend(packet)
                if self.command.endswith(b"\n"):
                    request = json.loads(self.command)
                    self.command.clear()
                    assert request["op"] == "ota_sync" and request["offset"] == len(self.data)
                    self.windows += 1
                    transport._notify(None, json.dumps({"v": 1, "id": request["id"], "ok": True,
                                                      "received": len(self.data)}).encode() + b"\n")
    transport.client = Client()
    await transport.ota_stream(session, data, 8)
    assert transport.client.data == data
    assert transport.client.packets == 23 and transport.client.windows == 3


async def test_binary_stream_missing_checkpoint_stops_without_replay():
    transport = BLETransport("fake", timeout=0.01)
    class Client:
        mtu_size = 247
        is_connected = True
        services = SimpleNamespace(get_characteristic=lambda _: SimpleNamespace(max_write_without_response_size=244))
        packets = 0
        async def write_gatt_char(self, uuid, packet, response):
            if uuid == OTA_DATA_UUID:
                self.packets += 1
    transport.client = Client()
    with pytest.raises(UncertainDelivery):
        await transport.ota_stream("ab" * 16, b"x" * 10000, 8)
    assert transport.client.packets == 8


async def test_control_write_without_att_response_still_requires_protocol_ack():
    transport = BLETransport("fake", timeout=0.1)
    class Client:
        is_connected = True
        mtu_size = 247
        services = SimpleNamespace(get_characteristic=lambda _: SimpleNamespace(
            properties=["write", "write-without-response"], max_write_without_response_size=244))
        calls = 0
        async def write_gatt_char(self, uuid, packet, response):
            assert uuid == COMMAND_UUID and not response
            self.calls += 1
            request = json.loads(packet)
            transport._notify(None, json.dumps({"v": 1, "id": request["id"], "ok": True}).encode()+b"\n")
    transport.client = Client()
    assert (await transport.request({"op": "ping"}))["ok"]
    assert transport.client.calls == 1
