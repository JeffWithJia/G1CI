# G1 机器人环境操作台

这是给操作员使用的本地网页工具。操作员不需要执行 ansible 命令，也不需要编辑 `inventory.ini`。

## 启动

在项目根目录执行：

```bash
python3 operator_console/operator_console.py
```

打开浏览器：

```text
http://127.0.0.1:8000
```

工具启动后会自动尝试打开这个本地页面。

启动时会检查控制电脑本地依赖：

- `ansible-playbook`
- `sshpass`
- `python3`
- `inventory.ini`
- `check_base_env.yml`
- `batch_cos_setup.yml`

如果控制电脑未安装 Ansible，请先执行：

```bash
sudo apt update
sudo apt install -y ansible sshpass
```

如果 `ansible-playbook` 不在 PATH 中，可以在 `operator_console/config.yml` 中配置绝对路径：

```yaml
ansible:
  ansible_playbook_path: /usr/bin/ansible-playbook
```

开发者模块中的“控制电脑环境检查”会显示这些依赖的当前状态。缺少 `ansible-playbook` 时，网页会在执行前直接提示“控制电脑未安装 Ansible”，不会等到任务执行后才显示英文错误。

## 页面模块

页面默认进入“操作模块”。普通操作员只需要在这里输入 SSH / sudo 密码、选择机器人、执行“服务状态检查”或“服务重启”，并查看执行结果、问题原因与处理建议。

“执行 cos_setup.sh”“一键安装 / 修复”“一键检查环境”已移动到“开发者模块”。这些操作可能耗时较长或修改机器人环境，点击后会出现二次确认弹窗，确认后才会执行。

“开发者模块”还包含实时执行日志、技术日志、Ansible 原始输出、任务执行日志、执行元信息和服务日志卡片，供工程师排查问题。

## 机器人管理

如果还没有机器人，先在“机器人管理”中添加机器人。只需要填写：

- `robot_id`
- IP 地址
- `end_effector`

系统会自动生成 inventory 名称：

```text
robot_id=60 -> g1_robot_060
robot_id=5  -> g1_robot_005
```

操作员不需要手动修改 `inventory.ini`，也不需要手动填写 `g1_robot_xxx`。

## 操作步骤

1. 打开页面后，默认使用“操作模块”。
2. 输入 SSH 密码和 sudo 密码。
3. 选择全部机器人，或勾选需要操作的单台 / 多台机器人。
4. 点击“服务状态检查”查看服务健康状态。
5. 如需恢复服务，选择服务后点击“服务重启”，并在二次确认弹窗中确认。
6. 页面会显示醒目的执行状态：执行中、执行成功、执行完成但存在警告或执行失败。
7. 查看“问题原因与处理建议”和“检查项明细”。

工程师需要执行 `cos_setup.sh`、安装 / 修复或完整环境检查时，请进入“开发者模块”执行，并在二次确认弹窗中确认机器人和操作名称。

页面只显示最新报告：`reports/latest_summary.json`。操作员不需要选择 JSON report。

原始报告保存在 `reports/raw/`，执行日志保存在 `logs/`，供工程师排查。

执行任务前，工具会先检查机器人 SSH 端口是否可连接。机器人不在线时会在几秒内显示“机器人无法连接”，不会长时间等待。

如果显示“无法连接机器人”，请先检查机器人是否开机、IP 是否正确、网络是否连通、SSH 密码是否正确。

如果显示“缺少 SSH 或 sudo 密码”，请填写密码后重新点击操作按钮。

一键检查环境不仅会检查 `/dev/cam_head`、`/dev/cam_wrist_left`、`/dev/cam_wrist_right` 是否存在，也会检查 `cos_teleop.service` 最近 150 行日志中是否存在相机初始化异常。

如果出现“teleop 相机运行日志检查 WARN”，建议先重启：

```bash
sudo systemctl restart cos_teleop.service
```

然后重新执行一键检查环境。

## cos_setup.sh 说明

“执行 cos_setup.sh”只处理机器人端 `/home/unitree/cos_setup.sh` 流程。该脚本会安装和检查机器人端服务：

- `cos_agent.service`
- `xrobotoolkit-pc-service.service`
- `cos_teleop.service`

这里的 `xrobotoolkit-pc-service.service` 来自机器人端 `/home/unitree/apk` 下的 arm64 deb 包，不是 PC 端 `ubuntu_24.04_amd64` 安装包流程。
