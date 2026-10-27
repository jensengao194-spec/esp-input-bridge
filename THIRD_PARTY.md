# Third-party components

This repository's original code is MIT licensed. Dependencies are obtained from
their upstream publishers; their licenses are not replaced by this project's MIT
license. The build uses:

- [Arduino-ESP32 3.3.7](https://github.com/espressif/arduino-esp32/tree/3.3.7),
  LGPL-2.1-or-later for the Arduino core, with additional licenses in the upstream
  tree and ESP-IDF components. Source, build configuration, and exact version
  references are provided so firmware can be rebuilt and modified.
- [ESP-IDF](https://github.com/espressif/esp-idf), Apache-2.0 and upstream component
  licenses, supplied through the pinned Arduino framework.
- [NimBLE-Arduino 2.3.6](https://github.com/h2zero/NimBLE-Arduino/tree/2.3.6),
  Apache-2.0, with upstream NimBLE/TinyCrypt notices preserved by the dependency.
  The build middleware in `firmware/scripts/secure_nimble.py` modifies its repeat
  pairing handler to preserve existing keys instead of silently replacing them.
  Legacy pairing is disabled and LE Secure Connections Only is enabled.
- [ArduinoJson 7.4.2](https://github.com/bblanchon/ArduinoJson/tree/v7.4.2), MIT.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), MIT.
- Python dependencies and their exact installed license metadata are included in
  dependency distributions. The plugin's setup script installs them into an
  isolated environment; it does not copy their source into this repository.

Firmware binary distribution must retain this notice and the source/version
references. No compiled vendor library is relicensed as original project code.
