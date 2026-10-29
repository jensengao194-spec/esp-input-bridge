# C3 A/B 与蓝牙 OTA（0.3.0）

4MiB ESP32-C3 的首次安装通过 USB 完成；后续应用更新可通过加密蓝牙传输。
首次改变分区表前，应备份整片 Flash。公开固件不含设备 token。

| 分区 | 地址 | 大小 |
| --- | --- | --- |
| NVS | `0x9000` | `0x5000` |
| OTA 启动信息 | `0xe000` | `0x2000` |
| A / app0 | `0x10000` | `0x1e0000`，1.875MiB |
| B / app1 | `0x1f0000` | `0x1e0000`，1.875MiB |
| 预留存储 | `0x3d0000` | `0x20000` |
| Coredump | `0x3f0000` | `0x10000` |

分区表为 `firmware/partitions_c3_ab.csv`。NVS 和 OTA 数据保持标准地址，
但应用分区边界改变，不能直接对历史布局做应用 OTA 来迁移分区表。

## 使用

先通过物理串口 `provision` 配置 token，再使用 `pair` 打开 120 秒单次配对窗口。
连接受保护的控制特征会触发系统验证码输入。macOS 将已知设备标识为 UUID；
主机可从 CoreBluetooth 取回已知设备，支持系统继续持有 HID 连接的情况。
主机依赖 Bleak 2.1 或更高的 2.x 版本。

```bash
# ESP_HID_TOKEN 环境变量应为该板已配置的私密 token。
.venv/bin/esp-use ota-status --address DEVICE_UUID
.venv/bin/esp-use ota --address DEVICE_UUID --image firmware/.pio/build/esp32c3_usb/firmware.bin

# 串口可读取状态；需要有线恢复时使用 ROM/esptool 按包内偏移烧录。
.venv/bin/esp-use ota-status --port /dev/cu.usbmodem_REPLACE

# 切回另一有效槽，重启后校验目标镜像并确认启动。
.venv/bin/esp-use ota-rollback --address DEVICE_UUID
```

仅发送 `firmware.bin` 应用镜像。主机检查芯片、应用描述符、大小和 ELF 身份；
整片合并镜像、其他芯片固件和 ZIP 均不能作为 OTA 应用。
本次发现 native USB 串口连续传输完整 OTA 镜像存在偶发 ACK 超时，
因此 `ota` 命令仅开放已完成整包验证的 BLE 通道。USB ROM 烧录和短命令已验证。
成功输出包含目标槽、SHA-256、传输模式、实际秒数、字节/秒及确认后的启动状态。

## 高速数据通道

控制使用原有 JSON characteristic。`ota_begin(token,size,sha256)` 返回随机
128 位 session nonce、目标槽、`binary_window=8`。
`ota_status.ota_binary=true` 且主机发现高速特征时自动使用二进制传输。

数据 characteristic UUID 为 `c3a00004-7e45-4f9b-9d83-9bb45a2c1000`，
要求加密、MITM、绑定和 16 字节密钥。每包为：

| 字节 | 内容 |
| --- | --- |
| 0..15 | `ota_begin` session 的 16 字节原始值 |
| 16..19 | 固件字节偏移，uint32 little endian |
| 20..末尾 | 原始固件，不进行 Base64 编码 |

数据包大小受 MTU 和主机免响应写入上限约束，固件上限 512 字节。
固件请求 MTU 247、251 字节 Data Length 和 15ms / latency 0 连接参数，
最终值由对端协商决定。每 8 包发送一次 JSON `ota_sync(session,offset)`，
固件处理完前序数据后返回 `received`。16 包有界队列只在 BLE 回调中入队，
Flash 写入在主循环进行，队列溢出、错序或偏移不符会中止会话。

小 MTU 或旧固件可使用 `ota_chunk(session,offset,data)` 的 Base64 兼容路径，
每个解码块最多 240 字节，逐包 ACK。新固件通过 Service Changed 通知刷新绑定端的 GATT 缓存。

## 完整性与恢复

升级前必须 disarm；写入期间禁止 arm、配置和配对。只写非运行槽。
会话绑定 BLE connection handle、连接代次及随机 nonce，重连不能续写旧会话。
断线或 30 秒无活动中止写入；macOS 可能仍为 HID 保留物理连接，此时按超时清理。
传输失败不自动重放，重新升级时从新的会话重新开始。

`ota_finish(session)` 要求大小和整个应用文件的 SHA-256 都匹配，再由 ESP-IDF
验证镜像格式、芯片和镜像内部完整性。只有成功后才修改启动槽，ACK 后延迟重启。
Bootloader 必须启用 `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`；当前 SDK 已启用。
固件覆盖 Arduino 的 `verifyRollbackLater()` 弱钩子，阻止框架在 `setup()` 前自动确认。
主机重连后核对槽位和 ELF SHA-256，再以 token 执行 `ota_confirm`。
新镜像 90 秒未确认则回滚；确认前再次复位也由 bootloader 回滚。
确认前 HID 无法 arm。`ota_abort(session)` 可主动终止写入。

SHA-256 验证传输完整性，不能替代签名、Secure Boot 或 Flash Encryption。
这里使用已绑定的加密 BLE 链路及独立设备 token 授权更新，不宣称已启用上述硬件安全功能。

## 实板记录

2026-10-27：已连接 ESP32-C3 revision v0.4 / 4MiB Flash / USB Serial-JTAG。
烧录前保存整片 Flash，原 Flash 全为 `0xff`；固件已启动并完成 macOS 加密绑定。
传输、换槽和回滚的最终结果记录在 `docs/VALIDATION.md`。
