# AGENTS.md

## 项目背景

这是机器人自动化部署与检查工具项目，主要用于 Unitree G1 机器人环境检查、cos_setup 安装、ROS2 topic 状态检查、摄像头/爪子/服务状态检查，以及 operator_console.py 前端按钮集成。

## 常见文件

- operator_console.py：本地 GUI/控制台入口
- batch_cos_setup.yml：Ansible 一键安装脚本
- check_base_env.yml：基础环境检查脚本
- inventory.ini：机器人主机清单
- reports/raw：原始日志输出目录

## 工作规则

- 修改前先阅读相关代码，不要直接大改。
- 优先做最小改动，不要重构无关代码。
- 每次修改后必须说明改了哪些文件、为什么改、如何验证。
- Python 修改后运行：
  - python3 -m py_compile operator_console.py
- Ansible 修改后运行：
  - ansible-playbook --syntax-check <对应 yml>
- 不要删除现有功能，除非任务明确要求删除。
- 所有输出尽量保留中文可读日志。
- 报错分析时要先找根因，不要盲目改。
