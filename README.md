# ESP Input Bridge

**用 ESP32 硬件键鼠替换 Agent 的软件输入后端。** Agent 沿用原有的观察和决策流程，本项目负责动作转换、设备传输与 HID 执行，MCP 路径不调用第二个模型。

An MIT-licensed hardware input adapter: ESP32-C3 BLE HID, ESP32-S3 BLE/USB HID, and a lightweight local MCP plugin for existing agents.

## 两个固件

| 固件 | 蓝牙键鼠 | 有线 USB 键鼠 | 命令接收 |
| --- | --- | --- | --- |
| ESP32-C3 | 支持 | 不支持 | USB 串口 / BLE GATT |
| ESP32-S3 | 支持 | 支持 | USB CDC 串口 / BLE GATT |

共用源码和协议，分别生成芯片对应的二进制。C3 是 RISC-V，S3 是 Xtensa，不能交叉烧录。插件通过 `ping` 握手识别型号和能力。S3 的 `output=auto` 优先使用已就绪的 USB，否则使用 BLE；批次中断线不会切换到另一个输出。

S3 必须连接开发板的**原生 USB OTG 口**。C3 原生 USB 仅作串口/JTAG，不能提供 USB HID：[乐鑫说明](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/usb-serial-jtag-console.html)。

**当前未进行物理开发板验证，也不保证绕过软件的自动化检测。** 编译、模拟、硬件与应用结果分别记录。

## 安装主机端

Python 3.11+：

```bash
git clone https://github.com/jensengao194-spec/esp-input-bridge.git
cd esp-input-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/esp-use mcp --demo
```

最后一条启动 stdio MCP 服务，应由 MCP 客户端连接。可视演示可直接运行：

```bash
.venv/bin/esp-use demo
.venv/bin/esp-use serve
```

控制台为 `http://127.0.0.1:8765`。演示在模拟表单里输入 `Hello ESP32-C3!` 并提交，不产生系统键鼠输入。

## 安装插件市场

本仓库含 `.agents/plugins/marketplace.json`：

```bash
codex plugin marketplace add jensengao194-spec/esp-input-bridge --ref main
codex plugin add esp-input-bridge@esp-input-bridge
```

也可在桌面插件目录中添加此仓库市场。插件带有服务器 wheel、图标、配置示例与使用技能。首次按照插件 SETUP 运行 `scripts/setup.py`，在独立用户缓存虚拟环境中安装声明的依赖，再重新连接 MCP。启动器本身不自动联网安装。需要本地桌面任务和可用的 `python3`。

GitHub 插件市场与 OpenAI 公共目录是不同渠道。官方公共目录的本地 MCP 提交目前需要合作方白名单、身份验证及审核；本仓库市场可独立分发，不代表已通过公共目录审核。见[官方要求](https://developers.openai.com/plugins/deploy/desktop-only)。

## MCP：三个工具

| 工具 | 行为 |
| --- | --- |
| `observe(image=true)` | 返回新 observation ID、简短状态及可选图片 |
| `act(observation_id, actions, image=false)` | 执行最多 8 个动作，返回下一次观察状态 |
| `stop()` | 停止会话并尝试释放输入，重启服务后才能再次输入 |

动作：`move_to`、`move`、`click`、`scroll`、`key`、`type_text`、`wait`。一次点击示例：

```json
[
  {"type":"move_to","x":180,"y":245},
  {"type":"click","button":"left","count":1}
]
```

坐标相对于观察图片，区域原点与缩放由本地映射。鼠标位置修正、拆键码、分片和 ACK 全在本地，不让模型逐包调用。`type_text` 支持 US ASCII；中文需显式 IME 按键流程。

`act` 默认只返回状态，需要视觉决策时再请求图像。图片以原生 MCP ImageContent 返回，不把 Base64 塞进普通文本。MCP 不保存日志，除非设置 `bridge.output_dir`。

## 配置硬件

无配置时默认是明确标记的 demo 模式。复制 `bridge.example.toml` 到 `~/.config/esp-input-bridge/config.toml`，或通过 `ESP_INPUT_CONFIG` 指定文件。不需要模型 API Key。

```toml
[bridge]
mode = "hardware" # demo / preview / hardware

[observation]
monitor = 1
max_width = 1280

[transport]
kind = "serial" # serial / ble
port = "/dev/cu.usbmodem_REPLACE" # Windows: COMx; Linux: /dev/ttyACMx
output = "auto" # auto / ble / usb
token_env = "ESP_HID_TOKEN"
```

BLE 控制通道用 `address` 替代 `port`，可用 `.venv/bin/esp-use scan` 发现地址。命令输入和 HID 输出是独立通道：通过 USB 控制 C3，它仍以蓝牙键鼠输出。

公开固件不含共享 token，首次通过物理串口设置：

```bash
export ESP_HID_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
.venv/bin/esp-use provision --port /dev/cu.usbmodem_REPLACE
.venv/bin/esp-use device --port /dev/cu.usbmodem_REPLACE
```

保存 token，并在 MCP 服务进程环境中提供相同 `ESP_HID_TOKEN`。凭证保存在板上 NVS。修改已有凭证还需设置 `ESP_HID_CURRENT_TOKEN`；不能通过 BLE 配置或重置。凭证不能放进公开仓库或插件包。

观察进程必须运行在接收键鼠输入的电脑上。macOS 需给启动程序屏幕录制权限，BLE 另需蓝牙权限；Linux 目前要求 X11。`.venv/bin/esp-use doctor` 只检查环境，不连接硬件。

## 编译与打包

将 PlatformIO 与 MCP 依赖环境分开：

```bash
python3 -m venv .pio-venv
.pio-venv/bin/python -m pip install -r firmware/requirements.txt
PLATFORMIO_CORE_DIR="$PWD/.platformio" .pio-venv/bin/pio run -d firmware -e esp32c3_usb -e esp32s3_usb
.venv/bin/python scripts/package_firmware.py
.venv/bin/pytest -q
.venv/bin/python scripts/build_plugin.py
```

固定 pioarduino `55.03.37`、Arduino-ESP32 `3.3.7`、NimBLE `2.3.6`、ArduinoJson `7.4.2`。默认 C3 DevKitM-1 与 S3 DevKitC-1 板型；其他板型需核对 Flash 和 USB 接法。可选 `esp32c3_uart` / `esp32s3_uart` 环境供 USB-UART 控制口使用。

`dist/` 含 C3、S3 两个固件 ZIP、镜像偏移与 SHA-256 manifest，以及 `esp-input-bridge-plugin-0.2.0.zip`。`firmware.bin` 是应用镜像，不能单独当作地址 0 的完整 Flash 镜像；首次烧录需按 manifest 使用 bootloader、分区表、boot_app0 与应用镜像。镜像分段烧录不覆盖 NVS，禁止跨芯片烧录。

插件 ZIP 带服务器 wheel。首次 setup 从用户配置的 Python 包索引安装声明的依赖，不修改系统 Python。官方目录的资格与审核独立于本地打包成功。

停止按钮、Ctrl+C、MCP stop、或日志目录里的 STOP 文件会停止输入并尝试释放。设备使用有界按键/点击脉冲、30 秒租约，未知投递不自动重发。链路失效时无法保证释放报告送达；ACK 不代表软件完成操作，仍应观察结果。

## 文档与开源

- [协议](docs/PROTOCOL.md) · [验证边界](docs/VALIDATION.md)
- [隐私](docs/PRIVACY.md) · [条款](docs/TERMS.md) · [第三方组件](THIRD_PARTY.md)
- [MIT License](LICENSE)

旧的 Responses / Chat Completions / 自定义 HTTP 规划演示仍可用 `run` 命令和 `config.example.toml` 运行，它不是插件的核心执行路径。
