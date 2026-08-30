# G1 Camera 一键调整与运维平台接入方案

## 目标

相机正常安装和开机流程保持不变。开机自动 AWB 锁定效果不好时，用户在运维平台点击一次“调整头部相机”，系统执行：

1. 确认 teleop 正在运行并持续取流；
2. 开启头部 AWB；
3. 等待 AWB 收敛；
4. 关闭 AWB；
5. 应用现有 `camera-controls-state.json` 中的头部参数；
6. 返回成功、失败和执行日志。

该操作不重新调参、不覆盖 JSON、不处理左右腕、不重启 teleop。

## 当前立即可用的一键入口

62 已安装独立服务：

```text
unitree-g1-head-camera-awb.service
```

手动重新执行：

```bash
sudo systemctl restart unitree-g1-head-camera-awb.service
```

查询结果：

```bash
systemctl show unitree-g1-head-camera-awb.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus
```

查看日志：

```bash
journalctl -u unitree-g1-head-camera-awb.service -n 50 --no-pager
```

当前服务会等待 teleop 10 秒、运行 AWB 5 秒，然后复用 JSON。因为 `RemainAfterExit=yes`，正常完成后显示 `active (exited)`；再次调整必须使用 `restart`，不能只用 `start`。

## 运维平台最小接入方案

### 按钮

名称建议：

```text
调整头部相机
```

不要使用“恢复参数”，因为该操作不仅写参数，还会运行一次 AWB初始化。

### 后端动作

平台后端在机器人执行：

```bash
sudo systemctl restart --no-block unitree-g1-head-camera-awb.service
```

`--no-block` 让 HTTP 请求立即返回，避免页面阻塞 15 秒。

### 状态轮询

平台每秒查询：

```bash
systemctl show unitree-g1-head-camera-awb.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus \
  -p ExecMainStartTimestamp -p ExecMainExitTimestamp
```

状态映射：

- `activating`：调整中；按钮禁用。
- `active + exited + Result=success`：调整成功。
- `failed` 或 `ExecMainStatus != 0`：调整失败。
- teleop inactive：不允许触发，并提示先启动 teleop。

### API 建议

若运维平台已有机器人命令执行能力，不需要为摄像头另建常驻 HTTP 服务。可在平台后端提供：

```text
POST /api/robots/{robotId}/camera/head/recover
GET  /api/robots/{robotId}/camera/head/recover/status
```

POST 返回 `202 Accepted`，GET 返回当前执行状态和最近日志摘要。

## 一键动作需要补强的工程项

### 1. teleop 前置检查

按钮触发前执行：

```bash
systemctl is-active --quiet cos_teleop.service
```

不 active 时拒绝执行，避免相机没有视频帧时 AWB 无法收敛。

### 2. 并发保护

调整进行中时禁止重复点击。脚本内部建议增加 `flock`，防止平台、SSH 和开机任务同时执行。

### 3. 不覆盖 JSON

一键调整只能读取：

```text
/home/unitree/camera_tool/camera-controls-state.json
```

不能调用 `--save-current`，也不能点击网页的“保存当前参数”。

### 4. 可观测性

每次执行至少记录：

- 开始和结束时间；
- robot ID；
- teleop 状态；
- AWB warm 秒数；
- 应用和跳过参数数量；
- 最终 AWB 开关与色温；
- 退出码和错误信息。

### 5. 用户提示

调整期间提示：

```text
正在调整头部相机，请保持机器人位于当前工作光照环境
```

不要让镜头正对大面积单色物体、强逆光或黑暗区域。

## 建议实施顺序

1. 先在 62 上继续使用 `systemctl restart unitree-g1-head-camera-awb.service` 验证一键恢复的稳定性。
2. 连续测试不同开机位置和光照，确定 AWB 5 秒是否足够。
3. 给脚本补并发锁、机器可读状态和明确退出码。
4. 运维平台接入 start/status 两个动作。
5. 批量部署前保留 enable 开关和一条完整回滚命令。

## 边界

这个一键动作可以恢复当前光照环境下的白平衡，但不能生成适用于所有光源的固定隐藏 RGB 增益。机器人进入明显不同的灯光区域时，可以再次点击调整，而不需要重新编辑或保存 JSON。
