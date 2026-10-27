"""Build a portable, schema-validated plugin ZIP with the current server wheel."""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT/"plugins/esp-input-bridge"


def validate():
    for name in ("plugin", "mcp"):
        schema = json.loads((ROOT/f"docs/schemas/{name}.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(json.loads((PLUGIN/f"{name}.json").read_text()))
    manifest = json.loads((PLUGIN/"plugin.json").read_text())
    interface = manifest["extensions"]["com.openai"]["interface"]
    assert len(interface["displayName"]) <= 30 and len(interface["shortDescription"]) <= 30
    for key in ("composerIcon", "logo"):
        assert interface[key].startswith("./") and (PLUGIN/interface[key]).is_file()
    assert not (PLUGIN/"hooks").exists(), "public plugin must not contain lifecycle hooks"


def main():
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(ROOT/"dist")], cwd=ROOT, check=True)
    wheel = ROOT/"dist/espcomputeruse-0.2.0-py3-none-any.whl"
    shutil.copy2(wheel, PLUGIN/"server"/wheel.name)
    shutil.copy2(ROOT/"bridge.example.toml", PLUGIN/"bridge.example.toml")
    shutil.copy2(ROOT/"LICENSE", PLUGIN/"LICENSE")
    shutil.copy2(ROOT/"THIRD_PARTY.md", PLUGIN/"THIRD_PARTY.md")
    validate()
    dest = ROOT/"dist/esp-input-bridge-plugin-0.2.0.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(PLUGIN.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
                archive.write(file, file.relative_to(PLUGIN))
    print(dest)


if __name__ == "__main__":
    main()
