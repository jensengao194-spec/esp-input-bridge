import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def launch():
    path = Path(__file__).resolve().parents[1]/"plugins/esp-input-bridge/server/launch.py"
    spec = importlib.util.spec_from_file_location("bridge_launch_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_is_separated_by_manifest_version(launch, monkeypatch, tmp_path):
    monkeypatch.delenv("ESP_INPUT_RUNTIME", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert launch.runtime_root() == tmp_path/"esp-input-bridge"/launch.plugin_version()
    monkeypatch.setenv("ESP_INPUT_RUNTIME", str(tmp_path/"custom"))
    assert launch.runtime_root() == tmp_path/"custom"


@pytest.mark.parametrize("ready", [None, "old-version"])
def test_launcher_rejects_missing_or_mismatched_runtime(launch, monkeypatch, tmp_path, ready):
    python = tmp_path/("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.touch()
    if ready:
        (tmp_path/".ready").write_text(ready+"\n")
    monkeypatch.setenv("ESP_INPUT_RUNTIME", str(tmp_path))
    calls = []
    monkeypatch.setattr(launch.os, "execv", lambda *args: calls.append(args))
    with pytest.raises(SystemExit) as error:
        launch.main()
    assert error.value.code == 1 and calls == []


def test_launcher_uses_matching_runtime_offline(launch, monkeypatch, tmp_path):
    python = tmp_path/("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.touch()
    (tmp_path/".ready").write_text(launch.plugin_version()+"\n")
    monkeypatch.setenv("ESP_INPUT_RUNTIME", str(tmp_path))
    monkeypatch.setattr(launch.sys, "argv", ["launch.py", "--demo"])
    calls = []
    monkeypatch.setattr(launch.os, "execv", lambda *args: calls.append(args))
    launch.main()
    assert calls == [(str(python), [str(python), "-m", "espcomputeruse.mcp_server", "--demo"])]
