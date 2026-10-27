# ESP32-C3 / ESP32-S3 命令协议 v1

USB 串口为 115200 8N1；原生 C3 USB 的 CDC 使用相同配置入口。每条命令是一行 UTF-8 JSON，以 LF 结束，正文最多 511 字节。BLE 使用同一个格式，按不超过 20 字节拆包；分片由换行结束，无需大 MTU。分片超时 2 秒或溢出后丢弃到下一个换行。

| GATT | UUID |
| --- | --- |
| Service | `c3a00001-7e45-4f9b-9d83-9bb45a2c1000` |
| Command (authenticated WRITE with response) | `c3a00002-7e45-4f9b-9d83-9bb45a2c1000` |
| Reply (NOTIFY / authenticated READ) | `c3a00003-7e45-4f9b-9d83-9bb45a2c1000` |

设备广告公布 HID 服务 `0x1812` 和名称，控制服务在连接后的 GATT 服务发现中可见。BLE 断线后不自动重连重发。一次只允许一个在途请求，主机逐条等待 ACK。

```json
{"v":1,"id":"b69b394ad4ef46d18786e201e225959ec","op":"move","dx":20,"dy":-5}
{"v":1,"id":"b69b394ad4ef46d18786e201e225959ec","ok":true,"hid_ready":true}
```

每条请求必须携带 `v=1`、32 位十六进制随机 `id`、`op`。失败 ACK 的 `ok=false`，另有 `error`。非法 JSON、无效 ID、缓冲/队列溢出直接丢弃，不执行；主机因 ACK 超时停止。

| op | 参数 | 行为 |
| --- | --- | --- |
| ping | 无 | 返回芯片、固件版本、输出能力和就绪状态，不产生输入 |
| configure | token 字符串；已有 token 时需 current_token | 仅物理 USB/串口且未 arm 时可写入 NVS；不支持 BLE 配置 |
| pair | token | 仅物理 USB/串口且未 arm；打开 120 秒单次配对窗口，ACK 的 passkey 为六位字符串，expires_in_seconds 为 120 |
| forget_bonds | token | 仅物理 USB/串口且未 arm；断开 BLE、清除全部绑定，关闭窗口 |
| arm | token；output 为 auto / ble / usb（默认 auto） | token 正确且指定 HID 就绪时持有 30 秒租约 |
| disarm | 无 | 撤销租约并发送空键鼠报告 |
| release | 无 | 发送空报告，不撤销租约 |
| move | dx, dy 整数 -127..127 | 相对移动，隐含 buttons=0 |
| click | button: 1 / 2 / 4 | 左 / 右 / 中键，35ms 后释放 |
| scroll | steps 整数 -20..20 | 垂直滚轮，正值向上 |
| key | mod: 0..255, keys: 0..6 个 usage 4..101 | 35ms 后释放，随后间隔 20ms |

键盘 mod 是标准位掩码：左 CTRL=1、SHIFT=2、ALT=4、GUI=8，右侧修饰键依次为 16/32/64/128。高层主机默认使用左侧修饰键。键盘报告为 8 字节：modifier、reserved、6 usages。鼠标报告为 4 字节：buttons、signed X、signed Y、signed wheel。BLE 每种输入报告独立 characteristic，通过 Report Reference 指明 report ID；报告数据不再重复添加 ID。

BLE HID 目标必须订阅键盘 report 1 与鼠标 report 2，且连接已加密、MITM 身份认证、绑定成功、密钥长度 16 字节；输入报告只发给该连接，不广播到控制端。使用 LE Secure Connections Only，关闭 legacy pairing。S3 的 USB HID 用相同 report map，经 native USB OTG 输出。C3 不支持 USB HID。USB/串口与 BLE 控制器共享单租约；不同 BLE 连接的分片有独立缓冲，队列消息和缓存 ACK 带有连接代次，不能跨断线复用。

ping / arm ACK 除基础字段外，还包含 `chip`（ESP32-C3 / ESP32-S3）、`firmware`、`outputs`（C3 为 ble，S3 为 ble 和 usb）、`ble_ready`、`usb_ready`、`active_output`、`token_configured`。S3 的 auto 优先使用已就绪的 USB；arm 后绑定该输出，断线停止，不在批次中切换目标。

configure 的 token 为 32..96 个非空格可打印 ASCII 字符，首次可直接设置，后续轮换必须提供已有 current_token。公开发布固件不含共享 token，须配置后才能 arm。NVS 持久化并不等于硬件加密存储。

ping / arm 还返回 `ble_security="sc_mitm_bonded"`、`pairing_open` 和 `bond_count`。
主机拒绝缺失此安全策略标记的旧固件。配对码只在 USB `pair` 的 ACK 中显示，
不经 ping 或 BLE 返回；配对窗口默认关闭，不能通过 BLE 打开或清除绑定。

固件内存保存最近 32 个 ID 与原始帧 fingerprint/ACK。同 ID 同帧返回已缓存 ACK，同 ID 不同帧拒绝 `id_conflict`。此机制只适用于当前启动、最近 32 条请求；不是跨重启的 exactly-once 保证。主机不会自动重试有副作用的操作，ACK 超时视为未知状态。

鼠标定位、图像缩放映射、文本拆成 usage、等待与批量动作属于主机层；固件不执行任意脚本、系统命令或字符串输入。
