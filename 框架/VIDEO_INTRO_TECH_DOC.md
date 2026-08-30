# G1 机器人环境检查与运维控制台技术介绍文档

## 1. 视频开场

大家好，这次介绍的是我做的 G1 机器人环境检查与运维控制台工具。

这个项目的目标是把原来需要工程师手动执行 Ansible 命令、查看英文日志、分析 JSON 报告的流程，封装成一个中文网页操作台。操作员只需要打开网页、选择机器人、输入 SSH 和 sudo 密码，就可以完成服务检查、服务重启、环境检查、安装修复、cos_setup 执行和手部动作验证。

项目主要服务于 Unitree G1 机器人的批量运维场景，核心关注点有四个：

- 降低操作门槛：普通操作员不需要记 Ansible 命令。
- 提高排障效率：把原始日志转换成中文原因和处理建议。
- 支持批量操作：可以对一台或多台机器人统一执行任务。
- 保留工程师排查能力：原始日志、JSON 报告和执行元数据仍然完整保存。

## 2. 项目背景和痛点

在做这个工具之前，G1 机器人的环境检查和修复主要依赖命令行。

典型流程是：

```bash
ansible-playbook -k -K -i inventory.ini check_base_env.yml --limit g1_robot_xxx
```

或者执行安装修复：

```bash
ansible-playbook -k -K -i inventory.ini check_base_env.yml --limit g1_robot_xxx -e g1_mode=install
```

这个方式对工程师来说可控，但对现场操作员有几个问题：

- 需要知道机器人 inventory 名称，例如 `g1_robot_060`。
- 需要知道不同动作对应哪个 playbook 和参数。
- 报错通常是 Ansible、SSH、systemd 或 Python 依赖相关的英文输出。
- 多台机器人操作时，日志和报告分散，现场判断成本高。
- 摄像头、灵巧手、cos 服务状态需要跨多个命令和日志确认。

所以我做了一个本地网页控制台，把这些命令行能力统一封装到按钮里，并把结果转成中文可读的状态和建议。

## 3. 整体架构

项目可以分成四层：

```text
浏览器页面
  ↓ HTTP API
operator_console.py 本地 Web 服务
  ↓ pexpect / shell / ansible-playbook
Ansible playbook 和部署脚本
  ↓ SSH
G1 机器人
```

核心文件如下：

```text
operator_console/operator_console.py      本地 Web 服务和任务调度核心
operator_console/templates/index.html     前端页面
operator_console/static/style.css         页面样式
operator_console/config.yml               服务端口、playbook 路径、超时时间配置
operator_console/translation_map.yml      状态、检查项和建议的中文映射
inventory.ini                             机器人清单
check_base_env.yml                        基础环境检查和安装修复 playbook
deploy_eva_robot检测/batch_cos_setup.yml  批量执行机器人端 cos_setup.sh
reports/latest_summary.json               页面展示的最新汇总报告
reports/raw/                              原始报告
logs/                                     执行日志
```

其中 `operator_console.py` 是唯一的主入口。启动后会监听 `config.yml` 中配置的地址和端口，目前是：

```yaml
server:
  host: 0.0.0.0
  port: 8000
```

因此运维电脑启动服务后，同一网络内的用户可以访问：

```text
http://运维电脑IP:8000/
```

## 4. 前端页面设计

页面分成三个模块：

### 4.1 操作模块

这是普通操作员默认使用的页面。

主要能力包括：

- 输入 SSH 密码和 sudo 密码。
- 选择全部机器人，或选择部分机器人。
- 执行服务状态检查。
- 执行指定服务重启。
- 验证灵巧手是否能动。
- 查看摄像头状态。
- 查看问题原因和处理建议。
- 查看检查项明细。

这里把高频、安全边界相对明确的操作放在首页，减少误操作。

### 4.2 机器人管理

这个模块用于维护 `inventory.ini`。

操作员只需要填写：

- `robot_id`
- IP 地址
- `end_effector`

系统会自动生成 inventory 主机名。

例如：

```text
robot_id=60 -> g1_robot_060
robot_id=5  -> g1_robot_005
```

这样操作员不需要手动编辑 inventory，也不需要记住命名规则。

### 4.3 开发者模块

这个模块面向工程师。

主要能力包括：

- 部署 robot 文件到机器人。
- 执行机器人端 `cos_setup.sh`。
- 一键安装 / 修复基础环境。
- 一键检查完整环境。
- 查看控制电脑本地依赖状态。
- 查看实时执行日志。
- 查看技术日志和 Ansible 原始输出。

这些操作可能耗时较长，或者会修改机器人环境，所以前端做了二次确认。

## 5. 后端实现原理

后端没有使用复杂框架，而是基于 Python 标准库的 `ThreadingHTTPServer` 实现。

这样做的原因是：

- 部署依赖少。
- 在运维电脑上更容易直接运行。
- 和现有脚本项目结合成本低。
- 每个请求可以独立处理，适合本地工具场景。

后端主要提供这些 API：

```text
GET  /                         返回前端 HTML 页面
GET  /api/robots               读取机器人列表
POST /api/robots               新增机器人
PUT  /api/robots/<hostname>    修改机器人
DELETE /api/robots/<hostname>  删除机器人

GET  /api/report/latest        读取最新汇总报告
GET  /api/logs/tail            读取实时日志尾部
GET  /api/job                  读取当前任务状态
GET  /api/local/checks         检查控制电脑本地依赖

POST /api/run/service_check    执行服务状态检查
POST /api/run/service_restart  执行服务重启
POST /api/run/check            执行完整环境检查
POST /api/run/install          执行安装 / 修复
POST /api/run/cos_setup        执行 cos_setup.sh
POST /api/run/deploy_to_robot  部署 robot 文件
POST /api/run/hand_test        验证手是否能动
POST /api/hand-test/cancel     取消手部测试
```

任务执行时，后端会启动后台线程，避免 HTTP 请求一直阻塞。前端每秒轮询 `/api/job` 和 `/api/logs/tail`，实时刷新任务状态和日志。

为了避免多个高风险任务同时执行，后端维护了一个全局任务状态 `CURRENT_JOB`，并通过锁控制同一时间只允许一个任务运行。

## 6. Ansible 执行链路

网页按钮最终还是复用项目已有的 Ansible 能力。

### 6.1 基础环境检查

完整检查对应：

```text
check_base_env.yml
```

它支持两种模式：

- `check`：只检查，不主动安装依赖。
- `install`：安装或恢复基础依赖、Python 环境、Unitree SDK2 和末端服务。

检查内容包括：

- `/home/unitree/g1_setup` 目录。
- conda 和 `tv` 环境。
- `tv` Python 版本。
- Python 包：`cv2`、`zmq`、`logging_mp`、`av`、`numpy`。
- `unitree_sdk2` 库、头文件和 CMake 配置。
- apt 依赖。
- 相机 udev 规则。
- `/dev/cam_head`、`/dev/cam_wrist_left`、`/dev/cam_wrist_right`。
- `cos_teleop.service` 最近日志里的相机初始化异常。
- `dex1` 或 `brainco` 灵巧手服务。
- `remote_start.service` 的运行前停止策略。

### 6.2 cos_setup 执行

`cos_setup` 对应：

```text
deploy_eva_robot检测/batch_cos_setup.yml
```

它会远程执行机器人端：

```text
/home/unitree/cos_setup.sh
```

重点检查和安装这些机器人端服务：

```text
cos_agent.service
xrobotoolkit-pc-service.service
cos_teleop.service
```

这里的 `xrobotoolkit-pc-service.service` 是机器人端 arm64 deb 包安装出的服务，不是 PC 端 amd64 安装包流程。

### 6.3 服务检查和服务重启

服务检查和服务重启由后端动态生成一个临时 playbook，再通过 Ansible 执行。

支持的服务包括：

```text
dex1_gripper.service
brainco_hand.service
cos_agent.service
cos_teleop.service
xrobotoolkit-pc-service.service
```

服务检查不仅看 systemd active 状态，还会结合部分服务日志判断异常。例如 `cos_teleop.service` 会识别 RealSense 或 V4L2 相机读取异常。

如果执行的是 `cos_teleop.service` 重启，还会额外检查三路 ROS2 摄像头 topic 心跳。

## 7. 密码和交互处理

因为 Ansible 执行时会出现 SSH 密码、sudo 密码、首次 SSH 指纹确认等交互提示，所以后端使用 `pexpect` 来运行命令。

它会识别这些情况：

- SSH password prompt。
- sudo / become password prompt。
- 首次连接的 host key 确认。
- 命令超时。
- 命令 EOF。
- 密码重复提示。

这样网页只需要让用户输入一次 SSH 密码和 sudo 密码，后端可以在命令执行过程中自动响应提示。

同时，错误会被归类成中文原因，例如：

- SSH 密码错误。
- sudo 密码缺失或错误。
- 机器人无法连接。
- 控制电脑没有安装 `ansible-playbook`。
- apt/dpkg 锁被占用。
- Python 依赖缺失。
- cos_setup.sh 不存在。
- 服务未正常运行。

## 8. 报告生成和页面展示

所有动作执行后都会生成报告。

页面展示统一读取：

```text
reports/latest_summary.json
```

原始数据保存在：

```text
reports/raw/
```

执行日志保存在：

```text
logs/
```

报告结构里会包含：

- 机器人名称。
- IP 地址。
- robot_id。
- end_effector。
- 总体状态。
- 检查项列表。
- 问题原因。
- 处理建议。
- 标准输出。
- 错误输出。
- 执行开始时间、结束时间和耗时。

页面会把状态聚合成：

- 正常。
- 警告。
- 失败。
- 跳过。
- 无法连接。

普通操作员主要看“问题原因与处理建议”；工程师可以展开技术日志继续排查。

## 9. 摄像头和手部检查

摄像头部分主要关注三路 topic：

```text
/cam_head/compressed_image
/cam_wrist_left/compressed_image
/cam_wrist_right/compressed_image
```

页面会把它们展示成头部相机、左腕相机、右腕相机三个状态。

手部验证支持：

- Dex1 灵巧手。
- Brainco 灵巧手。

前端要求一次只能选择一台机器人，并让用户选择左手或右手。后端会根据机器人的 `end_effector` 和选择的方向生成远程命令，并提供取消测试能力。

## 10. 启动和使用方式

在项目根目录启动：

```bash
python3 operator_console/operator_console.py
```

如果是在运维电脑上给局域网使用，配置里需要保证：

```yaml
server:
  host: 0.0.0.0
  port: 8000
```

然后其他电脑访问：

```text
http://运维电脑IP:8000/
```

控制电脑需要具备这些依赖：

```text
ansible-playbook
sshpass
python3
inventory.ini
check_base_env.yml
batch_cos_setup.yml
```

网页启动后会自动检查这些依赖，并在开发者模块里显示状态。

## 11. 视频演示推荐流程

录视频时可以按这个顺序演示。

### 第一步：介绍项目目录

重点展示：

```text
operator_console/operator_console.py
operator_console/templates/index.html
operator_console/config.yml
inventory.ini
check_base_env.yml
reports/
logs/
```

可以说明：网页不是单独重写一套检查逻辑，而是封装已有 Ansible 和脚本能力。

### 第二步：启动服务

执行：

```bash
python3 operator_console/operator_console.py
```

说明启动时会检查本地依赖，并打印访问地址。

### 第三步：打开网页

访问：

```text
http://运维电脑IP:8000/
```

介绍页面分为：

- 操作模块。
- 机器人管理。
- 开发者模块。

### 第四步：演示机器人管理

新增或展示一台机器人。

说明只需要维护：

- robot_id。
- IP。
- end_effector。

系统会自动生成 inventory 主机名，并写入 `inventory.ini`。

### 第五步：演示服务状态检查

在操作模块输入密码，选择机器人，点击“服务状态检查”。

讲解：

- 后端先做 SSH 端口预检查。
- 可连接后启动 Ansible。
- 前端实时显示任务状态和日志。
- 执行完成后展示服务状态、摄像头状态、问题原因和建议。

### 第六步：演示服务重启

选择一个服务，例如：

```text
cos_teleop.service
```

点击服务重启，并展示二次确认弹窗。

说明：重启服务属于会影响机器人运行状态的动作，所以做了二次确认。

### 第七步：演示开发者模块

介绍这些按钮：

- 部署到机器人。
- 执行 cos_setup.sh。
- 一键安装 / 修复。
- 一键检查环境。

强调这些是工程师操作，普通操作员一般不用。

### 第八步：展示报告和日志

展示：

```text
reports/latest_summary.json
reports/raw/
logs/
```

说明页面展示用的是最新汇总报告，原始报告和日志用于工程师追溯问题。

## 12. 异常处理设计

这个工具重点做了中文化错误处理。

比如底层日志里出现：

```text
Permission denied
```

页面会提示：

```text
SSH 密码错误，请确认 unitree 密码。
```

如果出现：

```text
Could not get lock
```

页面会提示：

```text
机器人正在执行其它 apt/dpkg 安装任务，软件包锁被占用。
```

这样现场人员不用直接理解 Ansible 或 Linux 原始错误，也能知道下一步该检查什么。

## 13. 权限和安全说明

这个工具运行在运维电脑上，访问者会在网页里输入机器人 SSH 和 sudo 密码。

所以使用时要注意：

- 只在可信运维局域网开放。
- 不建议暴露到公网。
- 后续如果长期多人使用，可以增加登录鉴权。
- 服务化后建议使用普通用户运行，不建议直接用 root 运行。

## 14. 后续服务化规划

当前启动方式是手动执行：

```bash
python3 operator_console/operator_console.py
```

后续可以做成 systemd 服务，让运维电脑开机后自动启动。

服务化思路：

- 只服务化 `operator_console.py`，不再维护额外跳转入口。
- systemd 使用普通用户运行，例如 `jeff`。
- `WorkingDirectory` 指向项目根目录。
- `ExecStart` 指向 `operator_console/operator_console.py`。
- 配置 `Restart=on-failure`，异常退出后自动恢复。
- 把 `open_browser` 改成 `false`，后台服务不自动弹浏览器。

服务化之后，用户不需要打开终端，直接访问：

```text
http://运维电脑IP:8000/
```

## 15. 总结

这个项目本质上是一个面向 G1 机器人运维场景的本地 Web 控制台。

它没有替代底层 Ansible 和脚本，而是把它们封装成更适合现场使用的操作界面：

- 操作员用中文网页执行常见任务。
- 工程师保留原始日志和报告。
- 多机器人批量操作统一入口。
- 错误原因和处理建议自动中文化。
- 摄像头、服务、灵巧手和基础环境检查集中展示。

最终效果是把“工程师命令行运维”变成“现场可执行的标准化网页流程”。
