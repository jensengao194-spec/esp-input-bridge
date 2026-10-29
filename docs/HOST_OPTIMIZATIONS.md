# 主机端优化与验证

2026-10-28，主机端及插件 0.3.1。板上固件仍为已确认的 0.3.1，本轮没有改动固件。

## 主机开销

同一台 Apple Silicon Mac、Python 3.13.15，使用同一脚本在改动前后各测 30 次，
每项预热 3 次。图像是 1280×720 的模拟表单，未采集真实屏幕，也不产生硬件输入。

| 项目 | 改动前 p50 / p95 | 改动后 p50 / p95 |
| --- | --- | --- |
| 同一观察的 JPEG 保存、元数据、规划 payload、图像服务序列化 | 5.108 / 5.231ms | 0.992 / 1.050ms |
| 立即返回的本地规划器完整 Runner 流程 | 55.094 / 56.128ms | 0.454 / 0.541ms |

观察序列化原先会重复压缩同一幅图像，现在每个观察只编码一次并复用哈希及 Base64。
观察图像应作为只读快照使用；图像改变时创建新观察。规划等待改为由任务完成或停止事件
唤醒，保留每 50ms 检查 STOP 文件，避免给已完成的规划结果增加固定等待。

这些数值仅反映合成场景的主机开销，不代表模型推理、BLE HID 或屏幕呈现速度。
复测命令：

```bash
.venv/bin/python scripts/benchmark_host.py --iterations 30 --output runs/host-benchmark.json
```

本机前后原始结果分别保留在 `runs/host-optimization-20261028/before.json` 和 `after.json`。

## 传输与停止

- 蓝牙仅保留一个当前请求的 Future，按随机 ID 接收 ACK；旧回复、重复回复和无关
  回复不会积累在无界队列中。511 字节接收上限及任意分片处理保持有效。
- 蓝牙断连回调立即结束 ACK 等待，不自动重连或重发原输入。
- 多次取消也必须等待正在传输的帧结束，再由上层尝试 disarm。串口工作线程不会因
  第二次取消而被遗留在后台、与清理命令交错读写。
- 串口和蓝牙关闭操作与请求共用锁；关闭等待当前事务结束，关闭后拒绝新请求。
- 回归测试覆盖重复取消、取消时事务失败、关闭与 ACK 并发、关闭后拒绝连接、
  断连唤醒、旧/重复/溢出回复、规划期间 STOP 文件，以及插件运行环境版本不匹配。

## C3 实物检查

通过新主机代码，USB、BLE 各完成 30 次保持连接的只读 ping：

| 控制通道 | p50 | p95 |
| --- | --- | --- |
| USB | 2.965ms | 3.148ms |
| BLE | 45.067ms | 46.870ms |

两条通道均成功查询 OTA 状态：0.3.1 / app0，`pending_verify=false`、`ota_active=false`，
`active_output=none`。本轮没有发送键鼠输入；既有真实应用输入结果见 [VALIDATION](VALIDATION.md)，
固件控制往返测量见 [LATENCY](LATENCY.md)。实物记录为本机 `runs/host-optimization-20261028/device.json`。

## 插件分发

构建器从 `pyproject.toml` 读取版本并核对 `plugin.json`，安装脚本、wheel 选择和缓存
目录均跟随 manifest 版本。启动器拒绝旧版本 `.ready`，避免新插件静默运行旧主机代码。
历史 0.2.0 wheel 与发布包保留，新包只携带当前版本 wheel。更新已安装插件后，
需要执行其 setup 并重新连接 MCP。

本轮 67 项测试通过。已从 `dist/esp-input-bridge-plugin-0.3.1.zip` 解包，在单独的
本地虚拟环境安装包内 wheel 和声明的依赖，`pip check` 无冲突；通过解包后的启动器
执行真实 stdio MCP 模拟表单流程，验证三个工具、图像返回、输入结果和停止。
验证工具也支持选择解包路径：

```bash
.venv/bin/python scripts/verify_plugin.py --plugin PATH_TO_EXTRACTED_PLUGIN --runtime PATH_TO_RUNTIME
```

本轮未更新全局已安装插件缓存；新的主机代码已包含在上述插件包与同版本 wheel 中。
