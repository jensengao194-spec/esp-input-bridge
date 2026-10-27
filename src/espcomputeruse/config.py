import os
import tomllib
from pathlib import Path

from .agents import HTTPPlanner
from .observation import DesktopObserver
from .runner import Runner
from .simulator import DemoPlanner, SimulatedTarget
from .transports import BLETransport, SerialTransport


class NoTransport:
    async def request(self, command):
        raise RuntimeError("hardware transport is disabled in preview mode")

    async def close(self):
        pass


def create_runner(config_path, run_dir: Path, execute=False, demo=False):
    if demo:
        target = SimulatedTarget()
        return Runner(target, DemoPlanner(), target, run_dir, execute=True, settle_ms=100)
    path = Path(config_path).resolve()
    with path.open("rb") as f:
        config = tomllib.load(f)
    a, o, t, r = (config.get(k, {}) for k in ("agent", "observation", "transport", "runner"))
    if o.get("kind", "desktop") != "desktop":
        raise ValueError("use the demo command for simulated observations")
    if t.get("kind", "serial") not in ("serial", "ble"):
        raise ValueError("transport.kind must be serial or ble")
    token = os.environ.get(t.get("token_env", "ESP_HID_TOKEN"), "")
    if execute and len(token) < 32:
        raise ValueError("set ESP_HID_TOKEN (or token_env) to at least 32 random characters before hardware execution")
    context_file = o.get("context_file")
    if context_file:
        context_file = path.parent / context_file
    observer = DesktopObserver(monitor=o.get("monitor", 1), region=o.get("region"),
                               max_width=o.get("max_width", 1280), context_file=context_file)
    planner = HTTPPlanner(kind=a.get("kind", "responses"), base_url=a.get("base_url", "https://api.openai.com/v1"),
                          model=a.get("model", ""), api_key=os.environ.get(a.get("api_key_env", "OPENAI_API_KEY"), ""),
                          timeout=a.get("timeout", 90), strict=a.get("strict", True))
    # No serial port or Bluetooth connection is opened in preview mode.
    if not execute:
        transport = NoTransport()
    elif t.get("kind", "serial") == "serial":
        transport = SerialTransport(t["port"], t.get("baudrate", 115200), t.get("timeout", 3))
    else:
        transport = BLETransport(t["address"], t.get("timeout", 3))
    return Runner(observer, planner, transport, run_dir, execute, token,
                  r.get("max_steps", 20), r.get("max_age", 120), r.get("settle_ms", 200), t.get("output", "auto"))
