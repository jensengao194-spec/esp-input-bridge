from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeviceInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    chip: Literal["ESP32-C3", "ESP32-S3"]
    firmware: str
    outputs: list[Literal["ble", "usb"]] = Field(min_length=1, max_length=2)
    ble_ready: bool
    usb_ready: bool
    active_output: Literal["none", "ble", "usb"]
    token_configured: bool
    ble_security: Literal["sc_mitm_bonded"]

    @model_validator(mode="after")
    def coherent(self):
        expected = ["ble"] if self.chip == "ESP32-C3" else ["ble", "usb"]
        if sorted(self.outputs) != expected or (self.chip == "ESP32-C3" and self.usb_ready):
            raise ValueError("firmware capabilities do not match the chip")
        return self


async def probe(transport) -> DeviceInfo:
    return DeviceInfo.model_validate(await transport.request({"op": "ping"}))
