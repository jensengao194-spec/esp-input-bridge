"""Offline launch only; installation is an explicit separate setup step."""
import json
import os
import sys
from pathlib import Path


def plugin_version():
    return json.loads((Path(__file__).resolve().parents[1]/"plugin.json").read_text(encoding="utf-8"))["version"]


def runtime_root():
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home()/".cache")))
    return Path(os.environ.get("ESP_INPUT_RUNTIME", str(base/"esp-input-bridge"/plugin_version())))


def main():
    root = runtime_root()
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ready = root/".ready"
    if not python.is_file() or not ready.is_file() or ready.read_text(encoding="utf-8").strip() != plugin_version():
        setup = Path(__file__).resolve().parents[1]/"scripts/setup.py"
        print(f'ESP Input Bridge runtime missing. Run: python3 "{setup}"', file=sys.stderr)
        raise SystemExit(1)
    # No shell, no automatic downloads, and no credentials on the command line.
    os.execv(str(python), [str(python), "-m", "espcomputeruse.mcp_server", *sys.argv[1:]])


if __name__ == "__main__":
    main()
