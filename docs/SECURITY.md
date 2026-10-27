# 安全边界

token 认证 Agent/MCP → ESP 的命令入口；蓝牙配对认证 ESP → 电脑的链路。
二者不能互相替代。token 不是模型 API Key，也不会放进公共固件或插件。

## 已实现的策略

- 每块板通过物理 USB 设置独立高熵 token，建议 `secrets.token_hex(32)`。
  后续修改须提供已有 token。只有正确 token 能 arm，错误认证有两秒退避。
- 开机默认关闭新配对。物理 USB + token 才能打开 120 秒窗口，显示随机
  六位配对码；每个窗口仅允许一个新连接尝试，成功/失败/断线后关闭。
  配对窗口内不能 arm，未完成认证的连接会超时断开。
- BLE 使用 Secure Connections Only、Passkey Entry、MITM 认证和 128 位链路密钥。
  关闭 Legacy 和 Just Works 降级路径。不静默确认 Numeric Comparison。
  GATT 命令入口和 HID 就绪检查要求加密、认证与绑定同时成立。
- 窗口外的新设备被断开。正常重连须证明持有绑定密钥，单凭复制 MAC 不够。
  最多三个绑定；满额拒绝新配对。构建时修补底层库默认的重复配对处理，
  拒绝“删除旧密钥然后重新配对”。重置绑定只能通过 USB + token 执行。
- 只有一个 HID 接收端，输入不向其他 BLE 连接广播。命令租约绑定控制连接，
  队列消息和去重 ACK 按连接代次隔离，断线后的旧消息不能借新连接执行。
- 输入有 30 秒租约、按键短脉冲和释放流程。超时或投递不确定时停止，不自动重试。

## 需要实际设备验证的内容

目前没有连接开发板；尚未实测各操作系统的配对交互、密钥重连、恶意连接与
射频攻击。原生策略测试和交叉编译证明代码可构建，不能证明实际蓝牙抗攻击能力。

## 不覆盖的威胁

此方案不能阻止射频干扰、所有连接洪泛或未知蓝牙栈漏洞，也不能保护已经被
恶意软件控制的 Agent 主机。token 是本机和固件中的秘密，日志或主机进程泄漏它
仍有风险。标准开发固件没有烧写安全启动或 Flash 加密 eFuse，NVS 不等于加密
存储；具备物理访问权限的人可能读取或替换固件。

电脑仍需拒绝陌生蓝牙键盘的配对。此 ESP 固件无法阻止电脑接受另一台攻击者设备。
仅用有线 S3 时，不需让电脑与其 BLE 配对。

Security reports: open an issue without publishing tokens, pairing codes or private screenshots.

References: [Espressif BLE security tutorial](https://github.com/espressif/esp-idf/blob/master/examples/bluetooth/nimble/bleprph/tutorial/bleprph_walkthrough.md),
[pinned NimBLE server implementation](https://github.com/h2zero/NimBLE-Arduino/blob/2.3.6/src/NimBLEServer.cpp).
