# Validation — 0.2.0

Date: 2026-10-27 (Asia/Shanghai). Build host: Apple Silicon macOS, Python 3.13.

## Executed

- 45 tests passed, including a real stdio MCP client/server session that
  lists exactly three tools and completes the simulated form task. Images return
  as ImageContent, not Base64 text. No external model is involved.
- C3 esp32c3_usb and S3 esp32s3_usb both cross-compiled with provisioning,
  capability handshake, BLE HID and S3 native USB HID.
- The original simulator and actual local HTTP Agent example each completed a
  four-observation form task. This is orchestration evidence, not model quality.
- Local browser dashboard start, completion and stop paths were exercised.
- Native C++ policy tests cover closed pairing, single candidate, expiry, millis
  wrap, and refusal of unauthenticated/unbonded/short-key links. The host rejects
  firmware lacking the secure BLE capability marker.
- Portable Agent Plugins 1.0 manifest schema validation passed. A separately
  installed wheel completed the form through the packaged stdio launcher.
- Codex CLI registered the marketplace and installed the plugin successfully.
  The existing configuration parsed correctly; only this plugin and marketplace
  keys changed. A private backup was retained locally.

Local logs under ignored runs/ are not published: real runs may contain screenshots,
input content and environment paths. Reproduce with README commands and GitHub CI.

## Not established

- No board was connected or flashed. Pairing, USB enumeration, Bluetooth dual
  connections, physical HID delivery and target application behavior are untested.
- Screen Recording permission has not been granted to this launching process;
  actual desktop capture was not performed. Simulator images are drawn locally.
- No real vision model request was used in the MCP path. The adapter never calls
  a model: decisions belong to the caller.
- Public directory approval is separate from a Git-backed plugin marketplace.
  Local stdio submissions require the current OpenAI partner eligibility process.
  The actual upload portal currently requires verified developer identity before
  accepting a package. No public-directory listing or approval is claimed.
- No claim of bypassing software automation detection is made.
