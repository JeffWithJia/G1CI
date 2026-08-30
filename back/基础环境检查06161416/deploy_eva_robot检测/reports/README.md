# EVA Robot cos_setup Reports

运行 `batch_cos_setup.yml` 后，本目录会生成：

- `cos_setup_<inventory_hostname>.json`
- `cos_setup_summary.json`

报告记录每台机器人执行 `/home/unitree/cos_setup.sh` 的 stdout/stderr、rc、重试状态、librealsense 清理结果、服务状态和时间戳。

历史 `deploy_eva_robot_*.json` 报告如仍存在，仅代表旧的 `deploy_to_robot.sh` 流程，不属于当前 `batch_cos_setup.yml`。
