"""An isolated target that consumes the same wire commands as the real device.

This verifies orchestration, not BLE/USB delivery or a real model's visual reasoning.
"""
import json
from PIL import Image, ImageDraw, ImageFont

from .hid import decode
from .models import Plan
from .observation import Observation
from .protocol import frame, check_reply


class SimulatedTarget:
    def __init__(self, chip="ESP32-C3", *, usb_ready=False, ble_ready=True):
        if chip not in ("ESP32-C3", "ESP32-S3") or (chip == "ESP32-C3" and usb_ready):
            raise ValueError("invalid simulated chip/capabilities")
        self.chip, self.usb_ready, self.ble_ready = chip, usb_ready, ble_ready
        self.active_output = "none"
        self.x, self.y = 40.0, 70.0
        self.focused, self.value, self.submitted = False, "", False
        self.armed = False
        self.commands = []

    def cursor(self):
        return self.x, self.y

    def state(self):
        return {"focused": self.focused, "value": self.value, "submitted": self.submitted}

    def capture(self):
        image = Image.new("RGB", (960, 600), "#0c1624")
        d = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=22)
        title = ImageFont.load_default(size=32)
        d.text((48, 36), "ESP / HARDWARE INPUT LAB", fill="#e4eef9", font=title)
        d.text((48, 95), "SIMULATED TARGET - no OS input is generated", fill="#84a5bf", font=font)
        d.text((48, 170), "Message", fill="#adc2d5", font=font)
        d.rounded_rectangle((48, 215, 700, 275), 10, fill="#16293a",
                            outline="#65e3ba" if self.focused else "#436078", width=2)
        d.text((65, 233), self.value or "Click here and type", fill="#e6f1fb", font=font)
        d.rounded_rectangle((48, 310, 220, 370), 10, fill="#65e3ba")
        d.text((83, 329), "Submit", fill="#10262e", font=font)
        if self.submitted:
            d.text((48, 420), "VERIFIED: " + self.value, fill="#65e3ba", font=title)
        d.polygon([(self.x, self.y), (self.x+3, self.y+23), (self.x+9, self.y+15),
                   (self.x+19, self.y+14)], fill="white", outline="#000000")
        return Observation(image, (0, 0), image.size, self.cursor(), json.dumps(self.state()))

    async def request(self, command):
        request, encoded = frame(command)
        # Exercise actual JSON serialization, not direct Python object shortcuts.
        command = json.loads(encoded)
        self.commands.append({k: v for k, v in command.items() if k != "token"})
        op, error = command["op"], None
        if op == "arm":
            output = command.get("output", "auto")
            output = "usb" if output == "auto" and self.usb_ready else "ble" if output == "auto" else output
            if output == "usb" and self.chip == "ESP32-C3":
                error = "unsupported_output"
            elif output not in ("usb", "ble"):
                error = "bad_output"
            elif not (self.usb_ready if output == "usb" else self.ble_ready):
                error = "hid_not_ready"
            else:
                self.active_output, self.armed = output, True
        elif op in ("release", "disarm"):
            if op == "disarm":
                self.armed = False
                self.active_output = "none"
        elif op == "ping":
            pass
        elif not self.armed:
            error = "not_armed"
        elif op == "move":
            self.x = max(0, min(959, self.x + command["dx"]))
            self.y = max(0, min(599, self.y + command["dy"]))
        elif op == "click":
            if command["button"] == 1:
                self.focused = 48 <= self.x <= 700 and 215 <= self.y <= 275
                if 48 <= self.x <= 220 and 310 <= self.y <= 370:
                    self.submitted = bool(self.value)
        elif op == "key" and self.focused:
            if command["mod"] & (1 | 8) and command["keys"] == [4]:
                self.value = ""
            elif command["keys"] == [42]:
                self.value = self.value[:-1]
            elif not command["mod"] & (1 | 4 | 8):
                self.value += "".join(decode(command["mod"], u) for u in command["keys"])
        elif op != "scroll":
            error = "unknown_op"
        reply = {"v": 1, "id": request["id"], "ok": error is None, "hid_ready": True}
        if op in ("ping", "arm"):
            reply.update(chip=self.chip, firmware="0.2.0", outputs=["ble"] if self.chip == "ESP32-C3" else ["ble", "usb"],
                         usb_ready=self.usb_ready, ble_ready=self.ble_ready, active_output=self.active_output,
                         token_configured=True, ble_security="sc_mitm_bonded")
        if error:
            reply["error"] = error
        return check_reply(reply, request["id"])

    async def close(self):
        pass


class DemoPlanner:
    """Deterministic planner over simulator semantics; does not call an LLM."""
    async def plan(self, goal, observation, history):
        s = json.loads(observation.context)
        if s["submitted"] and s["value"] == "Hello ESP32-C3!":
            status, reason, actions = "done", "Observed submitted message in target state", []
        elif s["value"] == "Hello ESP32-C3!":
            status, reason = "act", "Submit the completed form"
            actions = [{"type": "move_to", "x": 130, "y": 340}, {"type": "click"}]
        elif s["focused"]:
            status, reason = "act", "Type the message using HID usage reports"
            actions = [{"type": "type_text", "text": "Hello ESP32-C3!"}]
        else:
            status, reason = "act", "Focus the message input"
            actions = [{"type": "move_to", "x": 180, "y": 245}, {"type": "click"}]
        return Plan.model_validate({"observation_id": observation.id, "status": status,
                                    "reason": reason, "actions": actions})
