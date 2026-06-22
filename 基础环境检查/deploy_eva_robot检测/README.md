# G1 Robot cos_setup 部署目录

本目录只处理机器人端 `robot/` 文件分发和 `/home/unitree/cos_setup.sh` 批量执行。

## 目录说明

核心文件：

```text
deploy_eva_robot检测/
  batch_deploy_to_robot.sh
  batch_cos_setup.yml
  inventory.ini
  robot/
    deploy_to_robot.sh
    cos_setup.sh
  reports/
```

说明：

- `deploy_eva_robot.yml` 已废弃，不再推荐使用。
- 本目录不包含 `check_base_env.yml`；G1 基础环境安装/检查在独立目录执行。
- 不处理 PC 端部署。
- 不操作 dex1 / brainco 安装逻辑。
- `remote_start.service` 默认 stop，但不启动。
- stdout/stderr 全部写入 `reports/`。

## 使用说明

单台分发 robot 文件：

```bash
./robot/deploy_to_robot.sh unitree@192.168.90.68
```

批量分发 robot 文件：

```bash
./batch_deploy_to_robot.sh --inventory inventory.ini
```

只分发单台：

```bash
./batch_deploy_to_robot.sh --inventory inventory.ini --limit g1_robot_068
```

批量脚本支持：

- `--dry-run`
- `--continue-on-error`
- `--limit <inventory_hostname|IP>`

执行 `cos_setup.sh`：

```bash
ansible-playbook -k -K -i inventory.ini batch_cos_setup.yml --limit g1_robot_068
```

批量执行 `cos_setup.sh`：

```bash
ansible-playbook -k -K -i inventory.ini batch_cos_setup.yml
```

G1 基础环境安装/检查：

```text
check_base_env.yml 在独立目录执行。
```

## batch_cos_setup.yml 行为

1. 目标 hosts 为 `g1_robots`。
2. 执行前同步机器人时间，并 stop `remote_start.service`。
3. 执行 `/home/unitree/cos_setup.sh`。
4. 默认联网下载 Python 包，使用清华源。
5. 首次失败且 stdout/stderr 命中 `pyrealsense`、`librealsense`、`GLIBC` 或 `GLIBCXX` 时，清理 `librealsense2` 后重试一次。
6. check 模式不触发 remove + 重试，只输出 WARN / FAIL 报告。
7. 保留 `cos_agent` / `cos_teleop` 服务检查和 JSON report 输出。

## 报告

`batch_deploy_to_robot.sh`：

```text
reports/deploy_to_robot_<inventory_hostname>.json
reports/deploy_to_robot_summary.json
```

`batch_cos_setup.yml`：

```text
reports/cos_setup_<inventory_hostname>.json
reports/cos_setup_summary.json
```
