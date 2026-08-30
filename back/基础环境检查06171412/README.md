# G1 基础环境检查与安装

这个工具用于 G1 机器人基础环境检查与安装。入口 playbook 是 `check_base_env.yml`，支持 `check` 和 `install` 两种模式。

## 支持模式

- `check`: 只检查环境并生成报告，不主动安装依赖。
- `install`: 安装或恢复基础依赖、Python 环境、`unitree_sdk2` 和末端服务，并生成报告。

## 检查内容

- `g1_setup` directory
- conda engine
- `tv` environment
- `tv` Python version
- Python packages: `cv2` / `zmq` / `logging_mp` / `av` / `numpy`
- `unitree_sdk2`
- apt dependencies
- camera udev rules
- camera device symlinks
- `cos_teleop.service` 最近日志中的相机初始化异常
- `dex1` / `brainco` service
- `remote_start.service` stop policy

## install 模式能力

- 创建 `tv` Python 3.10 环境。
- 联网安装 Python 依赖，使用清华源：

```bash
{{ tv_pip }} install opencv-python pyzmq logging_mp av numpy -i https://pypi.tuna.tsinghua.edu.cn/simple
```

- 安装 / 检查 `unitree_sdk2`。
- 安装 / 恢复 `dex1` 或 `brainco`。
- 根据 `end_effector` 停止冲突末端服务：
  - `end_effector=dex1`: 停止 `brainco`。
  - `end_effector=brainco`: 停止 `dex1`。
  - `end_effector=none`: 停止两者。
- 生成 JSON report。

## 常用命令

check 单台：

```bash
ansible-playbook -k -K -i inventory.ini check_base_env.yml --limit g1_robot_xxx
```

install 单台：

```bash
ansible-playbook -k -K -i inventory.ini check_base_env.yml --limit g1_robot_xxx -e g1_mode=install
```

重建 `tv`：

```bash
ansible-playbook -k -K -i inventory.ini check_base_env.yml --limit g1_robot_xxx -e g1_mode=install -e recreate_tv_if_version_mismatch=true
```

## Inventory 示例

```ini
[g1_robots]
g1_robot_068 ansible_host=192.168.90.68 robot_id=68 end_effector=brainco
g1_robot_060 ansible_host=192.168.90.83 robot_id=60 end_effector=dex1

[g1_robots:vars]
ansible_user=unitree
ansible_python_interpreter=/usr/bin/python3
ansible_ssh_common_args='-o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
```

## 报告输出

每台机器人输出：

```text
reports/<inventory_hostname>.json
```

汇总输出：

```text
reports/g1_env_summary.json
```

每个 `WARN` / `FAIL` 项包含 `name`、`status`、`detail`、`reason`、`suggestion`。安装动作写入 `install_results`，包含 `name`、`changed`、`rc`、`detail`、`stdout`、`stderr`。最终 `overall_status` 以最终检查结果为准。

## 操作员网页工具

操作员网页工具位于 `operator_console/`，不修改现有 `check_base_env.yml`、`batch_cos_setup.yml`、`deploy_to_robot.sh` 或 `robot/` 目录。操作员通过中文网页添加机器人、执行 `cos_setup.sh`、一键安装/修复、一键检查环境，不需要手动输入任何 ansible 命令。

启动网页：

```bash
python3 operator_console/operator_console.py
```

控制电脑需要安装 Ansible 和 sshpass。网页启动时会检查 `ansible-playbook`、`sshpass`、`python3`、`inventory.ini`、`check_base_env.yml` 和 `batch_cos_setup.yml`，并在“开发者模块”的“控制电脑环境检查”中显示结果。

如果缺少 Ansible，请在控制电脑执行：

```bash
sudo apt update
sudo apt install -y ansible sshpass
```

如果 `ansible-playbook` 不在 PATH 中，可以在 `operator_console/config.yml` 配置：

```yaml
ansible:
  ansible_playbook_path: /usr/bin/ansible-playbook
```

打开浏览器访问：

```text
http://127.0.0.1:8000/
```

页面分为“操作模块”“机器人管理”“开发者模块”。

操作流程：

1. 打开网页：`http://127.0.0.1:8000/`
2. 在“机器人管理”中添加机器人，只填写 `robot_id`、IP 地址和 `end_effector`。
3. 系统会自动生成 inventory 名称，例如 `robot_id=60` 生成 `g1_robot_060`，`robot_id=5` 生成 `g1_robot_005`。
4. 普通操作员默认使用“操作模块”，输入 SSH / sudo 密码，选择机器人后执行“服务状态检查”或“服务重启”。
5. “执行 cos_setup.sh”“一键安装 / 修复”“一键检查环境”已移动到“开发者模块”，点击后需要二次确认。
6. 操作员查看“问题原因与处理建议”和“检查项明细”；工程师进入“开发者模块”查看实时日志和技术日志。

操作员不需要手动编辑 `inventory.ini`，也不需要手动填写 `g1_robot_xxx`。

“执行 cos_setup.sh”对应机器人端 `/home/unitree/cos_setup.sh` 流程。机器人端 cos_setup 会安装和检查：

- `cos_agent.service`
- `xrobotoolkit-pc-service.service`
- `cos_teleop.service`

这里的 `xrobotoolkit-pc-service.service` 是机器人端 `/home/unitree/apk` 下的 arm64 deb 安装出来的服务，不是之前 PC 端 `ubuntu_24.04_amd64` 安装包流程。当前 operator_console 的“执行 cos_setup.sh”只处理机器人端服务。

所有脚本统一使用项目根目录的同一份 inventory：

```text
inventory.ini
```

操作员页面只展示统一的最新报告：

```text
reports/latest_summary.json
```

原始报告和执行日志保留给工程师排查：

```text
reports/raw/
logs/
```

如果页面显示“无法连接机器人”，优先检查：

- 机器人是否开机。
- IP 是否正确。
- 电脑和机器人是否在同一网络。
- 网线 / Wi-Fi 是否正常。
- SSH 密码是否正确。

字段和检查项中文映射维护在：

```text
operator_console/translation_map.yml
```

一键检查环境不仅会检查 `/dev/cam_head`、`/dev/cam_wrist_left`、`/dev/cam_wrist_right` 是否存在，也会检查 `cos_teleop.service` 最近日志中是否存在相机初始化异常。

如果出现“teleop 相机运行日志检查 WARN”，建议先重启 `cos_teleop`：

```bash
sudo systemctl restart cos_teleop.service
```

然后重新执行一键检查环境。
