"""Package exactly two chip-specific, token-free firmware distributions."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if (ROOT/"firmware/include/secrets.h").exists():
        raise SystemExit("Do not package a public firmware build with a private secrets.h")
    dest = ROOT/"dist/firmware"
    dest.mkdir(parents=True, exist_ok=True)
    core = ROOT/".platformio/packages/framework-arduinoespressif32"
    manifest = {"version": "0.2.0", "hardware_tested": False, "chips": {}}
    for chip, env in [("esp32c3", "esp32c3_usb"), ("esp32s3", "esp32s3_usb")]:
        build = ROOT/f"firmware/.pio/build/{env}"
        table = (build/"partitions.bin").read_bytes()
        entries = [table[i:i+32] for i in range(0,len(table),32) if table[i:i+2]==b"\xaa\x50"]
        assert any(e[2]==0 and int.from_bytes(e[4:8],"little")==0x10000 for e in entries), "app offset mismatch"
        assert any(e[2:4]==b"\x01\x00" and int.from_bytes(e[4:8],"little")==0xe000 for e in entries), "OTA data offset mismatch"
        folder = dest/chip
        folder.mkdir(exist_ok=True)
        files = [(0x0, build/"bootloader.bin"), (0x8000, build/"partitions.bin"),
                 (0xE000, core/"tools/partitions/boot_app0.bin"), (0x10000, build/"firmware.bin")]
        images = []
        for address, source in files:
            data = source.read_bytes()
            if source.name in ("firmware.bin", "bootloader.bin"):
                expected = 5 if chip == "esp32c3" else 9
                assert data[0] == 0xE9 and int.from_bytes(data[12:14], "little") == expected, "wrong chip image"
            shutil.copy2(source, folder/source.name)
            images.append({"offset": hex(address), "file": f"{chip}/{source.name}", "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})
        shutil.copy2(ROOT/"LICENSE", folder/"LICENSE")
        shutil.copy2(ROOT/"THIRD_PARTY.md", folder/"THIRD_PARTY.md")
        manifest["chips"][chip] = {"environment": env, "hid_outputs": ["ble"] if chip == "esp32c3" else ["ble", "usb"],
                                   "ble_security": "sc_mitm_bonded",
                                   "images": images}
    (dest/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    for chip in manifest["chips"]:
        archive_path = ROOT/f"dist/esp-input-bridge-{chip}-0.2.0.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((dest/chip).iterdir()):
                archive.write(path, path.relative_to(dest))
            archive.writestr("manifest.json", json.dumps({**manifest, "chips": {chip: manifest["chips"][chip]}}, indent=2)+"\n")
            archive.writestr("FLASHING.md", f"""# {chip} firmware 0.2.0 (hardware untested)

This archive is for {chip} only, with the standard development-board profile.
Check your board and USB connector. Back up existing data before flashing.
No device is flashed automatically. With esptool installed, an example manual command is:

```
python -m esptool --chip {chip} --port PORT write-flash 0x0 {chip}/bootloader.bin 0x8000 {chip}/partitions.bin 0xe000 {chip}/boot_app0.bin 0x10000 {chip}/firmware.bin
```

After flashing, provision a unique token and open the authenticated pairing window
over physical USB. See README and docs/SECURITY.md in:
https://github.com/jensengao194-spec/esp-input-bridge

The manifest includes offsets, chip, sizes and SHA256. These hashes detect file
corruption; they do not imply Secure Boot or hardware Flash encryption.
""")
        print(archive_path)


if __name__ == "__main__":
    main()
