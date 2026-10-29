"""Explicit isolated installation of the packaged wheel and its dependencies."""
import argparse
import importlib.util
import os
import subprocess
import sys
import venv
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runtime", type=Path)
    args = p.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11+ is required")
    plugin = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("bridge_launch", plugin/"server/launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    version = launch.plugin_version()
    root = (args.runtime or launch.runtime_root()).expanduser().resolve()
    wheels = list((plugin/"server").glob(f"espcomputeruse-{version}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Bundled server wheel missing. Build the plugin package first.")
    venv.EnvBuilder(with_pip=True).create(root)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheels[0])], check=True)
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    (root/".ready").write_text(version+"\n", encoding="utf-8")
    print(f"Installed ESP Input Bridge {version} in {root}. Reconnect its MCP server.")


if __name__ == "__main__":
    main()
