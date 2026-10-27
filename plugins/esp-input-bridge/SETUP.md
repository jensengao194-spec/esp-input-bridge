# Setup

Requires a local desktop task and Python 3.11+ available as `python3`. For a fresh
plugin install run `python3 "${PLUGIN_ROOT}/scripts/setup.py"`, then reconnect the
plugin's MCP server. Installation downloads the Python wheel's declared dependencies
from the configured Python package index into an isolated user-cache venv. The MCP
launcher itself does not download or install anything. `ESP_INPUT_RUNTIME` can
select a different runtime directory.

Without configuration the server runs an isolated demo target. Use `observe`,
`act`, and `stop` to test it without a board or API key.

For hardware use, copy `bridge.example.toml` in this package to
`~/.config/esp-input-bridge/config.toml` (or set `ESP_INPUT_CONFIG`), and set
`bridge.mode="hardware"`. Choose a serial/BLE command channel and `output`:
`ble` on C3; `ble`, `usb`, or `auto` on S3. A standard release firmware must first
be provisioned with a unique token over physical serial using `esp-use provision`.
Set the same token through `ESP_HID_TOKEN` in the MCP server's launch environment.
Do not put tokens in the plugin package. Do not change an existing host config
without preserving unrelated settings.

BLE pairing is closed by default. After provisioning, use `esp-use pair --port PORT`
over physical USB with the token in `ESP_HID_TOKEN`. Enter the displayed one-time
six-digit code on the target computer within 120 seconds. One new connection
attempt closes the window after success/failure. Bound peers reconnect using their
authenticated keys. To clear lost bonds, use authenticated USB `esp-use forget-bonds`.

`esp-use` is inside the setup-created virtual environment, not added to your PATH.
On macOS/Linux invoke `${ESP_INPUT_RUNTIME}/bin/esp-use`; the default runtime is
`${XDG_CACHE_HOME:-$HOME/.cache}/esp-input-bridge/0.2.0`. On Windows use its
`Scripts/esp-use.exe`. Keep the token in the process environment, not command arguments.

macOS needs Screen Recording permission for the launching app/terminal and
Bluetooth permission when using BLE. Linux capture requires X11; Wayland portal
capture is not integrated. C3 native USB is a serial command channel only. S3
USB HID requires its native USB OTG connector. Firmware binary files are specific
to each chip and board profile; never cross-flash C3 and S3.

Hardware operation has not been physically validated in this release. A simulated
demo, compiler success, and application response are distinct evidence.

Source, firmware build steps, token provisioning, license and support:
https://github.com/jensengao194-spec/esp-input-bridge
