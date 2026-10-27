# Privacy

ESP Input Bridge is an open-source local hardware input adapter. The MCP server
does not call a model API, transmit telemetry, or operate a cloud service.

`observe` captures the configured desktop region and may read a configured local
context file. Tool images and state are returned to the agent host you use; that
host's data handling policy applies. `act` sends requested mouse and keyboard
input to the configured ESP device. Do not include secrets in tasks or inputs
unless you intend to share them with your agent host and the target application.

No logs are persisted by the MCP adapter unless `bridge.output_dir` is configured.
If enabled, logs contain screenshots, actions, and state. The adapter does not log
the device token. Local logs, configuration, device tokens, and build caches are
excluded from this repository and plugin packages.

Device tokens are stored locally in ESP NVS and in the controller environment.
The controller can provision or rotate tokens over a physical serial connection.
Standard firmware does not contain a shared default credential.

The optional legacy HTTP Agent demo sends screenshots to the endpoint explicitly
configured by its user. It is separate from the MCP execution path.

Support and questions: https://github.com/jensengao194-spec/esp-input-bridge/issues
