# Validation

## 0.3.1 host optimization — 2026-10-28

- 67 tests passed, including repeated cancellation, pending ACK/close ordering,
  disconnected ACK wake-up without replay, terminal closed transports, notification
  framing, JPEG reuse, STOP-file interruption during planning, and runtime version checks.
- A 30-sample synthetic host benchmark measured observation serialization p50
  5.108 → 0.992ms and immediate local-planner Runner p50 55.094 → 0.454ms.
  These exclude hardware input and screen presentation; see [HOST_OPTIMIZATIONS](HOST_OPTIMIZATIONS.md).
- New host code completed 30 read-only ping transactions on each physical C3 control
  channel: USB p50/p95 2.965/3.148ms, BLE 45.067/46.870ms. Both channels also read
  confirmed firmware 0.3.1 in app0, no active OTA and `active_output=none`.
  This round did not send HID input or change firmware.
- Built plugin 0.3.1 with one matching server wheel. Extracted the ZIP, installed
  its wheel and declared dependencies into a separate local runtime, and passed the
  real stdio MCP observe/act/stop simulated-form flow through the extracted launcher.
  This packaged-plugin check is simulation evidence, not physical input evidence.
- The historical tracked 0.2.0 wheel is byte-identical to HEAD. Historical releases,
  firmware packages and the installed global plugin cache were preserved.

## 0.3.1 local firmware — C3 control latency

- C3 and S3 builds passed; C3 updated through authenticated BLE OTA (681,408 bytes,
  59.497 seconds), booted app0 with expected ELF and was explicitly confirmed.
- 52 tests passed. The new control path still requires a matching protocol ACK when
  ATT write responses are omitted; compatibility with old WRITE-only firmware is retained.
- Real repeated control tests (30 samples per command/channel) completed with no errors:
  USB key-release p50 29.99ms, move p50 14.60ms; BLE key-release p50 44.87ms,
  move p50 44.98ms. Prior values were 58.00/37.91ms and 404.96/390.03ms.
  Full p50/p95 measurements and their scope are in [LATENCY.md](LATENCY.md).
- Actual connection metadata reports 15ms interval / latency 0. A faster unpaced trial
  hit finite TX buffers and stopped; the delivered firmware paces input reports per interval.
- Keyboard Setup Assistant advanced after hardware Z and slash reports; the user
  confirmed the assistant completed. Firmware was disarmed after each test.
- A real BLE mouse movement changed the macOS pointer position and closed-loop
  relative movement restored it to within one logical point of its initial position.
- Application verification resumed on 2026-10-28 after the user foregrounded the
  new blank TextEdit document. Real C3 BLE HID entered 110 characters through USB
  control in 3.260 seconds and 110 through BLE control in 4.965 seconds. The native
  text view matched all 20 expected digit rows; native exact-text selection also
  matched the full 220-character string, including its final newline. AX's displayed
  value trims that last newline. These sequence times are not per-character render latency.
- A real C3 mouse click moved the insertion point from the document end to its first
  line. A hardware `5` report inserted the marker there, and hardware Backspace
  restored the original text. This verifies the click's application outcome.
- For the scroll test only, native accessibility prepared 100 extra fixture rows;
  these rows are not counted as hardware typing. C3 wheel reports through BLE control
  changed the vertical scrollbar from 0 to 0.008503401360544218 and back to 0 with
  opposite wheel directions. The original 220-character document was then restored.
- The foreground guard checked TextEdit before physical input. Final device checks
  showed firmware 0.3.1 in app0, no pending verification or active OTA, and
  `active_output=none`. Application results and screenshots are retained locally in
  `runs/c3-ota-20261027/hid-functional-application-result.json` and sibling screenshots.
  The earlier 180-second foreground wait sent no text; it was superseded by this run.

## 0.3.0 rollback verification

- The new image was observed in `pending_verify=true` at 25.8, 51.8 and 77.8 seconds.
  At the 105.2-second poll it was back in the previous app1 with its original ELF hash
  and `pending_verify=false`, demonstrating the configured 90-second confirmation timeout.
- While pending, real firmware rejected HID arm and a confirmation with a wrong token.
- Manual slot selection followed by host ELF verification and confirmation returned
  the board to the desired image. The later 0.3.1 update was confirmed separately.

## 0.3.0 local firmware — C3 hardware, 2026-10-27

- ESP32-C3 revision v0.4, 4MiB embedded Flash, native USB Serial/JTAG detected.
  Full Flash backed up before initial flashing; the original Flash was entirely `0xff`.
- Initial USB flash hashes verified; NVS token provisioning and firmware handshake succeeded.
- The first real boot exposed the NimBLE 2.3.6 / Arduino 3.3.7 initialization crash.
  Pinning NimBLE 2.3.9 fixed it on this board. The bond-replacement protection remains enabled.
- macOS pairing completed with the USB-issued one-time PIN. Firmware requires encryption,
  MITM authentication, bonding and a 16-byte key before accepting the BLE controller.
  macOS subscribed to keyboard/mouse reports; firmware reports `hid_ready=true`.
- Full authenticated binary BLE OTA transferred 680,800 bytes A → B in 59.329 seconds,
  11,475 bytes/second, then booted the expected ELF and confirmed it.
- A second full BLE OTA transferred a 680,816-byte image B → A in 58.799 seconds,
  11,579 bytes/second. The final image also increases USB RX buffering for complete JSON frames.
  Target-slot/ELF checks passed and `pending_verify=false` after confirmation.
- An unconfirmed-boot test exposed Arduino's default early auto-confirmation. The final
  firmware overrides the weak `verifyRollbackLater()` hook. This 680,832-byte firmware
  transferred over BLE in 60.286 seconds (11,293 bytes/second), booted in app0 and was
  explicitly confirmed by the host. The preceding images were retained as test artifacts.
- C3 and S3 builds passed. S3 was compiled only; no S3 board was connected.
- 51 host/native-policy tests passed, including binary window framing, offsets, window size,
  missing-checkpoint stop without replay, wrong-chip preflight rejection, and compatibility frames.
- The same 32,768 bytes took 8.494 seconds through JSON/Base64 per-chunk ACKs and
  2.803 seconds through the binary window path: 3.03x throughput on the same link.
- Real firmware rejected a bad token, oversized image, wrong session, wrong offset,
  incomplete finish, and full-file SHA-256 mismatch. The running slot stayed selected.
- Native USB serial short commands and ROM flashing passed. Continuous full-image OTA
  through native USB serial encountered ACK timeouts, including after larger buffering
  and smaller-frame probes; it is not exposed by the supported OTA upload CLI.

The firmware uses a separate authenticated binary GATT characteristic, 8-packet windows,
bounded 16-packet queue, negotiated MTU/data length, and a 15ms connection-interval request.
The actual host and peer determine link parameters; throughput is from this Mac and board.

Radio fault injection and actual removal of power during flash writing are not
established by these tests. C3 application-level HID checks are recorded above for 0.3.1;
S3 has compilation evidence only.
No Secure Boot/Flash Encryption or comprehensive attack-resistance claim is made.
The historical public `0.2.0` packages and unrelated local work were preserved.

## Historical 0.2.0 release

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
