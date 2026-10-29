import argparse
import asyncio
import json
import os
import platform
import sys
import uuid
from pathlib import Path

from .config import create_runner


def parser():
    p = argparse.ArgumentParser(description="ESP32-C3 BLE / ESP32-S3 BLE+USB hardware input adapter")
    commands = p.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run a complete simulated task without hardware or API key")
    demo.add_argument("--output", default="runs")
    demo.add_argument("--agent-url", help="use your HTTP Agent with the simulated target instead of the built-in planner")
    run = commands.add_parser("run", help="run a vision agent; default is preview-only")
    run.add_argument("--goal", required=True)
    run.add_argument("--config", default="config.local.toml")
    run.add_argument("--output", default="runs")
    run.add_argument("--execute", action="store_true", help="enable actual hardware inputs")
    serve = commands.add_parser("serve", help="open local dashboard; demo works without configuration")
    serve.add_argument("--config", default="config.local.toml")
    serve.add_argument("--output", default="runs")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--execute", action="store_true")
    commands.add_parser("doctor", help="list serial ports and capture requirements without connecting")
    scan = commands.add_parser("scan", help="discover BLE bridge addresses (does not connect)")
    scan.add_argument("--seconds", type=float, default=5)
    capture = commands.add_parser("observe", help="capture one desktop observation without input injection")
    capture.add_argument("--output", default="runs/observation")
    capture.add_argument("--monitor", type=int, default=1)
    commands.add_parser("schema", help="print the external-agent Plan JSON Schema")
    mcp = commands.add_parser("mcp", help="start the lightweight stdio MCP input adapter")
    mcp.add_argument("--config")
    mcp.add_argument("--demo", action="store_true")
    for name in ("ota", "ota-status", "ota-confirm", "ota-rollback"):
        ota = commands.add_parser(name, help="A/B firmware update and boot management")
        if name == "ota":
            ota.add_argument("--address", required=True, help="bonded BLE device address/UUID")
            ota.set_defaults(port=None)
        else:
            channel = ota.add_mutually_exclusive_group(required=True)
            channel.add_argument("--address", help="bonded BLE device address/UUID")
            channel.add_argument("--port", help="USB serial status/boot management")
        ota.add_argument("--token-env", default="ESP_HID_TOKEN")
        if name == "ota":
            ota.add_argument("--image", required=True)
    for name in ("device", "provision", "pair", "forget-bonds"):
        device = commands.add_parser(name, help={"device": "probe a device", "provision": "provision a token over USB serial",
                                                "pair": "open a 120-second one-time BLE pairing window via USB",
                                                "forget-bonds": "erase BLE bonds via authenticated USB setup"}[name])
        device.add_argument("--port", required=True)
        if name != "device":
            device.add_argument("--token-env", default="ESP_HID_TOKEN")
        if name == "provision":
            device.add_argument("--current-token-env", default="ESP_HID_CURRENT_TOKEN")
    return p


def main():
    args = parser().parse_args()
    if args.command in ("ota", "ota-status", "ota-confirm", "ota-rollback"):
        from .ota import upload, verify_boot
        from .transports import BLETransport, SerialTransport
        token = os.environ.get(args.token_env, "")
        if args.command != "ota-status" and (not 32 <= len(token) <= 96 or any(not 33 <= ord(c) <= 126 for c in token)):
            raise ValueError("token environment variable must contain 32..96 printable non-space ASCII characters")
        factory = (lambda: BLETransport(args.address, timeout=15)) if args.address else (lambda: SerialTransport(args.port, timeout=15))
        async def ota_command():
            transport = factory()
            last_percent = -1
            def progress(sent, total):
                nonlocal last_percent
                percent = sent * 100 // total
                if percent // 5 != last_percent // 5:
                    print(f"OTA {percent}% ({sent}/{total})", file=sys.stderr, flush=True)
                last_percent = percent
            try:
                if args.command == "ota":
                    result = await upload(transport, args.image, token, progress)
                else:
                    op = {"ota-status": "ota_status", "ota-confirm": "ota_confirm", "ota-rollback": "ota_rollback"}[args.command]
                    result = await transport.request({"op": op, "token": token})
            finally:
                await transport.close()
            if args.command in ("ota", "ota-rollback"):
                result["boot"] = await verify_boot(factory, result, token)
            return result
        print(json.dumps(asyncio.run(ota_command()), indent=2))
        return
    if args.command == "mcp":
        from .mcp_server import main as mcp_main
        argv = (["--config", args.config] if args.config else []) + (["--demo"] if args.demo else [])
        return mcp_main(argv)
    if args.command in ("device", "provision", "pair", "forget-bonds"):
        from .device import probe
        from .transports import SerialTransport
        async def device_command():
            transport = SerialTransport(args.port)
            try:
                if args.command != "device":
                    token = os.environ.get(args.token_env, "")
                    if not 32 <= len(token) <= 96 or any(not 33 <= ord(c) <= 126 for c in token):
                        raise ValueError("token environment variable must contain 32..96 printable non-space ASCII characters")
                    if args.command == "provision":
                        await transport.request({"op": "configure", "token": token,
                                                 "current_token": os.environ.get(args.current_token_env, "")})
                    else:
                        reply = await transport.request({"op": "pair" if args.command == "pair" else "forget_bonds", "token": token})
                        # The one-time pairing code is intentionally shown only to the USB operator.
                        return {k: v for k, v in reply.items() if k not in ("id", "v")}
                return (await probe(transport)).model_dump()
            finally:
                await transport.close()
        print(json.dumps(asyncio.run(device_command()), indent=2))
        return
    if args.command == "schema":
        from .agents import strict_schema
        print(json.dumps(strict_schema(), indent=2))
        return
    if args.command == "doctor":
        from serial.tools.list_ports import comports
        screen_allowed = None
        if sys.platform == "darwin":
            import Quartz
            screen_allowed = bool(Quartz.CGPreflightScreenCaptureAccess())
        print(json.dumps({"platform": platform.platform(), "python": platform.python_version(),
                          "screen_capture_allowed": screen_allowed,
                          "serial_ports": [{"device": p.device, "description": p.description} for p in comports()],
                          "notes": ["C3 USB is serial/JTAG only; HID output is BLE",
                                    "macOS: allow Screen Recording for the launching terminal/app",
                                    "Linux Wayland: mss/X11 capture and cursor feedback require X11/XWayland support",
                                    "No port opened; no BLE scan or flash performed"]}, indent=2))
        return
    if args.command == "scan":
        from bleak import BleakScanner
        if not 1 <= args.seconds <= 30:
            raise SystemExit("--seconds must be 1..30")
        devices = asyncio.run(BleakScanner.discover(timeout=args.seconds))
        print(json.dumps([{"name": d.name, "address": d.address} for d in devices], indent=2))
        return
    if args.command == "observe":
        from .observation import DesktopObserver
        dest = Path(args.output)
        dest.mkdir(parents=True, exist_ok=False)
        dest.chmod(0o700)
        obs = DesktopObserver(monitor=args.monitor).capture()
        (dest / "screen.jpg").write_bytes(obs.jpeg())
        (dest / "observation.json").write_text(json.dumps(obs.metadata(), indent=2))
        print(str(dest.resolve()))
        return
    if args.command == "serve":
        import uvicorn
        from .server import create_app
        print(f"Dashboard: http://127.0.0.1:{args.port} (hardware inputs {'enabled' if args.execute else 'disabled'})")
        uvicorn.run(create_app(args.config, args.execute, args.output), host="127.0.0.1", port=args.port)
        return
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    demo = args.command == "demo"
    try:
        runner = create_runner(getattr(args, "config", None), output / uuid.uuid4().hex,
                               execute=getattr(args, "execute", False), demo=demo)
        if demo and args.agent_url:
            from .agents import HTTPPlanner
            runner.planner = HTTPPlanner("http", args.agent_url)
        goal = "Enter Hello ESP32-C3! and submit the form" if demo else args.goal
        result = asyncio.run(runner.run(goal))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result["status"] not in ("verified_demo", "agent_done", "dry_run"):
            sys.exit(1)
    except KeyboardInterrupt:
        print("Stopped; bounded input pulses and firmware lease provide cleanup.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
