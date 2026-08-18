#!/usr/bin/env python3
"""Local Chinese operator console for G1 robot environment operations."""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import mimetypes
import os
import re
import shlex
import shutil
import socket
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import pexpect
import yaml


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
CONFIG_PATH = APP_DIR / "config.yml"
TRANSLATION_PATH = APP_DIR / "translation_map.yml"

ROOT_REPORTS_DIR = ROOT_DIR / "reports"
RAW_REPORTS_DIR = ROOT_REPORTS_DIR / "raw"
DEVELOPER_REPORTS_DIR = ROOT_REPORTS_DIR / "developer"
ROOT_LOGS_DIR = ROOT_DIR / "logs"
LATEST_SUMMARY_PATH = ROOT_REPORTS_DIR / "latest_summary.json"
DEVELOPER_LATEST_SUMMARY_PATH = ROOT_REPORTS_DIR / "developer_latest_summary.json"
ROBOT_RECORD_PATH = ROOT_DIR / "data" / "robot-record.json"
MAX_ROBOT_RECORD_BYTES = 20 * 1024 * 1024
ROBOT_RECORD_LOCK = threading.Lock()
ROBOT_RECORD_PROJECT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
ROBOT_RECORD_API_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{32,128}$")
ROBOT_RECORD_REMOTE_PATH_PATTERN = re.compile(r"^/api/v1/robot-record/projects/[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

STATUS_ORDER = {"FAIL": 4, "UNREACHABLE": 4, "WARN": 3, "NO_REPORT": 2, "SKIP": 1, "OK": 0}
CAMERA_TOPIC_MIN_RATE_HZ = 29.0
CAMERA_TOPIC_SPECS = (
    {"topic": "/cam_head/compressed_image", "label": "头部相机", "device": "/dev/cam_head"},
    {"topic": "/cam_wrist_left/compressed_image", "label": "左腕相机", "device": "/dev/cam_wrist_left"},
    {"topic": "/cam_wrist_right/compressed_image", "label": "右腕相机", "device": "/dev/cam_wrist_right"},
)
CAMERA_TOPIC_NAMES = {str(item["topic"]) for item in CAMERA_TOPIC_SPECS}
COS_REQUIRED_SERVICES = ("cos_agent", "xrobotoolkit-pc-service", "cos_teleop")
COS_ACTIVE_SERVICES = ("cos_agent", "cos_teleop")
COS_XROBOT_SERVICE = "xrobotoolkit-pc-service"
CAMERA_CHECK_ACTION = "camera_check"
SERVICE_ACTIONS = {"service_check", "service_restart", CAMERA_CHECK_ACTION}
CAMERA_BINDING_RULE_PATH = "/etc/udev/rules.d/99-usb-cameras.rules"
CAMERA_BINDING_PROPERTY_KEYS = ("DEVNAME", "ID_PATH", "ID_V4L_PRODUCT", "ID_SERIAL")
DEPLOY_ACTION = "deploy_to_robot"
DEPLOY_SETUP_AGENT_ACTION = "deploy_setup_agent"
HAND_TEST_ACTION = "hand_test"
JOB_SCOPE_OPERATION = "operation"
JOB_SCOPE_DEVELOPER = "developer"
JOB_SCOPES = {JOB_SCOPE_OPERATION, JOB_SCOPE_DEVELOPER}
DEVELOPER_ACTIONS = {"cos_setup", "install", "check", DEPLOY_ACTION, DEPLOY_SETUP_AGENT_ACTION}
HAND_TEST_SIDES = {"left", "right"}
HAND_TEST_SIDE_LABELS = {"left": "左手", "right": "右手"}
RESTARTABLE_SERVICES = {
    "dex1_gripper.service": "Dex1 灵巧手服务",
    "brainco_hand.service": "Brainco 灵巧手服务",
    "cos_agent.service": "cos_agent 服务",
    "cos_teleop.service": "cos_teleop 服务",
    "xrobotoolkit-pc-service.service": "XRoboToolkit 机器人侧服务",
}
SERVICE_CHECK_NAMES = [
    "cos_agent.service",
    "cos_teleop.service",
    "xrobotoolkit-pc-service.service",
    "dex1_gripper.service",
    "brainco_hand.service",
]
SERVICE_DETAIL_BASE_NAMES = [
    "cos_agent.service",
    "cos_teleop.service",
    "xrobotoolkit-pc-service.service",
]
SERVICE_DETAIL_HAND_NAMES = {
    "dex1": "dex1_gripper.service",
    "brainco": "brainco_hand.service",
}
NETWORK_STATUS_REFRESH_SECONDS = 3.0
NETWORK_STATUS_TIMEOUT_SECONDS = 1.5
NETWORK_STATUS_MAX_WORKERS = 16
HEARTBEAT_ONLINE_SECONDS = 10.0
HEARTBEAT_STALE_SECONDS = 30.0
HEARTBEAT_PATHS = {"/api/robot/heartbeat", "/api/robots/heartbeat"}
SERVICE_DETAIL_REFRESH_SECONDS = 7.0
SERVICE_QUERY_TIMEOUT_SECONDS = 20.0
SERVICE_DETAIL_STATUS_TTL_SECONDS = 30.0
REPORT_BUSINESS_FRESH_SECONDS = 300.0
REFRESH_STATUS_MAX_WORKERS = 16
# RealSense frame wait timeout can appear while Pico video waits for device readiness;
# camera health is still verified by topic frequency checks.
COS_TELEOP_CAMERA_WARNING_RE = re.compile(
    r"Failed to read direct MJPEG frame|"
    r"Failed to open V4L2 device|"
    r"Failed to initialize V4L2|"
    r"Camera initialization failed|"
    r"No such file or directory",
    re.IGNORECASE,
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
INVENTORY_VARS = [
    "ansible_user=unitree",
    "ansible_python_interpreter=/usr/bin/python3",
    "ansible_ssh_common_args='-o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'",
]
END_EFFECTORS = {"dex1", "brainco", "none"}
PASSWORD_PROMPTS = [
    r"(?im)^SSH password.*:",
    r"(?im)^ssh password.*:",
    r"(?im)[^\r\n]*@[^:\r\n]+['’]s password\s*:",
    r"(?im)^BECOME password.*:",
    r"(?im)^become password.*:",
    r"(?im)^\[sudo\] password.*:",
    r"(?im)^sudo password.*:",
    r"(?im)^password.*:",
    r"(?i)Are you sure you want to continue connecting",
    pexpect.TIMEOUT,
    pexpect.EOF,
]
SSH_PROMPT_INDEXES = {0, 1, 2}
BECOME_PROMPT_INDEXES = {3, 4, 5, 6}
GENERIC_PASSWORD_PROMPT_INDEX = 7
HOST_KEY_PROMPT_INDEX = 8
TIMEOUT_PROMPT_INDEX = 9
EOF_PROMPT_INDEX = 10
MAX_PASSWORD_PROMPT_ATTEMPTS = 2

ERROR_RULES = [
    (
        "sudo 密码未被正确识别或认证失败。",
        "请重新输入 sudo 密码后重试。",
        ["sudo 密码未被正确识别或认证失败", "BECOME password prompt repeated", "sudo password prompt repeated"],
    ),
    (
        "密码认证失败。",
        "请检查 SSH 密码和 sudo 密码是否正确。",
        ["密码认证失败", "SSH password prompt repeated", "password prompt repeated"],
    ),
    (
        "SSH 密码错误，请确认 unitree 密码。",
        "请重新输入正确的 unitree 用户 SSH 密码。",
        ["Permission denied", "Authentication failed"],
    ),
    (
        "机器人无法连接，请检查网络、IP 和 SSH 密码。",
        "请检查机器人是否开机、IP 是否正确、网络是否连通、SSH 密码是否正确。",
        ["UNREACHABLE", "Failed to connect to the host via ssh", "Connection timed out", "No route to host", "Network is unreachable", "Could not resolve hostname"],
    ),
    (
        "这是新机器人首次连接时的 SSH 指纹确认问题。",
        "请确认项目目录中 ansible.cfg 已关闭 host key checking，或联系工程师处理首次 SSH 连接。",
        ["Host Key checking is enabled", "known_hosts", "sshpass does not support this"],
    ),
    (
        "sudo 密码缺失或错误。",
        "请重新输入正确的 sudo 密码。",
        ["Missing sudo password", "Incorrect sudo password", "BECOME password"],
    ),
    (
        "软件包降级被拒绝。",
        "安装本地 deb 包时触发了降级保护。由于官方 cos_setup.sh 暂不支持自动降级，请联系工程师手动清理旧版本包。",
        ["Packages were downgraded and -y was used without --allow-downgrades", "allow-downgrades"],
    ),
    (
        "cos_setup.sh 不存在，请先部署 robot 文件。",
        "请先执行 robot 文件部署步骤，确保 /home/unitree/cos_setup.sh 已经存在。",
        ["cos_setup.sh not found", "No such file or directory", "/home/unitree/cos_setup.sh"],
    ),
    (
        "机器人安装依赖失败，可能是网络或软件源问题。",
        "请检查机器人联网状态、DNS、系统时间和软件源。",
        ["Unable to locate package", "Temporary failure resolving", "Could not resolve", "apt update failed", "Failed to fetch", "pip install failed", "Read timed out", "HTTPSConnectionPool", "Could not find a version", "No matching distribution", "certificate verify failed"],
    ),
    (
        "机器人正在执行其它 apt/dpkg 安装任务，软件包锁被占用。",
        "请等待机器人上的 apt-get / dpkg / unattended-upgrades 结束后重试；如果长时间不释放，请联系工程师确认占锁进程是否卡住。",
        ["Could not get lock", "Failed to lock apt", "apt/dpkg lock", "软件包锁被占用", "软件包锁超时未释放"],
    ),
    (
        "机器人系统时间不正确，导致 HTTPS 证书校验失败。",
        "请重新执行任务，工具会尝试同步系统时间；如果仍失败，请联系工程师手动校时。",
        ["certificate is not yet valid", "system time", "SSL certificate"],
    ),
    (
        "机器人服务未正常运行。",
        "请确认相关硬件是否连接正常。如果是一键安装后仍未恢复，请联系工程师查看服务日志。",
        ["inactive", "failed", "activating", "service is not active", "systemctl"],
    ),
    (
        "Python 依赖缺失或安装不完整。",
        "请执行“一键安装 / 修复”。如果仍失败，请联系工程师查看安装日志。",
        ["ModuleNotFoundError", "No module named"],
    ),
    (
        "控制电脑没有找到 ansible-playbook 命令。",
        "请在控制电脑安装 Ansible 和 sshpass：\nsudo apt update\nsudo apt install -y ansible sshpass",
        ["ansible-playbook", "the command was not found", "was not executable"],
    ),
]

AGENT_DIAGNOSIS_RULES = [
    {
        "id": "missing_cos_setup",
        "keywords": ["cos_setup.sh not found", "remote cos_setup.sh not found", "/home/unitree/cos_setup.sh"],
        "reason": "机器人上缺少 /home/unitree/cos_setup.sh，通常是 robot 文件未成功部署或被覆盖。",
        "suggestion": "agent 会先尝试重新部署 robot 文件，再重新执行 cos_setup.sh。",
        "repair_action": "redeploy_and_retry_cos_setup",
    },
    {
        "id": "apt_lock",
        "keywords": ["Could not get lock", "Failed to lock apt", "apt/dpkg lock", "软件包锁被占用", "软件包锁超时未释放"],
        "reason": "机器人正在执行其它 apt/dpkg 安装任务，软件包锁被占用。",
        "suggestion": "请等待机器人上的 apt-get / dpkg / unattended-upgrades 结束后重试；如果长时间不释放，请联系工程师确认占锁进程。",
        "repair_action": "",
    },
    {
        "id": "realsense",
        "keywords": ["pyrealsense", "librealsense", "RealSense", "GLIBC", "GLIBCXX"],
        "reason": "RealSense / pyrealsense 相关依赖异常，可能是 apt 源、librealsense 包或二进制兼容问题。",
        "suggestion": "batch_cos_setup.yml 已包含一次清理 librealsense 后重试的逻辑；如果仍失败，请查看 cos_setup first_attempt / retry_attempt 日志。",
        "repair_action": "",
    },
    {
        "id": "local_deb",
        "keywords": ["No .deb packages found", "Failed to install local deb package", "/home/unitree/apk", "allow-downgrades"],
        "reason": "机器人本地 deb 包缺失、架构不匹配或触发降级保护。",
        "suggestion": "请确认 deploy 阶段已把 apk 目录同步到机器人，并检查 /home/unitree/apk 下 deb 包版本和架构。",
        "repair_action": "",
    },
    {
        "id": "service_failed",
        "keywords": ["service is not active", "systemctl", "inactive", "failed", "cos_agent", "cos_teleop", "xrobotoolkit-pc-service"],
        "reason": "cos_setup 执行后关键服务未达到预期状态。",
        "suggestion": "请查看 systemctl status 和 journalctl 日志；必要时先执行服务重启，再重新执行 cos_setup。",
        "repair_action": "",
    },
]

def empty_job_state() -> dict[str, Any]:
    return {
        "running": False,
        "action": "",
        "robots": [],
        "started_at": "",
        "finished_at": "",
        "returncode": None,
        "message": "空闲",
        "log_file": "",
        "cmd": "",
        "cwd": "",
        "start_time": "",
        "end_time": "",
        "duration_seconds": None,
        "report_file": "",
        "phase": "idle",
        "stages": [],
        "current_stage": "",
        "current_task": "",
        "current_command": "",
        "cancellable": False,
        "cancel_requested": False,
        "robot_label": "",
        "hand_type": "",
        "hand_side": "",
        "hand_side_cn": "",
    }


CURRENT_JOBS: dict[str, dict[str, Any]] = {
    JOB_SCOPE_OPERATION: empty_job_state(),
    JOB_SCOPE_DEVELOPER: empty_job_state(),
}
JOB_CONTEXT = threading.local()
JOB_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()
CURRENT_PROCESS: dict[str, Any] = {"child": None}
ROBOT_STATUS_LOCK = threading.Lock()
ROBOT_NETWORK_STATUS: dict[str, dict[str, Any]] = {}
ROBOT_HEARTBEATS: dict[str, dict[str, Any]] = {}
ROBOT_SERVICE_STATUS: dict[str, dict[str, Any]] = {}
LATEST_REPORT_BUSINESS_CACHE: dict[str, Any] = {"signature": None, "robot_signature": None, "reports": {}}


def job_scope_for_action(action: str) -> str:
    return JOB_SCOPE_DEVELOPER if action in DEVELOPER_ACTIONS else JOB_SCOPE_OPERATION


def normalize_job_scope(value: Any) -> str:
    scope = str(value or JOB_SCOPE_OPERATION)
    if scope not in JOB_SCOPES:
        raise ValueError("任务 scope 只能是 operation 或 developer")
    return scope


def current_job(scope: str | None = None) -> dict[str, Any]:
    resolved_scope = normalize_job_scope(scope or getattr(JOB_CONTEXT, "scope", JOB_SCOPE_OPERATION))
    return CURRENT_JOBS[resolved_scope]


def latest_summary_path(scope: str | None = None) -> Path:
    resolved_scope = normalize_job_scope(scope or getattr(JOB_CONTEXT, "scope", JOB_SCOPE_OPERATION))
    return DEVELOPER_LATEST_SUMMARY_PATH if resolved_scope == JOB_SCOPE_DEVELOPER else LATEST_SUMMARY_PATH

STANDARD_STAGE_TEMPLATE = [
    ("connect", "连接机器人"),
    ("collect", "收集系统信息"),
    ("services", "检查服务"),
    ("ros2", "检查相机帧率"),
    ("hardware", "检查硬件"),
    ("report", "生成报告"),
]
TASK_STAGE_TEMPLATES = {
    "check": STANDARD_STAGE_TEMPLATE,
    "install": STANDARD_STAGE_TEMPLATE,
    "cos_setup": [
        ("connect", "连接机器人"),
        ("collect", "收集系统信息"),
        ("services", "安装并检查服务"),
        ("hardware", "检查硬件依赖"),
        ("report", "生成报告"),
    ],
    "service_check": [
        ("connect", "连接机器人"),
        ("services", "检查服务"),
        ("ros2", "检查相机帧率"),
        ("report", "生成报告"),
    ],
    CAMERA_CHECK_ACTION: [
        ("connect", "连接机器人"),
        ("ros2", "检查相机帧率"),
        ("report", "生成报告"),
    ],
    "service_restart": [
        ("connect", "连接机器人"),
        ("services", "重启并检查服务"),
        ("report", "生成报告"),
    ],
    DEPLOY_ACTION: [
        ("connect", "连接机器人"),
        ("deploy", "部署文件"),
        ("report", "生成报告"),
    ],
    DEPLOY_SETUP_AGENT_ACTION: [
        ("connect", "连接机器人"),
        ("deploy", "部署 robot 文件"),
        ("services", "执行 cos_setup / 服务检查"),
        ("report", "生成报告"),
    ],
    HAND_TEST_ACTION: [
        ("connect", "连接机器人"),
        ("hardware", "检查硬件动作"),
        ("report", "生成报告"),
    ],
}


def stage_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def make_job_stages(action: str) -> list[dict[str, Any]]:
    template = TASK_STAGE_TEMPLATES.get(action, STANDARD_STAGE_TEMPLATE)
    return [
        {
            "id": stage_id,
            "label": label,
            "status": "pending",
            "started_at": "",
            "finished_at": "",
            "elapsed_seconds": 0.0,
            "task": "",
            "command": "",
            "items": [],
        }
        for stage_id, label in template
    ]


def stage_index_locked(stage_id: str) -> int | None:
    stages = current_job().get("stages")
    if not isinstance(stages, list):
        return None
    for index, stage in enumerate(stages):
        if isinstance(stage, dict) and stage.get("id") == stage_id:
            return index
    return None


def candidate_stage_locked(*stage_ids: str) -> str:
    for stage_id in stage_ids:
        if stage_id and stage_index_locked(stage_id) is not None:
            return stage_id
    return ""


def first_pending_stage_locked() -> str:
    stages = current_job().get("stages")
    if not isinstance(stages, list):
        return ""
    for stage in stages:
        if isinstance(stage, dict) and stage.get("status") == "pending":
            return str(stage.get("id") or "")
    return ""


def task_stage_from_text(text: str) -> str:
    value = str(text or "").lower()
    if not value:
        return ""
    if any(keyword in value for keyword in ("report", "summary", "json", "报告", "汇总")):
        return "report"
    if any(keyword in value for keyword in ("ros2", "topic", "camera topic", "camera frequency", "topic frequency", "/cam_", "摄像头 topic", "相机频率", "相机帧率")):
        return "ros2"
    if any(keyword in value for keyword in ("service", "systemd", "journal", "cos_agent", "cos_teleop", "xrobotoolkit", "服务")):
        return "services"
    if any(keyword in value for keyword in ("dex1", "brainco", "hand", "gripper", "camera", "realsense", "udev", "hardware", "硬件", "相机", "手")):
        return "hardware"
    if any(keyword in value for keyword in ("deploy", "rsync", "sync", "copy", "部署", "同步")):
        return "deploy"
    if any(keyword in value for keyword in ("fact", "environment", "dependency", "python", "conda", "apt", "ubuntu", "debian", "time", "环境", "依赖")):
        return "collect"
    return ""


def stage_id_for_job_update_locked(updates: dict[str, Any], explicit_stage_id: str) -> str:
    job = current_job()
    action = str(updates.get("action") or job.get("action") or "")
    phase = str(updates.get("phase") or job.get("phase") or "")
    message = str(updates.get("message") or job.get("message") or "")
    task = str(updates.get("current_task") or message)
    mapped = explicit_stage_id or task_stage_from_text(task)
    if mapped:
        mapped = candidate_stage_locked(mapped)
        if mapped:
            return mapped
    if phase == "precheck":
        return candidate_stage_locked("connect")
    if phase == "starting":
        return candidate_stage_locked("collect", "deploy", "services", "hardware") or first_pending_stage_locked()
    if phase == "reporting":
        return candidate_stage_locked("report")
    if phase == "cancelling":
        return candidate_stage_locked("hardware", "services")
    if phase == "running":
        if action == DEPLOY_ACTION:
            return candidate_stage_locked("deploy")
        return candidate_stage_locked("collect", "services", "hardware", "deploy") or first_pending_stage_locked()
    return ""


def finish_stage_locked(stage: dict[str, Any], status: str, now_epoch: float) -> None:
    started_epoch = float(stage.get("_started_epoch") or now_epoch)
    stage["status"] = status
    stage["finished_at"] = stage_timestamp()
    stage["elapsed_seconds"] = round(max(0.0, now_epoch - started_epoch), 1)


def update_job_stage_locked(stage_id: str, task: str = "", command: str = "") -> None:
    job = current_job()
    stages = job.get("stages")
    if not isinstance(stages, list):
        return
    index = stage_index_locked(stage_id)
    if index is None:
        return
    now_epoch = time.time()
    for before in stages[:index]:
        if before.get("status") == "running":
            finish_stage_locked(before, "completed", now_epoch)
        elif before.get("status") == "pending":
            before.update(
                {
                    "status": "completed",
                    "started_at": stage_timestamp(),
                    "finished_at": stage_timestamp(),
                    "elapsed_seconds": 0.0,
                }
            )
    for offset, stage in enumerate(stages):
        if offset != index and stage.get("status") == "running":
            finish_stage_locked(stage, "completed", now_epoch)
    stage = stages[index]
    if stage.get("status") == "pending":
        stage["status"] = "running"
        stage["started_at"] = stage_timestamp()
        stage["_started_epoch"] = now_epoch
        stage["finished_at"] = ""
        stage["elapsed_seconds"] = 0.0
    if task:
        stage["task"] = task
        job["current_task"] = task
    if command:
        stage["command"] = command
        job["current_command"] = command
    job["current_stage"] = stage_id


def merge_job_stage_items_locked(stage_id: str, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    stages = current_job().get("stages")
    if not isinstance(stages, list):
        return
    index = stage_index_locked(stage_id)
    if index is None:
        return
    stage = stages[index]
    existing_items = stage.get("items")
    if not isinstance(existing_items, list):
        existing_items = []
        stage["items"] = existing_items
    existing_keys = {
        str(item.get("key") or item.get("label") or ""): offset
        for offset, item in enumerate(existing_items)
        if isinstance(item, dict)
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        key = str(item.get("key") or label)
        normalized = {
            "key": key,
            "label": label,
            "status": str(item.get("status") or "done"),
        }
        if key in existing_keys:
            existing_items[existing_keys[key]].update(normalized)
        else:
            existing_keys[key] = len(existing_items)
            existing_items.append(normalized)
    if stage_id == "ros2" and any(
        str(item.get("status") or "").lower() in {"failed", "warn"}
        for item in existing_items
        if isinstance(item, dict)
    ):
        now_epoch = time.time()
        if stage.get("status") in {"pending", "running", "completed"}:
            if not stage.get("_started_epoch"):
                stage["_started_epoch"] = now_epoch
                stage["started_at"] = stage_timestamp()
            finish_stage_locked(stage, "failed", now_epoch)
        stage["task"] = "相机帧率异常"


def merge_job_stage_items_by_stage_locked(stage_items: dict[str, Any]) -> None:
    for stage_id, items in stage_items.items():
        if isinstance(stage_id, str) and isinstance(items, list):
            merge_job_stage_items_locked(stage_id, items)


def finalize_job_stages_locked(final_phase: str) -> None:
    stages = current_job().get("stages")
    if not isinstance(stages, list):
        return
    now_epoch = time.time()
    failed = final_phase == "fail"
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if stage.get("status") == "running":
            finish_stage_locked(stage, "failed" if failed else "completed", now_epoch)
        elif stage.get("status") == "pending" and not failed:
            stage["status"] = "skipped"


def job_payload_locked(scope: str | None = None) -> dict[str, Any]:
    now_epoch = time.time()
    job = current_job(scope)
    payload = dict(job)
    stages = []
    for stage in job.get("stages", []):
        if not isinstance(stage, dict):
            continue
        item = {key: value for key, value in stage.items() if not key.startswith("_")}
        if stage.get("status") == "running" and stage.get("_started_epoch"):
            item["elapsed_seconds"] = round(max(0.0, now_epoch - float(stage["_started_epoch"])), 1)
        stages.append(item)
    payload["stages"] = stages
    return payload


def load_yaml(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    return loaded if isinstance(loaded, dict) else default


CONFIG = load_yaml(CONFIG_PATH, {})
TRANSLATION = load_yaml(TRANSLATION_PATH, {})


def cfg(*keys: str, default: Any = None) -> Any:
    value: Any = CONFIG
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def ensure_dirs() -> None:
    ROOT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DEVELOPER_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ROOT_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def inventory_path() -> Path:
    value = str(cfg("ansible", "inventory", default="inventory.ini"))
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


def resolve_root_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


def ansible_playbook_configured_path() -> str:
    return str(cfg("ansible", "ansible_playbook_path", default="ansible-playbook")).strip() or "ansible-playbook"


def executable_status(command: str, configured: str | None = None) -> dict[str, Any]:
    value = configured or command
    path = Path(value)
    resolved = str(path) if path.is_absolute() and os.access(path, os.X_OK) else shutil.which(value)
    return {
        "name": command,
        "configured": value,
        "path": resolved or "",
        "ok": bool(resolved),
        "status": "OK" if resolved else "MISSING",
        "status_cn": "正常" if resolved else "缺失",
    }


def file_status(label: str, path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "name": label,
        "path": str(path.relative_to(ROOT_DIR) if path.is_relative_to(ROOT_DIR) else path),
        "ok": exists,
        "status": "OK" if exists else "MISSING",
        "status_cn": "存在" if exists else "缺失",
    }


def executable_file_status(label: str, path: Path) -> dict[str, Any]:
    exists = path.exists()
    executable = exists and os.access(path, os.X_OK)
    return {
        "name": label,
        "path": str(path.relative_to(ROOT_DIR) if path.is_relative_to(ROOT_DIR) else path),
        "ok": executable,
        "status": "OK" if executable else "MISSING" if not exists else "NOT_EXECUTABLE",
        "status_cn": "正常" if executable else "缺失" if not exists else "不可执行",
    }


def dependency_payload(checks: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [item for item in checks if not item["ok"]]
    missing_names = "、".join(str(item["name"]) for item in missing)
    reason = f"控制电脑缺少必要依赖：{missing_names}" if missing else ""
    suggestion = (
        "请在控制电脑安装缺失依赖后重试。常用命令：\n"
        "sudo apt update\n"
        "sudo apt install -y ansible sshpass rsync openssh-client"
        if missing
        else ""
    )
    return {"ok": not missing, "checks": checks, "reason": reason, "suggestion": suggestion}


def deploy_setup_agent_dependency_status() -> dict[str, Any]:
    checks = [
        executable_status("ssh"),
        executable_status("rsync"),
        executable_status("sshpass"),
        executable_status("ansible-playbook", ansible_playbook_configured_path()),
        executable_status("python3"),
        file_status("inventory.ini", inventory_path()),
        executable_file_status("deploy_to_robot.sh", ROOT_DIR / "deploy_eva_robot检测" / "robot" / "deploy_to_robot.sh"),
        file_status("batch_cos_setup.yml", resolve_root_path(str(cfg("ansible", "cos_setup_playbook", default="deploy_eva_robot检测/batch_cos_setup.yml")))),
    ]
    return dependency_payload(checks)


def local_dependency_status() -> dict[str, Any]:
    ansible_status = executable_status("ansible-playbook", ansible_playbook_configured_path())
    checks = [
        executable_status("ssh"),
        executable_status("rsync"),
        executable_status("sshpass"),
        ansible_status,
        executable_status("python3"),
        file_status("inventory.ini", inventory_path()),
        file_status("check_base_env.yml", resolve_root_path(str(cfg("ansible", "check_playbook", default="check_base_env.yml")))),
        executable_file_status("deploy_to_robot.sh", ROOT_DIR / "deploy_eva_robot检测" / "robot" / "deploy_to_robot.sh"),
        file_status("batch_cos_setup.yml", resolve_root_path(str(cfg("ansible", "cos_setup_playbook", default="deploy_eva_robot检测/batch_cos_setup.yml")))),
    ]
    return dependency_payload(checks)


def ansible_playbook_executable() -> str:
    status = executable_status("ansible-playbook", ansible_playbook_configured_path())
    return status["path"] or ansible_playbook_configured_path()


def ensure_ansible_available() -> None:
    status = executable_status("ansible-playbook", ansible_playbook_configured_path())
    if not status["ok"]:
        raise ValueError("控制电脑缺少 ansible-playbook")


def ensure_deploy_setup_agent_available() -> None:
    status = deploy_setup_agent_dependency_status()
    if not status["ok"]:
        raise ValueError(status["reason"] or "控制电脑缺少必要依赖")


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def validate_robot_record_data(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("机器人台帐数据必须是 JSON 对象")
    robots = value.get("robots")
    records = value.get("records")
    issues = value.get("issues", [])
    occurrences = value.get("problemOccurrences", [])
    if not isinstance(robots, list) or len(robots) > 200:
        raise ValueError("机器人台帐中的机器人清单无效")
    if not isinstance(records, dict):
        raise ValueError("机器人台帐中的历史记录无效")
    if not isinstance(issues, list) or len(issues) > 50000:
        raise ValueError("机器人台帐中的问题单列表无效")
    if not isinstance(occurrences, list) or len(occurrences) > 100000:
        raise ValueError("机器人台帐中的问题事件列表无效")


def validate_robot_record_store(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("机器人台帐数据必须是 JSON 对象")
    if value.get("version") == 7 and isinstance(value.get("projects"), list):
        projects = value["projects"]
        if not projects or len(projects) > 50:
            raise ValueError("机器人台帐中的项目列表无效")
        active_project_id = value.get("activeProjectId")
        seen_project_ids: set[str] = set()
        for project in projects:
            if not isinstance(project, dict):
                raise ValueError("机器人台帐中的项目无效")
            project_id = project.get("id")
            project_name = project.get("name")
            if not isinstance(project_id, str) or not ROBOT_RECORD_PROJECT_ID_PATTERN.fullmatch(project_id):
                raise ValueError("机器人台帐中的项目 ID 无效")
            if project_id in seen_project_ids:
                raise ValueError("机器人台帐中的项目 ID 重复")
            if not isinstance(project_name, str) or not project_name.strip() or len(project_name) > 60:
                raise ValueError("机器人台帐中的项目名称无效")
            validate_robot_record_data(project.get("data"))
            seen_project_ids.add(project_id)
        if active_project_id not in seen_project_ids:
            raise ValueError("机器人台帐中的当前项目无效")
    else:
        validate_robot_record_data(value)
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_ROBOT_RECORD_BYTES:
        raise ValueError("机器人台帐数据超过 20 MB，无法保存")
    return json.loads(serialized)


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def read_remote_robot_record(api_url: Any, api_key: Any) -> dict[str, Any]:
    url = str(api_url or "").strip()
    token = str(api_key or "").strip()
    if not url or len(url) > 2048:
        raise ValueError("项目 API 地址无效")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("项目 API 地址仅支持不含账号密码的 HTTP 或 HTTPS 地址")
    if not ROBOT_RECORD_REMOTE_PATH_PATTERN.fullmatch(parsed.path) or parsed.query:
        raise ValueError("项目 API 地址路径无效")
    if not ROBOT_RECORD_API_KEY_PATTERN.fullmatch(token):
        raise ValueError("API Key 格式不正确")

    request = Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "OperatorConsole/2.0"},
        method="GET",
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirectHandler).open(request, timeout=10) as response:
            raw = response.read(MAX_ROBOT_RECORD_BYTES + 1)
    except HTTPError as exc:
        if exc.code == HTTPStatus.UNAUTHORIZED:
            raise PermissionError("API Key 无效或已被轮换") from exc
        if exc.code == HTTPStatus.NOT_FOUND:
            raise FileNotFoundError("远程项目不存在，请检查接口地址") from exc
        if 300 <= exc.code < 400:
            raise ValueError("项目 API 不允许重定向") from exc
        raise ConnectionError(f"项目 API 请求失败（HTTP {exc.code}）") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        raise ConnectionError(f"无法连接项目 API：{reason}") from exc

    if len(raw) > MAX_ROBOT_RECORD_BYTES:
        raise ValueError("项目 API 返回的数据超过 20 MB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("项目 API 返回的不是有效 JSON") from exc
    if not isinstance(payload, dict) or payload.get("apiVersion") != "v1":
        raise ValueError("项目 API 版本或返回格式无效")
    project = payload.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        raise ValueError("项目 API 缺少项目信息")
    validate_robot_record_data(payload.get("data"))
    return {
        "apiVersion": "v1",
        "generatedAt": str(payload.get("generatedAt") or datetime.now().astimezone().isoformat()),
        "project": {
            "id": str(project.get("id") or ""),
            "name": project["name"][:60],
            "createdAt": str(project.get("createdAt") or ""),
            "updatedAt": str(project.get("updatedAt") or ""),
        },
        "data": json.loads(json.dumps(payload["data"], ensure_ascii=False)),
    }


def load_robot_record() -> dict[str, Any]:
    with ROBOT_RECORD_LOCK:
        try:
            store = validate_robot_record_store(read_json_file(ROBOT_RECORD_PATH))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"机器人台帐数据文件无法读取：{exc}") from exc
    return {"store": store, "path": str(ROBOT_RECORD_PATH.relative_to(ROOT_DIR))}


def save_robot_record(store: Any) -> dict[str, str]:
    validated = validate_robot_record_store(store)
    temporary_path = ROBOT_RECORD_PATH.with_suffix(".json.tmp")
    with ROBOT_RECORD_LOCK:
        write_json_file(temporary_path, validated)
        os.replace(temporary_path, ROBOT_RECORD_PATH)
    return {
        "path": str(ROBOT_RECORD_PATH.relative_to(ROOT_DIR)),
        "updatedAt": datetime.now().astimezone().isoformat(),
    }


def robot_record_status_by_code() -> dict[str, dict[str, str]]:
    try:
        store = load_robot_record()["store"]
    except (OSError, ValueError, json.JSONDecodeError):
        return {}

    data = store
    if store.get("version") == 7 and isinstance(store.get("projects"), list):
        active_project_id = store.get("activeProjectId")
        active_project = next(
            (project for project in store["projects"] if project.get("id") == active_project_id),
            None,
        )
        if not isinstance(active_project, dict) or not isinstance(active_project.get("data"), dict):
            return {}
        data = active_project["data"]

    robot_codes = {
        str(robot.get("id") or ""): str(robot.get("code") or "").strip()
        for robot in data.get("robots", [])
        if isinstance(robot, dict) and robot.get("id") and robot.get("code")
    }
    records = data.get("records", {})
    if not robot_codes or not isinstance(records, dict):
        return {}

    today = datetime.now().date().isoformat()
    statuses: dict[str, dict[str, str]] = {}
    resolved_robot_ids: set[str] = set()
    record_dates = (
        key
        for key in records
        if isinstance(key, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", key) and key <= today
    )
    for record_date in sorted(record_dates, reverse=True):
        daily_records = records.get(record_date)
        if not isinstance(daily_records, dict):
            continue
        for robot_id, record in daily_records.items():
            if robot_id in resolved_robot_ids or robot_id not in robot_codes or not isinstance(record, dict):
                continue
            resolved_robot_ids.add(robot_id)
            statuses[robot_codes[robot_id]] = {
                "ledger_availability": str(record.get("availability") or ""),
            }
    return statuses


def inventory_with_robot_record_status() -> list[dict[str, str]]:
    statuses = robot_record_status_by_code()
    robots = parse_inventory()
    for robot in robots:
        robot.update(statuses.get(str(robot.get("robot_id") or ""), {}))
    return robots


def translate_status(status: Any) -> str:
    key = str(status or "UNKNOWN").upper()
    return TRANSLATION.get("status", {}).get(key, key)


def translate_item(name: Any) -> str:
    text = "" if name is None else str(name)
    return TRANSLATION.get("items", {}).get(text, text)


def translate_phrase(text: Any) -> str:
    if text is None:
        return ""
    raw = str(text)
    return TRANSLATION.get("phrases", {}).get(raw, raw)


def classify_error(text: str) -> dict[str, str]:
    haystack = text or ""
    for reason, suggestion, keywords in ERROR_RULES:
        if any(keyword.lower() in haystack.lower() for keyword in keywords):
            return {"reason": reason, "suggestion": suggestion}
    return {
        "reason": "任务执行失败，请展开技术日志并联系工程师。",
        "suggestion": "请展开技术日志，将最新 100 行 ansible 输出提供给工程师排查。",
    }


def parse_inventory(path: Path | None = None) -> list[dict[str, str]]:
    path = path or inventory_path()
    robots: list[dict[str, str]] = []
    if not path.exists():
        return robots
    in_group = False
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_group = line == "[g1_robots]"
                continue
            if not in_group:
                continue
            parts = line.split()
            if not parts:
                continue
            robot = {"inventory_hostname": parts[0]}
            for part in parts[1:]:
                if "=" in part:
                    key, value = part.split("=", 1)
                    robot[key] = value.strip("'\"")
            if not robot.get("robot_id"):
                match = re.fullmatch(r"g1_robot_(\d+)", robot["inventory_hostname"])
                if match:
                    robot["robot_id"] = str(int(match.group(1)))
            robots.append(robot)
    return robots


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def find_inventory_robot(identifier: Any, robots: list[dict[str, str]] | None = None) -> dict[str, str] | None:
    text = str(identifier or "").strip()
    if not text:
        return None
    robots = robots or parse_inventory()
    normalized_id = ""
    try:
        normalized_id = normalize_robot_id(text)
    except ValueError:
        match = re.fullmatch(r"g1_robot_(\d+)", text)
        normalized_id = str(int(match.group(1))) if match else ""
    for robot in robots:
        if text in {robot.get("inventory_hostname", ""), robot.get("ansible_host", "")}:
            return robot
        if normalized_id and robot.get("robot_id") == normalized_id:
            return robot
    return None


def default_network_status(robot: dict[str, str]) -> dict[str, Any]:
    return {
        "inventory_hostname": robot.get("inventory_hostname", ""),
        "robot_id": robot.get("robot_id", ""),
        "ansible_host": robot.get("ansible_host", ""),
        "network_status": "CHECKING",
        "latency_ms": None,
        "checked_at": "",
        "reason": "等待首次网络探测",
    }


def probe_robot_network(robot: dict[str, str]) -> dict[str, Any]:
    host = str(robot.get("ansible_host", "")).strip()
    result = default_network_status(robot)
    if not host:
        result.update(
            {
                "network_status": "UNKNOWN",
                "checked_at": now_iso(),
                "reason": "inventory.ini 未配置 ansible_host",
            }
        )
        return result
    started = time.monotonic()
    try:
        with socket.create_connection((host, 22), timeout=NETWORK_STATUS_TIMEOUT_SECONDS):
            pass
        result.update(
            {
                "network_status": "ONLINE",
                "latency_ms": int(round((time.monotonic() - started) * 1000)),
                "checked_at": now_iso(),
                "reason": "",
            }
        )
    except OSError as exc:
        result.update(
            {
                "network_status": "OFFLINE",
                "latency_ms": None,
                "checked_at": now_iso(),
                "reason": f"{host}:22 连接失败：{exc}",
            }
        )
    return result


def refresh_robot_network_status_once() -> dict[str, dict[str, Any]]:
    robots = parse_inventory()
    current_names = {robot["inventory_hostname"] for robot in robots}
    with ROBOT_STATUS_LOCK:
        for hostname in list(ROBOT_NETWORK_STATUS):
            if hostname not in current_names:
                ROBOT_NETWORK_STATUS.pop(hostname, None)
        for robot in robots:
            ROBOT_NETWORK_STATUS.setdefault(robot["inventory_hostname"], default_network_status(robot))
    if not robots:
        return {}

    max_workers = max(1, min(NETWORK_STATUS_MAX_WORKERS, len(robots)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(probe_robot_network, robot) for robot in robots]
        for future in as_completed(futures):
            status = future.result()
            hostname = status.get("inventory_hostname", "")
            if not hostname:
                continue
            with ROBOT_STATUS_LOCK:
                ROBOT_NETWORK_STATUS[hostname] = status
    with ROBOT_STATUS_LOCK:
        return {
            robot["inventory_hostname"]: dict(ROBOT_NETWORK_STATUS.get(robot["inventory_hostname"], default_network_status(robot)))
            for robot in robots
        }


def robot_network_refresher() -> None:
    while True:
        try:
            refresh_robot_network_status_once()
        except Exception as exc:
            print(f"Robot status refresh warning: {exc}")
        time.sleep(NETWORK_STATUS_REFRESH_SECONDS)


def start_robot_status_refresher() -> None:
    thread = threading.Thread(target=robot_network_refresher, daemon=True)
    thread.start()


def public_heartbeat(record: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    received_at = float(record.get("received_at_epoch") or 0)
    age = max(0.0, now - received_at) if received_at else None
    if age is None:
        business_status = "UNKNOWN"
        reason = "尚未收到业务心跳"
    elif age <= HEARTBEAT_ONLINE_SECONDS:
        business_status = "ONLINE"
        reason = ""
    elif age <= HEARTBEAT_STALE_SECONDS:
        business_status = "STALE"
        reason = f"业务心跳已超过 {int(HEARTBEAT_ONLINE_SECONDS)} 秒未更新"
    else:
        business_status = "OFFLINE"
        reason = f"业务心跳已超过 {int(HEARTBEAT_STALE_SECONDS)} 秒未更新"
    public = {key: value for key, value in record.items() if key != "received_at_epoch"}
    public.update(
        {
            "business_status": business_status,
            "heartbeat_age_seconds": round(age, 1) if age is not None else None,
            "business_reason": reason,
        }
    )
    return public


def default_business_status(reason: str = "尚未收到业务心跳，也没有最新服务报告") -> dict[str, Any]:
    return {
        "business_status": "UNKNOWN",
        "last_heartbeat_at": "",
        "heartbeat_age_seconds": None,
        "business_reason": reason,
    }


def network_offline_business_status(network: dict[str, Any]) -> dict[str, Any]:
    reason = str(network.get("reason") or "").strip()
    business_reason = "机器人网络离线，业务状态同步标记为离线"
    if reason:
        business_reason = f"{business_reason}：{reason}"
    return default_business_status(business_reason) | {"business_status": "OFFLINE"}


def report_business_fresh_seconds() -> float:
    try:
        return float(cfg("reports", "business_fresh_seconds", default=REPORT_BUSINESS_FRESH_SECONDS))
    except (TypeError, ValueError):
        return REPORT_BUSINESS_FRESH_SECONDS


def report_business_age_seconds(report: dict[str, Any]) -> float | None:
    try:
        mtime_epoch = float(report.get("_business_report_mtime_epoch") or 0)
    except (TypeError, ValueError):
        return None
    if mtime_epoch <= 0:
        return None
    return max(0.0, time.time() - mtime_epoch)


def report_business_is_fresh(report: dict[str, Any]) -> bool:
    age = report_business_age_seconds(report)
    return age is None or age <= report_business_fresh_seconds()


def latest_report_candidates(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def business_report_paths() -> list[Path]:
    paths = [
        LATEST_SUMMARY_PATH,
        ROOT_REPORTS_DIR / "g1_env_summary.json",
        RAW_REPORTS_DIR / "deploy_setup_agent_summary.json",
        RAW_REPORTS_DIR / "cos_setup_summary.json",
    ]
    paths.extend(ROOT_REPORTS_DIR.glob("g1_robot_*.json"))
    paths.extend(RAW_REPORTS_DIR.glob("g1_robot_*.json"))
    paths.extend(RAW_REPORTS_DIR.glob("cos_setup_g1_robot_*.json"))
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        if path in seen or not path.exists() or not path.is_file():
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def latest_report_business_source(robots: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    paths = business_report_paths()
    signature: list[tuple[str, int]] = []
    for path in paths:
        try:
            signature.append((str(path), path.stat().st_mtime_ns))
        except OSError:
            continue
    robot_signature = tuple(
        (robot.get("inventory_hostname", ""), robot.get("robot_id", ""), robot.get("ansible_host", ""))
        for robot in robots
    )
    if (
        LATEST_REPORT_BUSINESS_CACHE.get("signature") == tuple(signature)
        and LATEST_REPORT_BUSINESS_CACHE.get("robot_signature") == robot_signature
    ):
        return dict(LATEST_REPORT_BUSINESS_CACHE.get("reports", {}))

    by_host: dict[str, tuple[int, dict[str, Any]]] = {}
    for path_text, mtime_ns in signature:
        path = Path(path_text)
        try:
            reports = latest_report_candidates(read_json_file(path))
            updated_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for report in reports:
            robot = None
            for key in ("inventory_hostname", "host", "robot_id", "ansible_host"):
                robot = find_inventory_robot(report.get(key), robots)
                if robot:
                    break
            if not robot:
                robot = find_inventory_robot(robot_identity(report), robots)
            if not robot:
                continue
            hostname = robot["inventory_hostname"]
            if hostname in by_host and by_host[hostname][0] > mtime_ns:
                continue
            item = dict(report)
            item["_business_report_source"] = str(path.relative_to(ROOT_DIR) if path.is_relative_to(ROOT_DIR) else path)
            item["_business_report_updated_at"] = updated_at
            item["_business_report_mtime_epoch"] = path.stat().st_mtime
            by_host[hostname] = (mtime_ns, item)

    reports_by_host = {hostname: report for hostname, (_, report) in by_host.items()}
    LATEST_REPORT_BUSINESS_CACHE.update(
        {"signature": tuple(signature), "robot_signature": robot_signature, "reports": reports_by_host}
    )
    return dict(reports_by_host)


def report_service_items(report: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    services = report.get("services")
    if isinstance(services, dict):
        return [(str(name), service) for name, service in services.items() if isinstance(service, dict)]
    service_health = report.get("service_health")
    if isinstance(service_health, dict):
        return [(str(name), service) for name, service in service_health.items() if isinstance(service, dict)]
    if isinstance(service_health, list):
        return [(str(service.get("name") or service.get("service_name") or ""), service) for service in service_health if isinstance(service, dict)]
    return []


def service_business_ok(service: dict[str, Any]) -> bool:
    return service_active(service) or str(service.get("status", "")).upper() == "OK"


def service_business_label(name: str) -> str:
    return name if name.endswith(".service") else f"{name}.service"


def service_detail_status_ttl_seconds() -> float:
    try:
        return float(cfg("reports", "service_detail_status_ttl_seconds", default=SERVICE_DETAIL_STATUS_TTL_SECONDS))
    except (TypeError, ValueError):
        return SERVICE_DETAIL_STATUS_TTL_SECONDS


def refresh_status_max_workers(total: int) -> int:
    try:
        configured = int(cfg("reports", "refresh_status_max_workers", default=REFRESH_STATUS_MAX_WORKERS))
    except (TypeError, ValueError):
        configured = REFRESH_STATUS_MAX_WORKERS
    return max(1, min(configured, total))


def service_details_business_status(services: list[dict[str, Any]], checked_at: str = "") -> dict[str, Any]:
    failed: list[str] = []
    warned: list[str] = []
    for service in services:
        if not isinstance(service, dict):
            continue
        name = str(service.get("name") or service.get("service_name") or "")
        label = service_business_label(name) if name else "unknown service"
        status = str(service.get("status", "")).upper()
        active_state = str(service.get("active_state", "") or "").lower()
        if status in ("FAIL", "UNREACHABLE") or (status not in ("OK", "WARN") and active_state != "active"):
            failed.append(label)
        elif status == "WARN":
            warned.append(label)

    if failed:
        reason = f"服务详情显示业务服务异常：{', '.join(failed[:3])}"
        business_status = "FAIL"
    elif warned:
        reason = f"服务详情显示业务服务存在警告：{', '.join(warned[:3])}"
        business_status = "WARN"
    else:
        reason = f"服务详情显示业务服务正常（{checked_at}）" if checked_at else "服务详情显示业务服务正常"
        business_status = "OK"
    return default_business_status(reason) | {"business_status": business_status}


def public_service_business_status(record: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
    now = time.time() if now is None else now
    received_at = float(record.get("received_at_epoch") or 0)
    if not received_at:
        return None
    age = max(0.0, now - received_at)
    if age > service_detail_status_ttl_seconds():
        return None
    query_error = str(record.get("query_error") or "")
    if query_error:
        status = default_business_status(query_error)
        status.update(
            {
                "business_status": "UNKNOWN",
                "last_heartbeat_at": str(record.get("checked_at", "") or ""),
                "heartbeat_age_seconds": round(age, 1),
            }
        )
        return status
    services = record.get("services")
    if not isinstance(services, list):
        return None
    status = service_details_business_status(services, str(record.get("checked_at", "") or ""))
    status.update(
        {
            "last_heartbeat_at": str(record.get("checked_at", "") or ""),
            "heartbeat_age_seconds": round(age, 1),
        }
    )
    return status


def record_robot_service_status(payload: dict[str, Any]) -> None:
    hostname = str(payload.get("inventory_hostname") or payload.get("host") or "").strip()
    services = payload.get("services")
    if not hostname or not isinstance(services, list):
        return
    record = {
        "inventory_hostname": hostname,
        "checked_at": str(payload.get("checked_at", "") or now_iso()),
        "services": [dict(service) for service in services if isinstance(service, dict)],
        "received_at_epoch": time.time(),
    }
    with ROBOT_STATUS_LOCK:
        ROBOT_SERVICE_STATUS[hostname] = record


def record_robot_service_query_error(robot: dict[str, str], reason: str) -> None:
    hostname = str(robot.get("inventory_hostname") or "").strip()
    if not hostname:
        return
    record = {
        "inventory_hostname": hostname,
        "checked_at": now_iso(),
        "services": [],
        "query_error": reason,
        "received_at_epoch": time.time(),
    }
    with ROBOT_STATUS_LOCK:
        ROBOT_SERVICE_STATUS[hostname] = record


def report_business_status(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return default_business_status()

    updated_at = str(report.get("_business_report_updated_at", "") or "")
    report_source = str(report.get("_business_report_source", "") or "最新报告")
    source = f"来自 {report_source} {updated_at}；未收到实时业务心跳" if updated_at else f"来自 {report_source}；未收到实时业务心跳"
    if not report_business_is_fresh(report):
        age = report_business_age_seconds(report)
        age_text = f"{int(age)} 秒" if age is not None else "较长时间"
        return default_business_status(f"最新业务报告已超过 {age_text}，不再作为当前业务状态（{source}）")
    if report_is_unreachable(report):
        return default_business_status(f"最新报告显示机器人无法连接（{source}）") | {"business_status": "FAIL"}

    services = report_service_items(report)
    scoped_services = [
        (name, service)
        for name, service in services
        if name.removesuffix(".service") in COS_REQUIRED_SERVICES
    ] or services

    if scoped_services:
        failed: list[str] = []
        warned: list[str] = []
        for name, service in scoped_services:
            status = str(service.get("status", "")).upper()
            label = service_business_label(name)
            if status in ("FAIL", "UNREACHABLE") or (status != "WARN" and not service_business_ok(service)):
                failed.append(label)
            elif status == "WARN":
                warned.append(label)
        if failed:
            return default_business_status(f"最新报告显示业务服务异常：{', '.join(failed[:3])}（{source}）") | {"business_status": "FAIL"}
        if warned:
            return default_business_status(f"最新报告显示业务服务存在警告：{', '.join(warned[:3])}（{source}）") | {"business_status": "WARN"}
        return default_business_status(f"最新报告显示业务服务正常（{source}）") | {"business_status": "OK"}

    report_status = str(report.get("overall_status") or report.get("setup_status") or report.get("status") or "").upper()
    if report_status == "OK":
        return default_business_status(f"最新报告状态正常（{source}）") | {"business_status": "OK"}
    if report_status in ("WARN", "FAIL", "UNREACHABLE"):
        return default_business_status(f"最新报告状态为 {report_status}，但缺少业务服务明细，未作为当前业务状态（{source}）")
    return default_business_status()


def heartbeat_identity(payload: dict[str, Any], robots: list[dict[str, str]]) -> dict[str, str] | None:
    for key in ("hostname", "inventory_hostname", "host", "robot_id", "ip", "ansible_host"):
        robot = find_inventory_robot(payload.get(key), robots)
        if robot:
            return robot
    return None


def record_robot_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("heartbeat payload 必须是 JSON 对象")
    robots = parse_inventory()
    robot = heartbeat_identity(payload, robots)
    hostname = str(payload.get("hostname") or payload.get("inventory_hostname") or payload.get("host") or "").strip()
    robot_id = str(payload.get("robot_id") or "").strip()
    if robot_id:
        try:
            robot_id = normalize_robot_id(robot_id)
        except ValueError:
            if not hostname:
                raise
    if robot:
        hostname = robot["inventory_hostname"]
        robot_id = robot.get("robot_id", robot_id)
    elif not hostname and robot_id:
        try:
            hostname = hostname_from_robot_id(normalize_robot_id(robot_id))
        except ValueError:
            hostname = ""
    if not hostname:
        raise ValueError("heartbeat 缺少 robot_id 或 hostname")

    services = payload.get("services")
    topics = payload.get("topics")
    record = {
        "inventory_hostname": hostname,
        "hostname": str(payload.get("hostname") or hostname),
        "robot_id": robot_id,
        "ip": str(payload.get("ip") or payload.get("ansible_host") or (robot or {}).get("ansible_host", "")),
        "services": services if isinstance(services, (dict, list)) else {},
        "topics": topics if isinstance(topics, (dict, list)) else {},
        "timestamp": str(payload.get("timestamp") or ""),
        "last_heartbeat_at": now_iso(),
        "received_at_epoch": time.time(),
    }
    with ROBOT_STATUS_LOCK:
        ROBOT_HEARTBEATS[hostname] = record
    return {"status": "OK", "heartbeat": public_heartbeat(record)}


def robots_status_payload() -> dict[str, Any]:
    robots = parse_inventory()
    now = time.time()
    with ROBOT_STATUS_LOCK:
        network_status = {key: dict(value) for key, value in ROBOT_NETWORK_STATUS.items()}
        heartbeats = {key: dict(value) for key, value in ROBOT_HEARTBEATS.items()}
        service_status = {key: dict(value) for key, value in ROBOT_SERVICE_STATUS.items()}
    report_business = latest_report_business_source(robots)

    items: list[dict[str, Any]] = []
    for robot in robots:
        hostname = robot["inventory_hostname"]
        network = network_status.get(hostname) or default_network_status(robot)
        if str(network.get("network_status", "")).upper() == "OFFLINE":
            heartbeat = network_offline_business_status(network)
        elif hostname in heartbeats:
            heartbeat = public_heartbeat(heartbeats[hostname], now)
        elif hostname in service_status and (
            service_heartbeat := public_service_business_status(service_status[hostname], now)
        ):
            heartbeat = service_heartbeat
        else:
            heartbeat = report_business_status(report_business.get(hostname))
        items.append(
            {
                **robot,
                "network_status": network.get("network_status", "UNKNOWN"),
                "latency_ms": network.get("latency_ms"),
                "checked_at": network.get("checked_at", ""),
                "reason": network.get("reason", ""),
                "business_status": heartbeat.get("business_status", "UNKNOWN"),
                "last_heartbeat_at": heartbeat.get("last_heartbeat_at", ""),
                "heartbeat_age_seconds": heartbeat.get("heartbeat_age_seconds"),
                "business_reason": heartbeat.get("business_reason", ""),
            }
        )
    return {"robots": items, "updated_at": now_iso()}


def hostname_from_robot_id(robot_id: str) -> str:
    return f"g1_robot_{int(robot_id):03d}"


def normalize_robot_id(value: Any) -> str:
    robot_id = str(value or "").strip()
    if not re.fullmatch(r"\d+", robot_id):
        raise ValueError("robot_id 必须是数字")
    return str(int(robot_id))


def validate_robot(payload: dict[str, Any], existing_name: str | None = None) -> dict[str, str]:
    robot_id = normalize_robot_id(payload.get("robot_id"))
    hostname = hostname_from_robot_id(robot_id)
    ansible_host = str(payload.get("ansible_host") or payload.get("ip") or "").strip()
    end_effector = str(payload.get("end_effector") or "").strip().lower()
    try:
        ipaddress.ip_address(ansible_host)
    except ValueError as exc:
        raise ValueError("IP 地址格式不正确") from exc
    if end_effector not in END_EFFECTORS:
        raise ValueError("end_effector 只能选择 dex1、brainco 或 none")
    return {
        "inventory_hostname": hostname,
        "ansible_host": ansible_host,
        "robot_id": robot_id,
        "end_effector": end_effector,
    }


def write_inventory(robots: list[dict[str, str]]) -> None:
    lines = ["[g1_robots]"]
    normalized = [validate_robot(robot) for robot in robots]
    for robot in sorted(normalized, key=lambda item: int(item["robot_id"])):
        lines.append(
            f"{robot['inventory_hostname']} ansible_host={robot['ansible_host']} "
            f"robot_id={robot['robot_id']} end_effector={robot['end_effector']}"
        )
    lines.extend(["", "[g1_robots:vars]", *INVENTORY_VARS, ""])
    inventory_path().write_text("\n".join(lines), encoding="utf-8")


def add_robot(payload: dict[str, Any]) -> list[dict[str, str]]:
    robot = validate_robot(payload)
    robots = parse_inventory()
    if any(normalize_robot_id(item.get("robot_id")) == robot["robot_id"] for item in robots if item.get("robot_id")):
        raise ValueError("robot_id 已存在")
    robots.append(robot)
    write_inventory(robots)
    return parse_inventory()


def update_robot(hostname: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    robots = parse_inventory()
    target_id = ""
    try:
        target_id = normalize_robot_id(hostname)
    except ValueError:
        match = re.fullmatch(r"g1_robot_(\d+)", hostname)
        target_id = str(int(match.group(1))) if match else ""
    if not any(item["inventory_hostname"] == hostname or item.get("robot_id") == target_id for item in robots):
        raise ValueError("机器人不存在")
    robot = validate_robot(payload, existing_name=hostname)
    updated: list[dict[str, str]] = []
    for item in robots:
        is_target = item["inventory_hostname"] == hostname or item.get("robot_id") == target_id
        if is_target:
            updated.append(robot)
        elif item.get("robot_id") == robot["robot_id"]:
            raise ValueError("robot_id 已存在")
        else:
            updated.append(item)
    write_inventory(updated)
    return parse_inventory()


def delete_robot(hostname: str) -> list[dict[str, str]]:
    robots = parse_inventory()
    try:
        target_id = normalize_robot_id(hostname)
    except ValueError:
        match = re.fullmatch(r"g1_robot_(\d+)", hostname)
        target_id = str(int(match.group(1))) if match else ""
    kept = [item for item in robots if item["inventory_hostname"] != hostname and item.get("robot_id") != target_id]
    if len(kept) == len(robots):
        raise ValueError("机器人不存在")
    write_inventory(kept)
    return parse_inventory()


def robot_identity(report: dict[str, Any]) -> str:
    return str(report.get("inventory_hostname") or report.get("host") or report.get("robot_id") or "unknown")


def status_of(report: dict[str, Any]) -> str:
    status = str(report.get("overall_status") or report.get("setup_status") or report.get("status") or "UNKNOWN").upper()
    if status == "NO_REPORT":
        return "SKIP"
    return status


def text_from_obj(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(
            str(value.get(key, "") or "")
            for key in ("reason", "suggestion", "detail", "stdout", "stderr", "latest_output")
        )
    return str(value or "")


def text_payload(item: dict[str, Any]) -> str:
    return "\n".join(str(item.get(key, "") or "") for key in ("detail", "stdout", "stderr"))


def is_cos_teleop_item(item: dict[str, Any]) -> bool:
    name = str(item.get("name") or item.get("service_name") or item.get("label") or "")
    return "cos_teleop" in name


def has_cos_teleop_camera_warning(item: dict[str, Any]) -> bool:
    return is_cos_teleop_item(item) and bool(COS_TELEOP_CAMERA_WARNING_RE.search(text_payload(item)))


def camera_label_from_topic(topic: str) -> str:
    mapping = {str(item["topic"]): str(item["label"]) for item in CAMERA_TOPIC_SPECS}
    return mapping.get(topic, topic)


def normalize_issue_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    if has_cos_teleop_camera_warning(normalized):
        normalized["status"] = "WARN"
        if not normalized.get("reason"):
            normalized["reason"] = "cos_teleop 日志中发现相机读取异常，建议重启 cos_teleop.service。"
        if not normalized.get("suggestion"):
            normalized["suggestion"] = "请尝试重启 cos_teleop.service；如果仍异常，请检查相机 Type-C 连接、udev 映射、相机设备状态或相机是否被占用。"
        if not normalized.get("detail"):
            normalized["detail"] = "cos_teleop journal has recent camera error"
    return normalized


def item_key(item: dict[str, Any]) -> str:
    return str(item.get("label") or item.get("service_name") or item.get("name") or "").strip().lower()


def issue_weight(item: dict[str, Any]) -> int:
    return sum(len(str(item.get(key, "") or "")) for key in ("reason", "suggestion", "detail", "stdout", "stderr"))


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = item_key(item)
        if not key:
            key = f"__index_{len(order)}"
        if key not in by_key:
            by_key[key] = item
            order.append(key)
            continue
        if issue_weight(item) > issue_weight(by_key[key]):
            by_key[key] = item
    return [by_key[key] for key in order]


def issue_sort_key(item: dict[str, Any]) -> int:
    name = str(item.get("name", ""))
    if name.startswith("/cam_"):
        return 0
    if name == "cos_teleop camera log" or "cos_teleop" in name:
        return 1
    return 2


def aggregate_status(report: dict[str, Any], issues: list[dict[str, Any]], checks: list[dict[str, Any]]) -> str:
    base_status = status_of(report)
    if base_status in ("UNREACHABLE", "SKIP", "NO_REPORT"):
        return base_status
    base_statuses = [base_status] if base_status and base_status != "UNKNOWN" else []
    camera_issue_statuses = [
        str(item.get("status", "")).upper()
        for item in issues
        if isinstance(item, dict) and str(item.get("name", "")).startswith("/cam_")
    ]
    non_camera_issue_statuses = [
        str(item.get("status", "")).upper()
        for item in issues
        if isinstance(item, dict) and not str(item.get("name", "")).startswith("/cam_")
    ]
    check_statuses = [str(item.get("status", "")).upper() for item in checks if isinstance(item, dict)]
    service_statuses: list[str] = []
    for key in ("services", "service_health"):
        value = report.get(key)
        service_items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        service_statuses.extend(str(item.get("status", "")).upper() for item in service_items if isinstance(item, dict))

    hard_statuses = base_statuses + non_camera_issue_statuses + check_statuses + service_statuses
    if any(status in ("FAIL", "UNREACHABLE") for status in hard_statuses):
        return "FAIL"
    if report_setup_success(report):
        return "OK"
    statuses = hard_statuses + camera_issue_statuses
    if "WARN" in statuses:
        return "WARN"
    if any(status == "FAIL" for status in camera_issue_statuses):
        return "WARN"
    if statuses and all(status == "OK" for status in statuses):
        return "OK"
    return base_status


def report_is_unreachable(report: dict[str, Any]) -> bool:
    if str(report.get("overall_status", "")).upper() == "UNREACHABLE":
        return True

    if str(report.get("status", "")).upper() == "UNREACHABLE":
        return True

    text_fields = [
        report.get("execution_error", ""),
        report.get("reason", ""),
        report.get("detail", ""),
    ]
    if any("机器人无法连接" in str(value) for value in text_fields):
        return True

    for key in ("failed_items", "warn_items", "checks", "issues"):
        items = report.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).upper() == "UNREACHABLE":
                return True
    return False


def normalized_camera_status(report: dict[str, Any]) -> dict[str, float]:
    if report_is_unreachable(report):
        return {}

    raw_status = report.get("camera_status")
    if isinstance(raw_status, dict):
        status = {
            "cam_head": float(raw_status.get("cam_head") or 0),
            "cam_left": float(raw_status.get("cam_left") or 0),
            "cam_right": float(raw_status.get("cam_right") or 0),
        }
        selected_topic = str(report.get("camera_check_topic") or "")
        selected_key = {
            "/cam_head/compressed_image": "cam_head",
            "/cam_wrist_left/compressed_image": "cam_left",
            "/cam_wrist_right/compressed_image": "cam_right",
        }.get(selected_topic)
        return {selected_key: status[selected_key]} if selected_key else status

    status = {"cam_head": 0.0, "cam_left": 0.0, "cam_right": 0.0}
    if not isinstance(report.get("camera_topics"), list):
        return status

    topic_to_key = {
        "/cam_head/compressed_image": "cam_head",
        "/cam_wrist_left/compressed_image": "cam_left",
        "/cam_wrist_right/compressed_image": "cam_right",
    }
    for item in report.get("camera_topics", []) if isinstance(report.get("camera_topics"), list) else []:
        if not isinstance(item, dict):
            continue
        key = topic_to_key.get(str(item.get("topic", "")))
        if key:
            status[key] = float(item.get("rate_hz") or 0)
    return status


def camera_topic_status_for_rate(rate_hz: float) -> str:
    if rate_hz >= CAMERA_TOPIC_MIN_RATE_HZ:
        return "OK"
    if rate_hz > 0:
        return "WARN"
    return "FAIL"


def normalized_camera_topics(report: dict[str, Any], camera_status: dict[str, float]) -> list[dict[str, Any]]:
    if report_is_unreachable(report):
        return []

    if str(report.get("camera_streams_status", "")).upper() == "SKIP":
        return []

    topic_specs = [
        ("cam_head", "/cam_head/compressed_image", "头部相机"),
        ("cam_left", "/cam_wrist_left/compressed_image", "左腕相机"),
        ("cam_right", "/cam_wrist_right/compressed_image", "右腕相机"),
    ]
    selected_topic = str(report.get("camera_check_topic") or "")
    if selected_topic:
        topic_specs = [item for item in topic_specs if item[1] == selected_topic]
    raw_items = report.get("camera_topics")
    by_topic: dict[str, dict[str, Any]] = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                by_topic[str(item.get("topic", ""))] = item
    normalized: list[dict[str, Any]] = []
    for key, topic, label in topic_specs:
        existing = by_topic.get(topic, {})
        rate_hz = float(existing.get("rate_hz", camera_status.get(key, 0) if camera_status else 0) or 0)
        status = str(existing.get("status") or camera_topic_status_for_rate(rate_hz)).upper()
        reason = existing.get("reason")
        suggestion = existing.get("suggestion")
        if not reason and status == "WARN":
            reason = "摄像头 topic 帧率不足"
        if not suggestion and status == "WARN":
            suggestion = f"检查{label}帧率是否稳定，确认相机连接、系统负载和 cos_teleop.service 状态。"
        item = {
            "topic": topic,
            "label": str(existing.get("label", label) or label),
            "status": status,
            "status_cn": translate_status(status),
            "rate_hz": rate_hz,
            "reason": translate_phrase(reason or ("" if rate_hz > 0 else "camera topic no data")),
            "suggestion": translate_phrase(suggestion or ("" if rate_hz > 0 else f"检查{label} Type-C 连接或重启 cos_teleop.service")),
        }
        for metadata_key in ("sample_seconds", "adaptive_decision"):
            if metadata_key in existing:
                item[metadata_key] = existing[metadata_key]
        normalized.append(item)
    return normalized


def report_setup_success(report: dict[str, Any]) -> bool:
    if truthy(report.get("setup_success")):
        return True

    required_services = report.get("required_services")
    if isinstance(required_services, dict):
        return all(
            service_active(required_services.get(f"{name}.service", {}))
            for name in COS_REQUIRED_SERVICES
        )

    services = report.get("services")
    if isinstance(services, dict):
        return all(service_active(services.get(name, {})) for name in COS_REQUIRED_SERVICES)

    return False


def service_loaded(service: dict[str, Any]) -> bool:
    stdout = str(service.get("stdout", "") or "")
    return truthy(service.get("loaded")) or "Loaded: loaded" in stdout


def service_enabled(service: dict[str, Any]) -> bool:
    stdout = str(service.get("stdout", "") or "")
    enabled_value = service.get("enabled")
    return truthy(enabled_value) or "\nenabled\n" in f"\n{stdout}\n" or "; enabled;" in stdout


def service_active(service: dict[str, Any]) -> bool:
    stdout = str(service.get("stdout", "") or "")
    return truthy(service.get("active")) or "\nactive\n" in f"\n{stdout}\n" or "Active: active" in stdout


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "ok", "active"}


def tail_lines(text: str, count: int = 100) -> str:
    return "\n".join((text or "").splitlines()[-count:])


def parse_recap_counts(line: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("ok", "changed", "unreachable", "failed", "skipped", "rescued", "ignored"):
        match = re.search(rf"\b{key}=(\d+)", line)
        if match:
            counts[key] = int(match.group(1))
    return counts


def recap_success_from_text(text: str) -> bool:
    recap_seen = False
    for line in text.splitlines():
        if "PLAY RECAP" in line:
            recap_seen = True
            continue
        if not recap_seen:
            continue
        counts = parse_recap_counts(line)
        if counts and counts.get("failed", 0) == 0 and counts.get("unreachable", 0) == 0:
            return True
    return False


def recap_failed_from_text(text: str) -> bool:
    recap_seen = False
    for line in text.splitlines():
        if "PLAY RECAP" in line:
            recap_seen = True
            continue
        if not recap_seen:
            continue
        counts = parse_recap_counts(line)
        if counts and (counts.get("failed", 0) > 0 or counts.get("unreachable", 0) > 0):
            return True
    return False


def clean_ansible_heading(text: str) -> str:
    return re.sub(r"\s+\*+$", "", text.strip())


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def service_progress_label(host: str, service_name: str) -> str:
    service_label = RESTARTABLE_SERVICES.get(service_name, service_name)
    return f"{host}：{service_label}"


def selected_service_names_for_robot(robot: dict[str, str], service_name: str = "") -> list[str]:
    if service_name:
        return [service_name]
    names = [
        "cos_agent.service",
        "cos_teleop.service",
        "xrobotoolkit-pc-service.service",
    ]
    end_effector = str(robot.get("end_effector", "")).strip().lower()
    if end_effector == "dex1":
        names.append("dex1_gripper.service")
    elif end_effector == "brainco":
        names.append("brainco_hand.service")
    return names


def service_stage_items_for_robots(robots: list[dict[str, str]], service_name: str = "", status: str = "pending") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for robot in robots:
        host = str(robot.get("inventory_hostname") or robot.get("ansible_host") or "unknown")
        for name in selected_service_names_for_robot(robot, service_name):
            items.append(
                {
                    "key": f"{host}:service:{name}",
                    "label": service_progress_label(host, name),
                    "status": status,
                }
            )
    return items


def stage_item_status_from_report(status: str) -> str:
    value = str(status or "").upper()
    if value == "WARN":
        return "warn"
    if value in ("FAIL", "UNREACHABLE"):
        return "failed"
    return "done"


def service_stage_items_from_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for report in reports:
        host = str(report.get("inventory_hostname") or report.get("host") or report.get("ansible_host") or "unknown")
        services = report.get("services")
        if not isinstance(services, dict):
            continue
        for name, service in services.items():
            if not isinstance(service, dict):
                continue
            items.append(
                {
                    "key": f"{host}:service:{name}",
                    "label": service_progress_label(host, str(name)),
                    "status": stage_item_status_from_report(str(service.get("status") or "")),
                    "detail": str(service.get("reason") or service.get("detail") or ""),
                }
            )
    return items


def latest_service_stage_items(scope: str | None = None) -> list[dict[str, Any]]:
    path = latest_summary_path(scope)
    if not path.exists():
        return []
    data = read_json_file(path)
    reports = [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return service_stage_items_from_reports(reports)


def camera_stage_items_from_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for report in reports:
        host = str(report.get("inventory_hostname") or report.get("host") or report.get("ansible_host") or "unknown")
        camera_topics = report.get("camera_topics")
        if not isinstance(camera_topics, list):
            continue
        for camera in camera_topics:
            if not isinstance(camera, dict):
                continue
            topic = str(camera.get("topic") or "").strip()
            if not topic:
                continue
            label = str(camera.get("label") or camera_label_from_topic(topic))
            items.append(
                {
                    "key": f"{host}:topic:{topic}",
                    "label": f"{host}：{label}（{topic}）",
                    "host": host,
                    "topic": topic,
                    "status": stage_item_status_from_report(str(camera.get("status") or "")),
                    "detail": str(camera.get("reason") or camera.get("detail") or ""),
                }
            )
    return items


def latest_camera_stage_items(scope: str | None = None) -> list[dict[str, Any]]:
    path = latest_summary_path(scope)
    if not path.exists():
        return []
    data = read_json_file(path)
    reports = [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return camera_stage_items_from_reports(reports)


def merge_job_stage_items(stage_items: dict[str, list[dict[str, Any]]]) -> None:
    with JOB_LOCK:
        merge_job_stage_items_by_stage_locked(stage_items)


def parse_camera_loop_item(item_text: str) -> tuple[str, str]:
    value = item_text.strip()
    if len(value) < 1000 and value.startswith("{"):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            camera_item = parsed.get("item") if isinstance(parsed.get("item"), dict) else parsed
            if isinstance(camera_item.get("item"), dict):
                camera_item = camera_item["item"]
            topic = str(camera_item.get("topic") or "").strip()
            label = str(camera_item.get("label") or camera_label_from_topic(topic)).strip()
            if topic:
                return topic, label or topic
    label_topic_match = re.match(r"(.+?)（(/[^）]+)）$", value)
    if label_topic_match:
        label = label_topic_match.group(1).strip()
        topic = label_topic_match.group(2).strip()
        return topic, label or camera_label_from_topic(topic)
    topic_match = re.search(r"['\"]topic['\"]\s*:\s*['\"]([^'\"]+)['\"]", value)
    label_match = re.search(r"['\"]label['\"]\s*:\s*['\"]([^'\"]+)['\"]", value)
    topic = topic_match.group(1) if topic_match else value
    label = label_match.group(1) if label_match else camera_label_from_topic(topic)
    return topic, label or topic


def append_stage_progress_item(stage_items: dict[str, list[dict[str, Any]]], stage_id: str, item: dict[str, Any]) -> None:
    stage_items.setdefault(stage_id, []).append(item)


def camera_stage_status_from_result(status: str) -> str:
    value = str(status or "").strip().upper()
    if value == "OK":
        return "done"
    if value == "WARN":
        return "warn"
    if value == "FAIL":
        return "failed"
    return "pending"


def parse_ansible_progress(text: str) -> dict[str, Any]:
    progress: dict[str, Any] = {
        "message": "",
        "phase": "running",
        "task_name": "",
        "task_stage_id": "",
        "stage_items": {},
        "recap_success": False,
        "recap_failed": False,
    }
    recap_seen = False
    active_task_name = ""
    last_camera_result_item: tuple[str, str, str] | None = None
    stage_items: dict[str, list[dict[str, Any]]] = {}
    for raw_line in text.splitlines():
        line = strip_ansi(raw_line).strip()
        play_match = re.match(r"PLAY \[(.+?)\]\s+\*+", line)
        if play_match:
            progress.update({"message": f"开始执行：{clean_ansible_heading(play_match.group(1))}", "phase": "running"})
            continue
        task_match = re.match(r"TASK \[(.+?)\]\s+\*+", line)
        if task_match:
            task_name = clean_ansible_heading(task_match.group(1))
            active_task_name = task_name
            display_task_name = task_name
            message = f"正在执行：{task_name}"
            task_stage_id = task_stage_from_text(task_name)
            if task_name == "启动相机频率检查":
                display_task_name = "正在并行采样相机帧率（低帧率持续上升时最长 40 秒）"
                message = display_task_name
                task_stage_id = "ros2"
            elif task_name == "等待相机频率检查完成":
                display_task_name = "正在等待相机帧率结果"
                message = display_task_name
                task_stage_id = "ros2"
            elif task_name in {"记录相机频率状态", "输出相机频率检查结果"}:
                display_task_name = "正在整理相机帧率结果"
                message = display_task_name
                task_stage_id = "ros2"
            progress.update(
                {
                    "message": message,
                    "phase": "running",
                    "task_name": display_task_name,
                    "task_stage_id": task_stage_id,
                }
            )
            continue
        host_match = re.match(r"(ok|changed|failed|fatal): \[([^\]]+)\](?: => \(item=(.*)\))?", line)
        if host_match:
            status, host, loop_item = host_match.groups()
            item_status = "failed" if status in ("failed", "fatal") else "done"
            if loop_item and active_task_name in {"Check selected services", "检查服务"}:
                service_name = loop_item.strip()
                append_stage_progress_item(
                    stage_items,
                    "services",
                    {
                        "key": f"{host}:service:{service_name}",
                        "label": service_progress_label(host, service_name),
                        "status": item_status,
                    },
                )
            elif loop_item and active_task_name in {
                "Check ROS2 camera topic heartbeats",
                "检查相机频率",
                "启动相机频率检查",
                "等待相机频率检查完成",
                "输出相机频率检查结果",
            }:
                topic, label = parse_camera_loop_item(loop_item)
                display = label if label and label != topic else camera_label_from_topic(topic)
                suffix = f"（{topic}）" if topic.startswith("/") and topic not in display else ""
                camera_item_status = item_status
                if active_task_name in {"启动相机频率检查", "等待相机频率检查完成", "输出相机频率检查结果"}:
                    camera_item_status = "pending"
                if active_task_name == "输出相机频率检查结果":
                    last_camera_result_item = (host, topic, display)
                append_stage_progress_item(
                    stage_items,
                    "ros2",
                    {
                        "key": f"{host}:topic:{topic}",
                        "label": f"{host}：{display}{suffix}",
                        "host": host,
                        "topic": topic,
                        "status": camera_item_status,
                    },
                )
            if status in ("failed", "fatal"):
                progress.update({"message": f"{host}：执行失败，请查看日志", "phase": "fail"})
            else:
                progress.update({"message": f"{host}：该步骤完成", "phase": "running"})
            continue
        camera_result_match = re.search(r"__CAMERA_TOPIC_STATUS__\s+status=(OK|WARN|FAIL)\s+topic=([^ \"]+)", line)
        if camera_result_match and last_camera_result_item:
            result_status, result_topic = camera_result_match.groups()
            host, fallback_topic, label = last_camera_result_item
            topic = result_topic or fallback_topic
            display = label if label and label != topic else camera_label_from_topic(topic)
            suffix = f"（{topic}）" if topic.startswith("/") and topic not in display else ""
            append_stage_progress_item(
                stage_items,
                "ros2",
                {
                    "key": f"{host}:topic:{topic}",
                    "label": f"{host}：{display}{suffix}",
                    "host": host,
                    "topic": topic,
                    "status": camera_stage_status_from_result(result_status),
                },
            )
            progress.update(
                {
                    "message": f"{host}：相机帧率结果 {result_status}",
                    "phase": "running",
                    "task_name": "正在整理相机帧率结果",
                    "task_stage_id": "ros2",
                }
            )
            last_camera_result_item = None
            continue
        if "PLAY RECAP" in line:
            recap_seen = True
            progress.update(
                {
                    "message": "任务执行结束，正在生成中文报告……",
                    "phase": "reporting",
                    "task_name": "生成中文报告",
                    "task_stage_id": "report",
                }
            )
            continue
        if recap_seen:
            counts = parse_recap_counts(line)
            if not counts:
                continue
            if counts.get("failed", 0) == 0 and counts.get("unreachable", 0) == 0:
                progress.update({"message": "执行完成：任务成功。", "phase": "success", "recap_success": True})
            else:
                progress.update({"message": "执行失败：存在失败或无法连接的机器人", "phase": "fail", "recap_failed": True})
    progress["stage_items"] = stage_items
    return progress


def report_source_for_action(action: str) -> Path:
    if action == DEPLOY_SETUP_AGENT_ACTION:
        return RAW_REPORTS_DIR / "deploy_setup_agent_summary.json"
    if action == "cos_setup":
        return RAW_REPORTS_DIR / "cos_setup_summary.json"
    if action in {"check", "install"}:
        return DEVELOPER_REPORTS_DIR / "g1_env_summary.json"
    return ROOT_REPORTS_DIR / "g1_env_summary.json"


def report_path_text(action: str) -> str:
    return str(report_source_for_action(action).relative_to(ROOT_DIR))


def collect_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for key in ("failed_items", "warn_items", "skipped_items"):
        value = report.get(key)
        if isinstance(value, list):
            issues.extend([normalize_issue_item(x) for x in value if isinstance(x, dict) and x.get("name") != "Python pyrealsense2"])
    if not issues and isinstance(report.get("card"), dict):
        card_issues = report["card"].get("issues", [])
        if isinstance(card_issues, list):
            issues.extend([normalize_issue_item(x) for x in card_issues if isinstance(x, dict) and x.get("name") != "Python pyrealsense2"])
    for item in report.get("checks", []) if isinstance(report.get("checks"), list) else []:
        if not isinstance(item, dict) or item.get("name") == "Python pyrealsense2":
            continue
        normalized = normalize_issue_item(item)
        if str(normalized.get("status", "")).upper() in ("WARN", "FAIL", "UNREACHABLE"):
            issues.append(normalized)
    for item in report.get("camera_topics", []) if isinstance(report.get("camera_topics"), list) else []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).upper()
        if status not in ("WARN", "FAIL"):
            continue
        topic = str(item.get("topic", "") or "")
        label = str(item.get("label", "") or camera_label_from_topic(topic))
        rate_hz = item.get("rate_hz", 0)
        issues.append(
            {
                "name": topic,
                "status": status,
                "reason": item.get("reason") or f"{label}图像无数据或频率异常。",
                "suggestion": item.get("suggestion")
                or f"请检查{label} Type-C 连接、相机供电、udev 映射，并尝试重启 cos_teleop.service。",
                "detail": item.get("detail") or f"{topic} rate_hz={rate_hz}",
                "stdout": item.get("stdout", ""),
                "stderr": item.get("stderr", ""),
            }
        )
    service_health = report.get("service_health")
    service_health_items = service_health.values() if isinstance(service_health, dict) else service_health if isinstance(service_health, list) else []
    for item in service_health_items:
        if not isinstance(item, dict):
            continue
        normalized = normalize_issue_item(item)
        if str(normalized.get("status", "")).upper() in ("WARN", "FAIL", "UNREACHABLE"):
            issues.append(normalized)
    if report.get("setup_status") or report.get("setup_detail"):
        status = str(report.get("setup_status") or report.get("overall_status") or "UNKNOWN").upper()
        if status != "OK":
            first = report.get("first_attempt") if isinstance(report.get("first_attempt"), dict) else {}
            retry = report.get("retry_attempt") if isinstance(report.get("retry_attempt"), dict) else {}
            log_text = "\n".join([str(report.get("setup_detail", "")), text_from_obj(first), text_from_obj(retry)])
            classified = classify_error(log_text)
            issues.insert(
                0,
                {
                    "name": "cos_setup.sh",
                    "status": status,
                    "reason": classified["reason"],
                    "suggestion": classified["suggestion"],
                    "detail": f"返回码：{report.get('rc', '')}；{translate_phrase(report.get('setup_detail', ''))}",
                    "stdout": first.get("stdout", ""),
                    "stderr": first.get("stderr", ""),
                },
            )
    if report.get("setup_status"):
        services = report.get("services") if isinstance(report.get("services"), dict) else {}
        if COS_XROBOT_SERVICE not in services:
            issues.append(
                {
                    "name": f"{COS_XROBOT_SERVICE} service enabled",
                    "status": "WARN",
                    "reason": "cos_setup 报告缺少 XRoboToolkit 服务检查结果。",
                    "suggestion": "请确认 batch_cos_setup.yml 已更新，并重新执行 cos_setup。",
                    "detail": "missing xrobotoolkit-pc-service service check",
                }
            )
        else:
            service = services.get(COS_XROBOT_SERVICE)
            service = service if isinstance(service, dict) else {}
            if not service_loaded(service):
                issues.append(
                    {
                        "name": f"{COS_XROBOT_SERVICE} service enabled",
                        "status": "FAIL",
                        "reason": "XRoboToolkit 机器人侧服务未安装。",
                        "suggestion": "请重新执行“执行 cos_setup.sh”。如果仍失败，请联系工程师查看 /home/unitree/apk 下的 deb 包是否存在。",
                        "detail": service.get("detail", "unit file not loaded"),
                        "stdout": service.get("stdout", ""),
                        "stderr": service.get("stderr", ""),
                    }
                )
            elif not service_enabled(service):
                issues.append(
                    {
                        "name": f"{COS_XROBOT_SERVICE} service enabled",
                        "status": "FAIL",
                        "reason": "XRoboToolkit 机器人侧服务未设置开机自启。",
                        "suggestion": "请重新执行“执行 cos_setup.sh”，或联系工程师检查 systemd 服务文件。",
                        "detail": service.get("detail", "service disabled"),
                        "stdout": service.get("stdout", ""),
                        "stderr": service.get("stderr", ""),
                    }
                )
        for service_name in COS_ACTIVE_SERVICES:
            service = services.get(service_name)
            if not isinstance(service, dict) or service_active(service):
                continue
            issues.append(
                {
                    "name": f"{service_name} service active",
                    "status": "FAIL",
                    "reason": f"{service_name}.service 未正常运行。",
                    "suggestion": "请重新执行“执行 cos_setup.sh”。如果仍失败，请联系工程师查看 systemctl status 和 journalctl 日志。",
                    "detail": service.get("detail", "service not active"),
                    "stdout": service.get("stdout", ""),
                    "stderr": service.get("stderr", ""),
                }
            )
    if str(report.get("mode", "")) in SERVICE_ACTIONS and isinstance(report.get("services"), dict):
        for service_name, service in report["services"].items():
            if not isinstance(service, dict):
                continue
            service = normalize_issue_item(service)
            service_status = str(service.get("status", "")).upper()
            if service_status not in ("WARN", "FAIL"):
                continue
            raw_reason = str(service.get("reason", "") or "")
            raw_suggestion = str(service.get("suggestion", "") or "")
            reason = raw_reason or f"{service_name} 服务状态异常。"
            suggestion = raw_suggestion or "请尝试重启该服务；如果仍异常，请检查服务日志和机器人连接状态。"
            if service_name == "cos_teleop.service" and service.get("camera_error"):
                reason = raw_reason or "cos_teleop 服务正在运行，但日志中存在相机读取或初始化异常。"
                suggestion = raw_suggestion or "请尝试重启 cos_teleop.service；如果仍异常，请检查相机 Type-C 连接、udev 映射、相机设备状态或相机是否被占用。"
            elif service_name == "xrobotoolkit-pc-service.service":
                reason = "XRoboToolkit 机器人侧服务未安装或未设置开机自启。"
                suggestion = "请执行服务重启；如果仍失败，请重新执行“执行 cos_setup.sh”并检查 /home/unitree/apk 下的 deb 包。"
            issues.append(
                {
                    "name": service_name,
                    "status": service_status,
                    "reason": reason,
                    "suggestion": suggestion,
                    "detail": service.get("detail", ""),
                    "stdout": service.get("stdout", ""),
                    "stderr": service.get("stderr", ""),
                }
            )
    if report.get("execution_error") and not issues:
        classified = classify_error(text_from_obj(report))
        issues.insert(
            0,
            {
                "name": "任务执行",
                "status": report.get("overall_status", "FAIL"),
                "reason": classified["reason"],
                "suggestion": classified["suggestion"],
                "detail": report.get("execution_error", ""),
                "stdout": report.get("stdout", ""),
                "stderr": report.get("stderr", "") or report.get("latest_output", ""),
            },
        )
    return sorted(dedupe_items(issues), key=issue_sort_key)


def raw_outputs(report: dict[str, Any]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []

    def add(label: str, obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        stdout = str(obj.get("stdout", "") or "")
        stderr = str(obj.get("stderr", "") or "")
        if stdout or stderr:
            outputs.append({"label": translate_item(label), "name": label, "stdout": stdout, "stderr": stderr})

    ansible_stdout = str(report.get("ansible_stdout", "") or "")
    ansible_stderr = str(report.get("ansible_stderr", "") or "")
    if ansible_stdout or ansible_stderr:
        outputs.append({"label": "Ansible 原始输出", "stdout": ansible_stdout, "stderr": ansible_stderr})
    if report.get("stdout") or report.get("stderr") or report.get("latest_output"):
        outputs.append(
            {
                "label": "任务执行日志",
                "stdout": str(report.get("stdout", "") or ""),
                "stderr": str(report.get("stderr", "") or report.get("latest_output", "") or ""),
            }
        )
    metadata_lines = [
        f"command: {report.get('command', '')}",
        f"cwd: {report.get('cwd', '')}",
        f"returncode: {report.get('returncode', '')}",
        f"report_path: {report.get('report_path', '')}",
        f"log_file: {report.get('log_file', '')}",
    ]
    if any(line.split(": ", 1)[1] for line in metadata_lines):
        outputs.append({"label": "执行元信息", "stdout": "\n".join(metadata_lines), "stderr": ""})
    for item in report.get("checks", []) if isinstance(report.get("checks"), list) else []:
        if isinstance(item, dict) and item.get("name") != "Python pyrealsense2":
            add(str(item.get("name", "检查项")), item)
    for item in report.get("install_results", []) if isinstance(report.get("install_results"), list) else []:
        add(str(item.get("name", "安装项")), item)
    for key in ("first_attempt", "retry_attempt", "librealsense_cleanup", "remote_start", "time_sync"):
        add(key, report.get(key))
    for phase in report.get("agent_phases", []) if isinstance(report.get("agent_phases"), list) else []:
        if not isinstance(phase, dict):
            continue
        phase_name = str(phase.get("label") or phase.get("name") or "agent phase")
        add(f"agent: {phase_name}", phase)
    services = report.get("services")
    if isinstance(services, dict):
        for name, obj in services.items():
            if name in RESTARTABLE_SERVICES:
                add(RESTARTABLE_SERVICES[name], obj)
            else:
                add(f"{name} service active", obj)
                add(f"{name} service enabled", obj)
    return [{"label": item["label"], "stdout": item["stdout"], "stderr": item["stderr"]} for item in dedupe_items(outputs)]


def normalize_report(report: dict[str, Any]) -> dict[str, Any]:
    checks = [
        normalize_issue_item(item)
        for item in report.get("checks", [])
        if isinstance(item, dict) and item.get("name") != "Python pyrealsense2"
    ]
    issues = collect_issues(report)
    status = aggregate_status(report, issues, checks)
    camera_status = normalized_camera_status(report)
    camera_topics = normalized_camera_topics(report, camera_status)
    return {
        "inventory_hostname": robot_identity(report),
        "robot_id": str(report.get("robot_id", "")),
        "ansible_host": str(report.get("ansible_host", "")),
        "end_effector": str(report.get("end_effector", "")),
        "mode": str(report.get("mode", "cos_setup" if report.get("setup_status") else "")),
        "status": status,
        "status_cn": translate_status(status),
        "system_services_status": str(report.get("system_services_status", "")),
        "camera_streams_status": str(report.get("camera_streams_status", "")),
        "camera_status": camera_status,
        "issue_count": len(issues),
        "summary": (
            "cos_setup.sh 执行完成，cos_agent / cos_teleop / xrobotoolkit-pc-service 状态正常。"
            if status == "OK" and (report.get("setup_status") or report.get("services"))
            else "未发现需要处理的问题。"
            if not issues
            else "；".join([translate_phrase(item.get("reason", "")) for item in issues[:2] if item.get("reason")])
        ),
        "issues": [
            {
                "name": translate_item(item.get("name")),
                "name_raw": str(item.get("name", "")),
                "status": str(item.get("status", status)).upper(),
                "status_cn": translate_status(item.get("status", status)),
                "reason": translate_phrase(item.get("reason", "")),
                "suggestion": translate_phrase(item.get("suggestion", "")),
                "detail": translate_phrase(item.get("detail", "")),
                "stdout": str(item.get("stdout", "") or ""),
                "stderr": str(item.get("stderr", "") or ""),
            }
            for item in issues
        ],
        "checks": [
            {
                "name": translate_item(item.get("name")),
                "status": str(item.get("status", "")).upper(),
                "status_cn": translate_status(item.get("status", "")),
                "detail": translate_phrase(item.get("detail", "")),
            }
            for item in checks
        ],
        "camera_topics": camera_topics,
        "raw_outputs": raw_outputs(report),
    }


def latest_report_payload(scope: str = JOB_SCOPE_OPERATION) -> dict[str, Any]:
    summary_path = latest_summary_path(scope)
    data: Any = []
    source = str(summary_path.relative_to(ROOT_DIR))
    fallback_path = DEVELOPER_REPORTS_DIR / "g1_env_summary.json" if scope == JOB_SCOPE_DEVELOPER else ROOT_REPORTS_DIR / "g1_env_summary.json"
    if summary_path.exists():
        data = read_json_file(summary_path)
    elif fallback_path.exists():
        data = read_json_file(fallback_path)
        write_json_file(summary_path, data)
        source = str(fallback_path.relative_to(ROOT_DIR))
    reports = [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    robots = [normalize_report(item) for item in reports]
    counts = {"total": len(robots), "ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for robot in robots:
        status = robot["status"]
        if status == "OK":
            counts["ok"] += 1
        elif status in ("FAIL", "UNREACHABLE"):
            counts["fail"] += 1
        elif status == "SKIP":
            counts["skip"] += 1
        else:
            counts["warn"] += 1
    robots.sort(key=lambda item: (-STATUS_ORDER.get(item["status"], 2), item["inventory_hostname"]))
    updated_at = datetime.fromtimestamp(summary_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if summary_path.exists() else "-"
    return {"counts": counts, "robots": robots, "source": source, "updated_at": updated_at}


def camera_warning_message(issues: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        topic = str(issue.get("name_raw", "") or "")
        if not topic.startswith("/cam_") or issue.get("status") != "WARN":
            continue
        label = camera_label_from_topic(topic)
        if label not in seen:
            labels.append(label)
            seen.add(label)
    if not labels:
        return ""
    label_text = "、".join(labels)
    if len(labels) == 1:
        suffix = "Type-C 连接" if labels[0] in ("左腕相机", "右腕相机") else "连接"
        return f"{label_text}图像无数据或频率异常，建议检查{label_text}{suffix}或重启 cos_teleop.service。"
    return f"{label_text}图像无数据或频率异常，建议检查对应相机连接或重启 cos_teleop.service。"


def latest_action_outcome(scope: str | None = None) -> tuple[int, str, str]:
    path = latest_summary_path(scope)
    if not path.exists():
        return 1, "执行失败：未生成报告。", "fail"
    data = read_json_file(path)
    reports = [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    robots = [normalize_report(item) for item in reports]
    if not robots:
        return 1, "执行失败：未生成报告。", "fail"
    problem_robots = [robot for robot in robots if robot["status"] in ("FAIL", "UNREACHABLE", "WARN")]
    if problem_robots:
        first = problem_robots[0]
        first_issue = first["issues"][0] if first.get("issues") else {}
        camera_warnings = [
            issue
            for robot in problem_robots
            if robot["status"] == "WARN"
            for issue in robot.get("issues", [])
            if str(issue.get("name_raw", "")).startswith("/cam_") and issue.get("status") == "WARN"
        ]
        camera_message = camera_warning_message(camera_warnings)
        if first["status"] == "WARN" and camera_warnings:
            first_issue = camera_warnings[0]
        reason = first_issue.get("reason") or first.get("summary") or "请查看下方问题报告。"
        if first["status"] == "WARN" and camera_message:
            reason = camera_message
        if first["status"] in ("FAIL", "UNREACHABLE"):
            return 1, f"执行失败：{reason}", "fail"
        return 0, f"执行完成但存在警告：{reason}", "partial"
    if any(str(item.get("mode", "")) == CAMERA_CHECK_ACTION for item in reports if isinstance(item, dict)):
        host_names = [robot["inventory_hostname"] for robot in robots if robot.get("inventory_hostname")]
        host_label = host_names[0] if len(host_names) == 1 else "所有机器人"
        selected_topics = {
            str(item.get("camera_check_topic") or "")
            for item in reports
            if isinstance(item, dict) and item.get("camera_check_topic")
        }
        camera_label = camera_label_from_topic(next(iter(selected_topics))) if len(selected_topics) == 1 else "三路相机"
        return 0, f"执行成功：{host_label} {camera_label}帧率正常。", "success"
    if any(str(item.get("mode", "")) in SERVICE_ACTIONS for item in reports if isinstance(item, dict)):
        host_names = [robot["inventory_hostname"] for robot in robots if robot.get("inventory_hostname")]
        host_label = host_names[0] if len(host_names) == 1 else "所有机器人"
        return 0, f"执行成功：{host_label} 所有关键服务正常。", "success"
    return 0, "执行完成：任务成功。", "success"


def copy_raw_jsons(pattern: str) -> None:
    for item in ROOT_REPORTS_DIR.glob(pattern):
        if item.parent == RAW_REPORTS_DIR:
            continue
        shutil.copy2(item, RAW_REPORTS_DIR / item.name)


def update_latest_from_action(action: str, execution: dict[str, Any]) -> None:
    source = report_source_for_action(action)
    summary_path = latest_summary_path(job_scope_for_action(action))
    if job_scope_for_action(action) == JOB_SCOPE_OPERATION:
        copy_raw_jsons("g1_robot_*.json")
    if source.exists():
        data = read_json_file(source)
        reports = [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        write_json_file(summary_path, attach_execution_metadata(reports, execution))
        if action == "cos_setup":
            for item in RAW_REPORTS_DIR.glob("cos_setup_*.json"):
                if item.name != "cos_setup_summary.json":
                    shutil.copy2(item, RAW_REPORTS_DIR / item.name)
        return
    write_json_file(summary_path, build_execution_summary(action, execution))


def selected_inventory_robots(selected: list[str]) -> list[dict[str, str]]:
    names = {str(item) for item in selected if item}
    selected_ids: set[str] = set()
    for item in names:
        try:
            selected_ids.add(normalize_robot_id(item))
            continue
        except ValueError:
            pass
        match = re.fullmatch(r"g1_robot_(\d+)", item)
        if match:
            selected_ids.add(str(int(match.group(1))))
    return [
        robot
        for robot in parse_inventory()
        if not names or robot["inventory_hostname"] in names or robot.get("robot_id") in selected_ids or robot.get("ansible_host") in names
    ]


def requested_robot_names(selected: list[str]) -> list[str]:
    resolved = [robot["inventory_hostname"] for robot in selected_inventory_robots(selected)]
    if resolved:
        return resolved
    return [str(item).strip() for item in selected if str(item).strip()]


def conflicting_job_locked(scope: str, robot_names: list[str]) -> dict[str, Any] | None:
    other_scope = JOB_SCOPE_DEVELOPER if scope == JOB_SCOPE_OPERATION else JOB_SCOPE_OPERATION
    other_job = current_job(other_scope)
    if not other_job.get("running"):
        return None
    if set(robot_names).intersection(str(item) for item in other_job.get("robots", [])):
        return other_job
    return None


def unreachable_report(robot: dict[str, str], action: str, detail: str = "") -> dict[str, Any]:
    reason = "机器人无法连接，请检查网络、IP 和 SSH 密码。"
    suggestion = "请检查机器人是否开机、IP 是否正确、电脑和机器人是否在同一网络、网线/Wi-Fi 是否正常，以及 SSH 密码是否正确。"
    return {
        "inventory_hostname": robot["inventory_hostname"],
        "robot_id": robot.get("robot_id", ""),
        "ansible_host": robot.get("ansible_host", ""),
        "end_effector": robot.get("end_effector", ""),
        "mode": action,
        "overall_status": "UNREACHABLE",
        "camera_streams_status": "SKIP",
        "camera_status": {},
        "camera_topics": [],
        "execution_error": reason,
        "reason": reason,
        "suggestion": suggestion,
        "detail": detail,
        "failed_items": [
            {
                "name": "SSH 连接",
                "status": "UNREACHABLE",
                "reason": reason,
                "suggestion": suggestion,
                "detail": detail or f"{robot.get('ansible_host', '')}:22 无法连接",
            }
        ],
    }


def precheck_ssh(robots: list[dict[str, str]], action: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    timeout = float(cfg("ansible", "ssh_precheck_timeout_seconds", default=3))
    reachable: list[dict[str, str]] = []
    unreachable: list[dict[str, Any]] = []
    for robot in robots:
        host = robot.get("ansible_host", "")
        try:
            with socket.create_connection((host, 22), timeout=timeout):
                reachable.append(robot)
        except OSError as exc:
            unreachable.append(unreachable_report(robot, action, f"{host}:22 连接失败：{exc}"))
    return reachable, unreachable


def merge_latest_summary(action: str, execution: dict[str, Any], extra_reports: list[dict[str, Any]]) -> None:
    source = report_source_for_action(action)
    summary_path = latest_summary_path(job_scope_for_action(action))
    if job_scope_for_action(action) == JOB_SCOPE_OPERATION:
        copy_raw_jsons("g1_robot_*.json")
    reports: list[dict[str, Any]] = []
    if source.exists():
        data = read_json_file(source)
        reports = [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    else:
        reports = build_execution_summary(action, execution)
    by_host = {robot_identity(item): item for item in reports}
    for item in extra_reports:
        by_host[robot_identity(item)] = item
    write_json_file(summary_path, attach_execution_metadata(list(by_host.values()), execution))


def attach_execution_metadata(reports: list[dict[str, Any]], execution: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = {
        "command": execution.get("command", ""),
        "cwd": execution.get("cwd", ""),
        "returncode": execution.get("returncode", ""),
        "report_path": execution.get("report_path", ""),
        "log_file": execution.get("log_file", ""),
        "ansible_stdout": execution.get("stdout", ""),
        "ansible_stderr": execution.get("stderr", ""),
        "latest_output": execution.get("latest_output", ""),
        "start_time": execution.get("start_time", ""),
        "end_time": execution.get("end_time", ""),
        "duration_seconds": execution.get("duration_seconds", ""),
    }
    enriched: list[dict[str, Any]] = []
    for report in reports:
        item = dict(report)
        for key, value in metadata.items():
            item.setdefault(key, value)
        enriched.append(item)
    return enriched


def build_execution_summary(action: str, execution: dict[str, Any]) -> list[dict[str, Any]]:
    selected = execution.get("robots") or []
    robots = [item for item in parse_inventory() if not selected or item["inventory_hostname"] in selected]
    combined = "\n".join([str(execution.get("stdout", "")), str(execution.get("stderr", ""))])
    returncode = int(execution.get("returncode") or 0)
    classified = classify_error(combined)
    status = "OK" if returncode == 0 else "UNREACHABLE" if classified["reason"].startswith("机器人无法连接") else "FAIL"
    execution_error = "" if returncode == 0 else classified["reason"]
    suggestion = "" if returncode == 0 else classified["suggestion"]
    latest_output = tail_lines(combined)
    return [
        {
            "inventory_hostname": robot["inventory_hostname"],
            "robot_id": robot.get("robot_id", ""),
            "ansible_host": robot.get("ansible_host", ""),
            "end_effector": robot.get("end_effector", ""),
            "mode": action,
            "overall_status": status,
            "execution_error": execution_error,
            "reason": execution_error,
            "suggestion": suggestion,
            "stdout": execution.get("stdout", ""),
            "stderr": execution.get("stderr", ""),
            "latest_output": latest_output,
            "command": execution.get("command", ""),
            "cwd": execution.get("cwd", ""),
            "returncode": returncode,
            "report_path": execution.get("report_path", ""),
            "log_file": execution.get("log_file", ""),
            "start_time": execution.get("start_time", ""),
            "end_time": execution.get("end_time", ""),
            "duration_seconds": execution.get("duration_seconds", ""),
        }
        for robot in robots
    ]


def build_single_execution_report(action: str, execution: dict[str, Any], robot: dict[str, str]) -> list[dict[str, Any]]:
    combined = "\n".join([str(execution.get("stdout", "")), str(execution.get("stderr", ""))])
    returncode = int(execution.get("returncode") or 0)
    classified = classify_error(combined)
    status = "OK" if returncode == 0 else "UNREACHABLE" if classified["reason"].startswith("机器人无法连接") else "FAIL"
    execution_error = "" if returncode == 0 else classified["reason"]
    return [
        {
            "inventory_hostname": robot.get("inventory_hostname", robot.get("ansible_host", "unknown")),
            "robot_id": robot.get("robot_id", ""),
            "ansible_host": robot.get("ansible_host", ""),
            "end_effector": robot.get("end_effector", ""),
            "mode": action,
            "overall_status": status,
            "execution_error": execution_error,
            "reason": execution_error,
            "suggestion": "" if returncode == 0 else classified["suggestion"],
            "stdout": execution.get("stdout", ""),
            "stderr": execution.get("stderr", ""),
            "latest_output": execution.get("latest_output", ""),
            "command": execution.get("command", ""),
            "cwd": execution.get("cwd", ""),
            "returncode": returncode,
            "report_path": execution.get("report_path", ""),
            "log_file": execution.get("log_file", ""),
            "start_time": execution.get("start_time", ""),
            "end_time": execution.get("end_time", ""),
            "duration_seconds": execution.get("duration_seconds", ""),
        }
    ]


def build_execution_failure_summary(action: str, execution: dict[str, Any]) -> list[dict[str, Any]]:
    return build_execution_summary(action, execution)


def action_timeout_seconds(action: str) -> int:
    defaults = {
        "check": 600,
        "install": 1800,
        "cos_setup": 1800,
        "service_check": 300,
        "service_restart": 300,
        CAMERA_CHECK_ACTION: 300,
        HAND_TEST_ACTION: 600,
        DEPLOY_ACTION: 1800,
        DEPLOY_SETUP_AGENT_ACTION: 3600,
    }
    return int(cfg("ansible", f"{action}_timeout_seconds", default=defaults.get(action, 900)))


def ssh_common_args() -> str:
    connect_timeout = int(cfg("ansible", "ssh_connect_timeout", default=5))
    return (
        "-o PreferredAuthentications=password -o PubkeyAuthentication=no "
        "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout={connect_timeout} -o ConnectionAttempts=1"
    )


def ssh_key_common_args() -> list[str]:
    connect_timeout = int(cfg("ansible", "ssh_connect_timeout", default=5))
    return [
        "-o",
        "BatchMode=yes",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PubkeyAuthentication=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "ConnectionAttempts=1",
    ]


def service_detail_names_for_robot(robot: dict[str, str]) -> list[str]:
    names = list(SERVICE_DETAIL_BASE_NAMES)
    hand_service = SERVICE_DETAIL_HAND_NAMES.get(str(robot.get("end_effector", "")).strip().lower())
    if hand_service:
        names.append(hand_service)
    return names


def service_detail_script(service_names: list[str]) -> str:
    service_args = " ".join(shlex.quote(name) for name in service_names)
    return f"""set +e
for service in {service_args}; do
  printf '__SERVICE_BEGIN__ name=%s\\n' "$service"
  systemctl show "$service" --property=LoadState,ActiveState,UnitFileState,MainPID --no-pager 2>&1
  show_rc=$?
  printf '__SERVICE_SHOW_RC__ %s\\n' "$show_rc"
  printf '__SERVICE_END__\\n'
done
"""


def service_detail_command(robot: dict[str, str], ssh_password: str) -> list[str]:
    host = str(robot.get("ansible_host", "")).strip()
    if not host:
        raise ValueError("机器人未配置 ansible_host")
    remote_cmd = "bash -lc " + shlex.quote(service_detail_script(service_detail_names_for_robot(robot)))
    if ssh_password:
        args = shlex.split(ssh_common_args())
    else:
        args = ssh_key_common_args()
    return ["ssh", *args, f"unitree@{host}", remote_cmd]


def camera_binding_script(operation: str) -> str:
    if operation == "inspect":
        return """for d in /dev/video*; do
  [ -e "$d" ] || continue
  echo "===== $d ====="
  udevadm info -q property -n "$d" 2>/dev/null | grep -E "DEVNAME|ID_PATH|ID_V4L_PRODUCT|ID_SERIAL"
done
"""
    if operation == "reload":
        sudo_prompt = "[sudo] password: "
        return (
            f"sudo -S -p {shlex.quote(sudo_prompt)} udevadm control --reload-rules && "
            f"sudo -S -p {shlex.quote(sudo_prompt)} udevadm trigger && "
            "ls -la /dev/cam_*"
        )
    raise ValueError("未知摄像头绑定操作")


def camera_binding_command(robot: dict[str, str], ssh_password: str, operation: str) -> list[str]:
    host = str(robot.get("ansible_host", "")).strip()
    if not host:
        raise ValueError("机器人未配置 ansible_host")
    remote_cmd = "bash -lc " + shlex.quote(camera_binding_script(operation))
    args = shlex.split(ssh_common_args()) if ssh_password else ssh_key_common_args()
    return ["ssh", *args, f"unitree@{host}", remote_cmd]


def run_ssh_capture(cmd: list[str], ssh_password: str, sudo_password: str = "", timeout_seconds: float = SERVICE_QUERY_TIMEOUT_SECONDS) -> tuple[int, str]:
    deadline = time.time() + timeout_seconds
    prompt_timeout = min(5, int(cfg("ansible", "prompt_timeout", default=15)))
    child = pexpect.spawn(cmd[0], cmd[1:], cwd=str(ROOT_DIR), encoding="utf-8", timeout=prompt_timeout)
    output: list[str] = []
    ssh_prompt_attempts = 0
    sudo_prompt_attempts = 0
    generic_password_attempts = 0
    try:
        while True:
            index = child.expect(PASSWORD_PROMPTS)
            if child.before:
                output.append(child.before)
            if index in SSH_PROMPT_INDEXES:
                if not ssh_password:
                    child.terminate(force=True)
                    raise PermissionError("详情服务查询需要 SSH 密码或免密 SSH。")
                ssh_prompt_attempts += 1
                if ssh_prompt_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                    child.terminate(force=True)
                    raise PermissionError("SSH 密码错误，请确认 unitree 密码。")
                child.sendline(ssh_password)
            elif index in BECOME_PROMPT_INDEXES:
                sudo_prompt_attempts += 1
                if sudo_prompt_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                    child.terminate(force=True)
                    raise PermissionError("sudo 密码未被正确识别或认证失败。")
                child.sendline(sudo_password or ssh_password)
            elif index == GENERIC_PASSWORD_PROMPT_INDEX:
                password = sudo_password if (ssh_prompt_attempts or sudo_prompt_attempts) and sudo_password else ssh_password
                if not password:
                    child.terminate(force=True)
                    raise PermissionError("详情服务查询需要 SSH 密码或免密 SSH。")
                generic_password_attempts += 1
                if generic_password_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                    child.terminate(force=True)
                    raise PermissionError("密码认证失败。请检查 SSH 密码和 sudo 密码是否正确。")
                child.sendline(password)
            elif index == HOST_KEY_PROMPT_INDEX:
                child.sendline("yes")
            elif index == TIMEOUT_PROMPT_INDEX:
                if not child.isalive():
                    child.close()
                    break
                if time.time() > deadline:
                    child.terminate(force=True)
                    raise TimeoutError("服务状态查询超时。")
                continue
            elif index == EOF_PROMPT_INDEX:
                break
    finally:
        child.close()
    returncode = child.exitstatus if child.exitstatus is not None else child.signalstatus or 1
    return returncode, "".join(output)


def parse_camera_binding_output(output: str) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in output.splitlines():
        match = re.fullmatch(r"===== (.+) =====", line.strip())
        if match:
            current = {"device": match.group(1), "properties": {}}
            devices.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if any(property_key in key for property_key in CAMERA_BINDING_PROPERTY_KEYS):
            current["properties"][key] = value.strip()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        properties = device["properties"]
        product = str(properties.get("ID_V4L_PRODUCT") or "未识别 ID_V4L_PRODUCT")
        grouped.setdefault(product, []).append(device)

    categories: list[dict[str, Any]] = []
    for index, (product, items) in enumerate(grouped.items(), start=1):
        copy_sections: list[str] = []
        for item in items:
            lines = [f"===== {item['device']} ====="]
            lines.extend(f"{key}={value}" for key, value in item["properties"].items())
            copy_sections.append("\n".join(lines))
        categories.append(
            {
                "index": index,
                "product": product,
                "device_count": len(items),
                "devices": items,
                "copy_text": "\n".join(copy_sections),
            }
        )
    return categories


def selected_camera_binding_robot(selected: list[str]) -> dict[str, str]:
    if len([item for item in selected if str(item).strip()]) != 1:
        raise ValueError("摄像头绑定检查一次只能选择 1 台机器人")
    robots = selected_inventory_robots(selected)
    if len(robots) != 1:
        raise ValueError("摄像头绑定检查一次只能选择 1 台机器人")
    return robots[0]


def camera_binding_payload(
    operation: str,
    selected: list[str],
    ssh_password: str,
    sudo_password: str,
) -> dict[str, Any]:
    robot = selected_camera_binding_robot(selected)
    cmd = camera_binding_command(robot, ssh_password, operation)
    returncode, output = run_ssh_capture(
        cmd,
        ssh_password=ssh_password,
        sudo_password=sudo_password,
        timeout_seconds=30.0,
    )
    if returncode != 0 and ("Permission denied" in output or "publickey" in output):
        raise PermissionError("SSH 认证失败，请确认 unitree 密码。")

    base = {
        "inventory_hostname": robot["inventory_hostname"],
        "robot_id": robot.get("robot_id", ""),
        "ansible_host": robot.get("ansible_host", ""),
        "checked_at": now_iso(),
        "returncode": returncode,
        "output": output.strip(),
    }
    if operation == "inspect":
        categories = parse_camera_binding_output(output)
        device_count = sum(int(item["device_count"]) for item in categories)
        if returncode != 0 and not categories:
            raise RuntimeError(f"摄像头信息读取失败，返回码：{returncode}。{tail_lines(output, 8)}")
        return {
            **base,
            "status": "OK" if len(categories) == 3 else "WARN",
            "category_count": len(categories),
            "device_count": device_count,
            "categories": categories,
            "rule_path": CAMERA_BINDING_RULE_PATH,
        }
    return {
        **base,
        "status": "OK" if returncode == 0 else "FAIL",
        "command": "sudo udevadm control --reload-rules && sudo udevadm trigger && ls -la /dev/cam_*",
    }


def parse_marker_value(line: str, key: str) -> str:
    match = re.search(rf"(?:^| ){re.escape(key)}=([^ ]*)", line)
    return match.group(1) if match else ""


def parse_service_detail_output(output: str, service_names: list[str]) -> list[dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    in_status = False
    for line in output.splitlines():
        if line.startswith("__SERVICE_BEGIN__"):
            name = parse_marker_value(line, "name")
            current = {"name": name, "show": {}, "status_lines": [], "show_rc": "", "status_rc": ""}
            in_status = False
            if name:
                sections[name] = current
            continue
        if current is None:
            continue
        if line.startswith("__SERVICE_SHOW_RC__"):
            current["show_rc"] = line.rsplit(" ", 1)[-1].strip()
            continue
        if line == "__SERVICE_STATUS_BEGIN__":
            in_status = True
            continue
        if line.startswith("__SERVICE_STATUS_RC__"):
            current["status_rc"] = line.rsplit(" ", 1)[-1].strip()
            in_status = False
            continue
        if line.startswith("__SERVICE_END__"):
            current = None
            in_status = False
            continue
        if in_status:
            current["status_lines"].append(line)
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            current["show"][key.strip()] = value.strip()

    return [normalize_service_detail(name, sections.get(name)) for name in service_names]


def normalize_service_detail(name: str, section: dict[str, Any] | None) -> dict[str, Any]:
    section = section or {}
    show = section.get("show") if isinstance(section.get("show"), dict) else {}
    load_state = str(show.get("LoadState") or "unknown")
    active_state = str(show.get("ActiveState") or "unknown")
    enabled_state = str(show.get("UnitFileState") or "unknown")
    pid_text = str(show.get("MainPID") or "0")
    pid = int(pid_text) if pid_text.isdigit() else 0
    enabled_ok_states = {"enabled", "enabled-runtime", "static", "generated", "indirect"}
    if load_state != "loaded":
        status = "FAIL"
        reason = f"{name} 未安装或未加载。"
    elif active_state == "active" and enabled_state in enabled_ok_states:
        status = "OK"
        reason = ""
    elif active_state == "active":
        status = "WARN"
        reason = f"{name} 正在运行，但 enabled_state={enabled_state}。"
    else:
        status = "FAIL"
        reason = f"{name} 未正常运行，active_state={active_state}。"
    return {
        "name": name,
        "load_state": load_state,
        "active_state": active_state,
        "enabled_state": enabled_state,
        "pid": pid,
        "status": status,
        "reason": reason,
    }


def robot_services_payload(hostname: str, ssh_password: str = "", sudo_password: str = "") -> dict[str, Any]:
    robot = find_inventory_robot(hostname)
    if not robot:
        raise ValueError("机器人不存在")
    service_names = service_detail_names_for_robot(robot)
    cmd = service_detail_command(robot, ssh_password)
    returncode, output = run_ssh_capture(cmd, ssh_password=ssh_password, sudo_password=sudo_password)
    if returncode != 0:
        if "Permission denied" in output or "publickey" in output:
            raise PermissionError("SSH 认证失败。请输入 SSH 密码，或确认已配置免密 SSH。")
        raise RuntimeError(f"服务状态查询失败，返回码：{returncode}。{tail_lines(output, 8)}")
    payload = {
        "inventory_hostname": robot["inventory_hostname"],
        "robot_id": robot.get("robot_id", ""),
        "ansible_host": robot.get("ansible_host", ""),
        "service_names": service_names,
        "checked_at": now_iso(),
        "refresh_seconds": SERVICE_DETAIL_REFRESH_SECONDS,
        "services": parse_service_detail_output(output, service_names),
    }
    record_robot_service_status(payload)
    return payload


def refresh_robot_business_status(robot: dict[str, str], ssh_password: str = "", sudo_password: str = "") -> dict[str, Any]:
    hostname = str(robot.get("inventory_hostname") or "")
    try:
        payload = robot_services_payload(hostname, ssh_password=ssh_password, sudo_password=sudo_password)
        return {
            "inventory_hostname": hostname,
            "status": "OK",
            "checked_at": payload.get("checked_at", ""),
            "service_count": len(payload.get("services", [])),
            "reason": "",
        }
    except Exception as exc:  # noqa: BLE001 - keep refresh stable per robot
        reason = f"业务状态查询失败：{exc}"
        record_robot_service_query_error(robot, reason)
        return {
            "inventory_hostname": hostname,
            "status": "QUERY_FAILED",
            "checked_at": now_iso(),
            "service_count": 0,
            "reason": reason,
        }


def refresh_online_robot_business_status(
    robots: list[dict[str, str]],
    network_status: dict[str, dict[str, Any]],
    ssh_password: str = "",
    sudo_password: str = "",
) -> list[dict[str, Any]]:
    online_robots = [
        robot
        for robot in robots
        if str(network_status.get(robot["inventory_hostname"], {}).get("network_status", "")).upper() == "ONLINE"
    ]
    if not online_robots:
        return []

    results: list[dict[str, Any]] = []
    max_workers = refresh_status_max_workers(len(online_robots))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(refresh_robot_business_status, robot, ssh_password, sudo_password): robot
            for robot in online_robots
        }
        for future in as_completed(futures):
            robot = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - do not let one robot stop the batch
                reason = f"业务状态查询失败：{exc}"
                record_robot_service_query_error(robot, reason)
                results.append(
                    {
                        "inventory_hostname": str(robot.get("inventory_hostname") or ""),
                        "status": "QUERY_FAILED",
                        "checked_at": now_iso(),
                        "service_count": 0,
                        "reason": reason,
                    }
                )
    results.sort(key=lambda item: item.get("inventory_hostname", ""))
    return results


def refresh_latest_report_with_status(ssh_password: str = "", sudo_password: str = "") -> dict[str, Any]:
    if not REFRESH_LOCK.acquire(blocking=False):
        raise RuntimeError("正在刷新最新报告，请稍后再试。")
    started_at = now_iso()
    try:
        robots = parse_inventory()
        network_status = refresh_robot_network_status_once()
        business_results = refresh_online_robot_business_status(
            robots,
            network_status,
            ssh_password=ssh_password,
            sudo_password=sudo_password,
        )
        payload = latest_report_payload()
        robot_statuses = robots_status_payload()
        online_count = sum(
            1
            for robot in robots
            if str(network_status.get(robot["inventory_hostname"], {}).get("network_status", "")).upper() == "ONLINE"
        )
        failed_business = [item for item in business_results if item.get("status") != "OK"]
        payload["robot_statuses"] = robot_statuses.get("robots", [])
        payload["refresh"] = {
            "started_at": started_at,
            "finished_at": now_iso(),
            "network_checked": len(robots),
            "network_online": online_count,
            "network_offline": max(0, len(robots) - online_count),
            "business_checked": len(business_results),
            "business_failed": len(failed_business),
            "business_results": business_results,
        }
        return payload
    finally:
        REFRESH_LOCK.release()


def service_action_playbook(action: str, service_name: str = "", camera_topic: str = "") -> Path:
    playbook_path = Path("/tmp") / f"operator_console_{action}.yml"
    camera_topics = [dict(item) for item in CAMERA_TOPIC_SPECS if not camera_topic or item["topic"] == camera_topic]
    restart_line = ""
    if action == "service_restart":
        restart_line = f"""
    - name: Restart selected service
      become: true
      ansible.builtin.shell: |
        systemctl restart {service_name}
        sleep 3
        systemctl is-active {service_name}
      args:
        executable: /bin/bash
      register: service_restart_result
      changed_when: service_restart_result.rc == 0
      failed_when: false
"""
    content = f"""---
- name: Operator console service action
  hosts: g1_robots
  gather_facts: false
  become: false
  ignore_unreachable: true

  vars:
    service_names: {json.dumps(SERVICE_CHECK_NAMES, ensure_ascii=False)}
    service_report_file: "{(ROOT_REPORTS_DIR / "g1_env_summary.json")}"
    service_action: "{action}"
    service_restart_name: "{service_name}"
    camera_check_topic: {json.dumps(camera_topic, ensure_ascii=False)}
    camera_topic_min_rate_hz: {CAMERA_TOPIC_MIN_RATE_HZ:.1f}
    camera_topic_sample_seconds: 8
    camera_topic_extend_seconds: 4
    camera_topic_max_sample_seconds: 40
    camera_topic_growth_epsilon_hz: 0.05
    camera_status_default:
      cam_head: 0
      cam_left: 0
      cam_right: 0
    should_check_services: "{{{{ service_action != 'camera_check' }}}}"
    should_check_camera_topics: >-
      {{{{
        service_action == 'camera_check'
        or
        (
          service_action == 'service_check'
          and (
            (service_restart_name | default('') | length) == 0
            or service_restart_name == 'cos_teleop.service'
          )
        )
        or service_restart_name == 'cos_teleop.service'
      }}}}
    camera_topics: {json.dumps(camera_topics, ensure_ascii=False)}

  tasks:
{restart_line}
    - name: Select services for this robot
      ansible.builtin.set_fact:
        selected_service_names: >-
          {{{{
            []
            if service_action == 'camera_check'
            else [service_restart_name]
            if (service_restart_name | default('') | length) > 0
            else (
              ['cos_agent.service', 'cos_teleop.service', 'xrobotoolkit-pc-service.service']
              + (['dex1_gripper.service'] if (end_effector | default('none') | lower) == 'dex1' else [])
              + (['brainco_hand.service'] if (end_effector | default('none') | lower) == 'brainco' else [])
            )
          }}}}

    - name: 初始化服务检查结果
      ansible.builtin.set_fact:
        service_result_map: {{}}
        service_check_items: []

    - name: 检查服务
      become: true
      ansible.builtin.shell: |
        set +e
        service="{{{{ item }}}}"
        restart_rc=""
        log_issue=0
        health_ok=0
        detail="not checked"
        check_service() {{
          enabled="$(systemctl is-enabled "$service" 2>&1)"
          enabled_rc=$?
          active="$(systemctl is-active "$service" 2>&1)"
          active_rc=$?
          status="$(systemctl status "$service" --no-pager -l 2>&1)"
          status_rc=$?
          if [ "$service" = "dex1_gripper.service" ] || [ "$service" = "brainco_hand.service" ]; then
            journal="$(journalctl -u "$service" -n 60 --no-pager -o cat 2>&1)"
          elif [ "$service" = "cos_teleop.service" ]; then
            journal="$(journalctl -u "$service" -n 60 --no-pager -o cat 2>&1)"
          else
            journal="$(journalctl -u "$service" -n 60 --no-pager 2>&1)"
          fi
          if printf '%s\\n' "$status" | grep -q 'Loaded: loaded'; then loaded=1; else loaded=0; fi
        }}
        evaluate_health() {{
          log_issue=0
          health_ok=0
          detail="not active"
          if [ "{{{{ service_action }}}}" = "service_restart" ]; then
            if [ "$active_rc" -eq 0 ]; then
              health_ok=1
              detail="active"
            else
              detail="not active"
            fi
            return
          fi
          if [ "$service" = "xrobotoolkit-pc-service.service" ]; then
            if [ "$loaded" -eq 1 ] && [ "$enabled_rc" -eq 0 ]; then
              health_ok=1
              detail="loaded/enabled"
            else
              detail="not loaded or disabled"
            fi
            return
          fi
          if [ "$loaded" -ne 1 ]; then
            detail="not loaded"
            return
          fi
          if [ "$enabled_rc" -ne 0 ]; then
            detail="disabled"
            return
          fi
          if [ "$active_rc" -ne 0 ]; then
            detail="not active"
            return
          fi
          if [ "$service" = "dex1_gripper.service" ]; then
            dex1_startup_ok=0
            dex1_runtime_ok=0
            if printf '%s\\n' "$journal" | grep -q 'Available Serial Ports' &&
               printf '%s\\n' "$journal" | grep -q 'Detected motors' &&
               printf '%s\\n' "$journal" | grep -q 'Side: right' &&
               printf '%s\\n' "$journal" | grep -q 'Side: left' &&
               printf '%s\\n' "$journal" | grep -Eq 'Dex1-1 Gripper Server.*started'; then
              dex1_startup_ok=1
            fi
            if printf '%s\\n' "$journal" | grep -Eq 'No DDS command received on rt/dex1/right/cmd.*motor 0.*publishing state' &&
               printf '%s\\n' "$journal" | grep -Eq 'No DDS command received on rt/dex1/left/cmd.*motor 1.*publishing state'; then
              dex1_runtime_ok=1
            fi
            if [ "$dex1_startup_ok" -eq 1 ] || [ "$dex1_runtime_ok" -eq 1 ]; then
              health_ok=1
              detail="active/enabled/motors bound"
            else
              log_issue=1
              detail="active but motor binding not confirmed"
            fi
            return
          fi
          if [ "$service" = "brainco_hand.service" ]; then
            if printf '%s\\n' "$journal" | grep -qi 'left hand bound' &&
               printf '%s\\n' "$journal" | grep -qi 'right hand bound' &&
               printf '%s\\n' "$journal" | grep -qi 'Starting worker for left' &&
               printf '%s\\n' "$journal" | grep -qi 'Starting worker for right'; then
              health_ok=1
              detail="active/enabled/hands bound"
            else
              log_issue=1
              detail="active but hand binding not confirmed"
            fi
            return
          fi
          if [ "$service" = "cos_teleop.service" ] && printf '%s\\n' "$journal" | grep -Eiq 'Failed to read direct MJPEG frame|Failed to open V4L2 device|Failed to initialize V4L2|Failed to initialize V4L2 direct MJPEG capture|Camera initialization failed|No such file or directory'; then
            log_issue=1
            detail="cos_teleop journal has recent camera initialization error"
            return
          fi
          health_ok=1
          detail="active/loaded/enabled"
        }}
        check_service
        evaluate_health
        if [ "{{{{ service_action }}}}" = "service_restart" ] && [ "$service" != "xrobotoolkit-pc-service.service" ] && {{ [ "$active_rc" -ne 0 ] || [ "$log_issue" -eq 1 ]; }}; then
          systemctl restart "$service"
          restart_rc=$?
          sleep 3
          check_service
          evaluate_health
          if [ "$health_ok" -eq 1 ]; then
            detail="service recovered after restart"
          elif [ "$log_issue" -eq 1 ]; then
            detail="service journal still has health error after restart"
          fi
        fi
        printf '__SERVICE_META__ name=%s loaded=%s enabled_rc=%s active_rc=%s status_rc=%s log_issue=%s health_ok=%s restart_rc=%s detail=%s\\n' "$service" "$loaded" "$enabled_rc" "$active_rc" "$status_rc" "$log_issue" "$health_ok" "$restart_rc" "$detail"
        printf '%s\\n' "--- is-enabled ---" "$enabled"
        printf '%s\\n' "--- is-active ---" "$active"
        printf '%s\\n' "--- status ---" "$status"
        printf '%s\\n' "--- journal ---" "$journal"
        exit 0
      args:
        executable: /bin/bash
      loop: "{{{{ selected_service_names }}}}"
      loop_control:
        label: "{{{{ item }}}}"
      register: service_checks
      changed_when: false
      failed_when: false

    - name: Build service result map
      ansible.builtin.set_fact:
        service_result_map: >-
          {{{{
            service_result_map | default({{}}) | combine({{
              item.item: {{
                'name': item.item,
                'stdout': item.stdout | default(''),
                'stderr': item.stderr | default(''),
                'loaded': item.stdout is search('__SERVICE_META__ .*loaded=1'),
                'enabled': item.stdout is search('__SERVICE_META__ .*enabled_rc=0'),
                'active': item.stdout is search('__SERVICE_META__ .*active_rc=0'),
                'log_issue': item.stdout is search('__SERVICE_META__ .*log_issue=1'),
                'health_ok': item.stdout is search('__SERVICE_META__ .*health_ok=1'),
                'restart_rc': item.stdout | regex_search('__SERVICE_META__ .*restart_rc=([^\\n ]*)', '\\1') | default(''),
                'camera_error': (
                  item.item == 'cos_teleop.service' and
                  ((item.stdout | default('') | lower) is regex('failed to read direct mjpeg frame|failed to open v4l2 device|failed to initialize v4l2|failed to initialize v4l2 direct mjpeg capture|camera initialization failed|no such file or directory'))
                ),
                'status': (
                  'WARN'
                  if (
                    (item.stdout is search('__SERVICE_META__ .*log_issue=1')) and
                    (item.stdout is search('__SERVICE_META__ .*loaded=1')) and
                    (item.stdout is search('__SERVICE_META__ .*enabled_rc=0')) and
                    (item.stdout is search('__SERVICE_META__ .*active_rc=0'))
                  )
                  else 'OK'
                  if (
                    (item.stdout is search('__SERVICE_META__ .*health_ok=1'))
                  )
                  else 'FAIL'
                ),
                'detail': (
                  'service recovered after restart'
                  if (item.stdout is search('__SERVICE_META__ .*health_ok=1')) and (item.stdout is search('__SERVICE_META__ .*restart_rc=[0-9]+'))
                  else 'service journal still has health error after restart'
                  if (item.stdout is search('__SERVICE_META__ .*log_issue=1')) and item.item in ['dex1_gripper.service', 'brainco_hand.service']
                  else 'cos_teleop journal has recent camera initialization error'
                  if (
                    item.item == 'cos_teleop.service' and
                    ((item.stdout | default('') | lower) is regex('failed to read direct mjpeg frame|failed to open v4l2 device|failed to initialize v4l2|failed to initialize v4l2 direct mjpeg capture|camera initialization failed|no such file or directory'))
                  )
                  else 'loaded/enabled'
                  if item.item == 'xrobotoolkit-pc-service.service' and (item.stdout is search('__SERVICE_META__ .*health_ok=1'))
                  else 'active/loaded/enabled'
                  if (item.stdout is search('__SERVICE_META__ .*health_ok=1'))
                  else 'not loaded'
                  if not (item.stdout is search('__SERVICE_META__ .*loaded=1'))
                  else 'disabled'
                  if not (item.stdout is search('__SERVICE_META__ .*enabled_rc=0'))
                  else 'not active'
                ),
                'reason': (
                  'Dex1 服务已启动，但未确认左右手电机绑定成功。'
                  if item.item == 'dex1_gripper.service' and (item.stdout is search('__SERVICE_META__ .*log_issue=1'))
                  else 'Brainco 服务已启动，但未确认左右手绑定成功。'
                  if item.item == 'brainco_hand.service' and (item.stdout is search('__SERVICE_META__ .*log_issue=1'))
                  else 'cos_teleop 日志中发现相机读取异常，建议重启 cos_teleop.service。'
                  if item.item == 'cos_teleop.service' and (item.stdout is search('__SERVICE_META__ .*log_issue=1'))
                  else item.item ~ ' 未安装或未加载。'
                  if not (item.stdout is search('__SERVICE_META__ .*loaded=1'))
                  else item.item ~ ' 未设置开机自启。'
                  if not (item.stdout is search('__SERVICE_META__ .*enabled_rc=0'))
                  else item.item ~ ' 无法启动，请检查设备。'
                  if not (item.stdout is search('__SERVICE_META__ .*active_rc=0'))
                  else ''
                ),
                'suggestion': (
                  '请尝试重启 dex1_gripper.service；如果仍异常，请检查 Dex1 有线连接、串口识别和硬件状态。'
                  if item.item == 'dex1_gripper.service' and (item.stdout is search('__SERVICE_META__ .*log_issue=1'))
                  else '请尝试重启 brainco_hand.service；如果仍异常，请检查 Brainco 有线连接和硬件状态。'
                  if item.item == 'brainco_hand.service' and (item.stdout is search('__SERVICE_META__ .*log_issue=1'))
                  else '请尝试重启 cos_teleop.service；如果仍异常，请检查相机 Type-C 连接、udev 映射、相机设备状态或相机是否被占用。'
                  if item.item == 'cos_teleop.service' and (item.stdout is search('__SERVICE_META__ .*log_issue=1'))
                  else '请尝试服务重启；如果仍异常，请重新执行“执行 cos_setup.sh”。'
                )
              }}
            }})
          }}}}
      loop: "{{{{ service_checks.results | default([]) }}}}"
      loop_control:
        label: "{{{{ item.item }}}}"

    - name: Build service check items
      ansible.builtin.set_fact:
        service_check_items: >-
          {{{{
            service_result_map | dict2items | map(attribute='value') | list
          }}}}

    - name: 启动相机频率检查
      ansible.builtin.shell: |
        set +e
        export HOME=/home/unitree
        export ROS_VERSION=2
        export ROS_DISTRO=foxy
        export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
        export CYCLONEDDS_URI=file:///home/unitree/unitree_ros2/cyclonedds_ws/src/cyclonedds.xml
        export PYTHONUNBUFFERED=1
        source /opt/ros/foxy/setup.bash
        if [ -f /home/unitree/unitree_ros2/cyclonedds_ws/install/setup.bash ]; then
          source /home/unitree/unitree_ros2/cyclonedds_ws/install/setup.bash
        fi
        if [ -f /home/unitree/cos_ws/install/setup.bash ]; then
          source /home/unitree/cos_ws/install/setup.bash
        fi
        topic="{{{{ item.topic }}}}"
        device="{{{{ item.device | default('') }}}}"
        min_rate="{{{{ camera_topic_min_rate_hz }}}}"
        sample_seconds="{{{{ camera_topic_sample_seconds | int }}}}"
        extend_seconds="{{{{ camera_topic_extend_seconds | int }}}}"
        max_sample_seconds="{{{{ camera_topic_max_sample_seconds | int }}}}"
        growth_epsilon="{{{{ camera_topic_growth_epsilon_hz }}}}"
        if [ -n "$device" ] && [ ! -e "$device" ]; then
          printf 'camera_device_missing: %s\\n' "$device"
          exit 2
        fi
        hz_output_file="$(mktemp)"
        ros2 topic hz "$topic" > "$hz_output_file" 2>&1 &
        hz_pid=$!
        cleanup_hz() {{
          if kill -0 "$hz_pid" >/dev/null 2>&1; then
            kill "$hz_pid" >/dev/null 2>&1 || true
            wait "$hz_pid" >/dev/null 2>&1 || true
          fi
          rm -f "$hz_output_file"
        }}
        trap cleanup_hz EXIT
        sleep "$sample_seconds"
        elapsed_seconds="$sample_seconds"
        hz_rates="$(sed -n 's/.*average rate:[[:space:]]*\\([0-9][0-9.]*\\).*/\\1/p' "$hz_output_file")"
        hz_rate="$(printf '%s\\n' "$hz_rates" | tail -n 1)"
        previous_rate="$(printf '%s\\n' "$hz_rates" | tail -n 2 | head -n 1)"
        adaptive_decision="initial_sample"

        if [ -z "$hz_rate" ]; then
          adaptive_decision="no_data"
        elif awk "BEGIN {{ exit !($hz_rate >= $min_rate) }}"; then
          adaptive_decision="passed_initial"
        elif [ -n "$previous_rate" ] && awk "BEGIN {{ exit !($hz_rate > $previous_rate + $growth_epsilon) }}"; then
          adaptive_decision="rising"
          while [ "$elapsed_seconds" -lt "$max_sample_seconds" ]; do
            remaining_seconds=$((max_sample_seconds - elapsed_seconds))
            next_sleep="$extend_seconds"
            if [ "$remaining_seconds" -lt "$next_sleep" ]; then
              next_sleep="$remaining_seconds"
            fi
            previous_rate="$hz_rate"
            sleep "$next_sleep"
            elapsed_seconds=$((elapsed_seconds + next_sleep))
            next_rate="$(sed -n 's/.*average rate:[[:space:]]*\\([0-9][0-9.]*\\).*/\\1/p' "$hz_output_file" | tail -n 1)"
            if [ -z "$next_rate" ] || ! awk "BEGIN {{ exit !($next_rate > $previous_rate + $growth_epsilon) }}"; then
              [ -n "$next_rate" ] && hz_rate="$next_rate"
              adaptive_decision="stopped_not_rising"
              break
            fi
            hz_rate="$next_rate"
            if awk "BEGIN {{ exit !($hz_rate >= $min_rate) }}"; then
              adaptive_decision="recovered"
              break
            fi
            adaptive_decision="rising_until_limit"
          done
        else
          adaptive_decision="stopped_not_rising"
        fi

        if kill -0 "$hz_pid" >/dev/null 2>&1; then
          kill "$hz_pid" >/dev/null 2>&1 || true
          wait "$hz_pid" >/dev/null 2>&1 || true
          hz_rc=124
        else
          wait "$hz_pid"
          hz_rc=$?
        fi
        hz_output="$(cat "$hz_output_file" 2>/dev/null || true)"
        printf '%s\\n' "$hz_output"
        printf '__CAMERA_HZ_ADAPTIVE__ rate_hz=%s sampled_seconds=%s decision=%s\\n' "${{hz_rate:-0}}" "$elapsed_seconds" "$adaptive_decision"
        trap - EXIT
        rm -f "$hz_output_file"
        if [ -z "$hz_rate" ]; then
          printf 'ros_hz_warning: no average rate received on %s rc=%s\\n' "$topic" "$hz_rc"
          exit 1
        fi
        if awk "BEGIN {{ exit !($hz_rate >= $min_rate) }}"; then
          exit 0
        fi
        printf 'ros_hz_warning: average rate %s Hz below minimum %s Hz on %s rc=%s\\n' "$hz_rate" "$min_rate" "$topic" "$hz_rc"
        exit 1
      args:
        executable: /bin/bash
      loop: "{{{{ camera_topics }}}}"
      loop_control:
        label: "{{{{ item.label }}}}（{{{{ item.topic }}}}）"
      async: 55
      poll: 0
      register: camera_topic_hz_jobs
      changed_when: false
      failed_when: false
      when: should_check_camera_topics | bool

    - name: 等待相机频率检查完成
      ansible.builtin.async_status:
        jid: "{{{{ item.ansible_job_id }}}}"
      loop: "{{{{ camera_topic_hz_jobs.results | default([]) | selectattr('ansible_job_id', 'defined') | list }}}}"
      loop_control:
        label: "{{{{ item.item.label | default('相机') }}}}（{{{{ item.item.topic | default(item.ansible_job_id) }}}}）"
      register: camera_topic_hz_checks
      until: camera_topic_hz_checks.finished
      retries: 50
      delay: 1
      changed_when: false
      failed_when: false
      when: should_check_camera_topics | bool

    - name: 初始化相机频率原始结果
      ansible.builtin.set_fact:
        camera_topic_hz_results: []
      when: should_check_camera_topics | bool

    - name: 整理相机频率并发结果
      ansible.builtin.set_fact:
        camera_topic_hz_results: "{{{{ camera_topic_hz_results + [camera_topic_hz_result] }}}}"
      vars:
        camera_topic_hz_result:
          item: "{{{{ item.item.item | default(item.item) }}}}"
          stdout: "{{{{ item.stdout | default('') }}}}"
          stderr: "{{{{ item.stderr | default('') }}}}"
          rc: "{{{{ item.rc | default(1) }}}}"
      loop: "{{{{ camera_topic_hz_checks.results | default([]) }}}}"
      loop_control:
        label: "{{{{ item.item.item.label | default(item.item.item.topic | default(item.item.ansible_job_id | default('camera topic'))) }}}}"
      when: should_check_camera_topics | bool

    - name: 初始化相机频率结果
      ansible.builtin.set_fact:
        camera_topic_results: []

    - name: 记录相机频率状态
      ansible.builtin.set_fact:
        camera_topic_results: "{{{{ camera_topic_results + [camera_topic_result] }}}}"
      vars:
        camera_topic_rate_matches: "{{{{ item.stdout | default('') | regex_findall('average rate:\\\\s*([0-9]+(?:\\\\.[0-9]+)?)') }}}}"
        camera_topic_adaptive_rate_matches: "{{{{ item.stdout | default('') | regex_findall('__CAMERA_HZ_ADAPTIVE__ rate_hz=([0-9]+(?:\\\\.[0-9]+)?)') }}}}"
        camera_topic_rate_hz: "{{{{ (camera_topic_adaptive_rate_matches | last | default(camera_topic_rate_matches | last | default('0', true), true)) | float }}}}"
        camera_topic_sample_matches: "{{{{ item.stdout | default('') | regex_findall('__CAMERA_HZ_ADAPTIVE__ rate_hz=[0-9]+(?:\\\\.[0-9]+)? sampled_seconds=([0-9]+)') }}}}"
        camera_topic_decision_matches: "{{{{ item.stdout | default('') | regex_findall('__CAMERA_HZ_ADAPTIVE__ rate_hz=[0-9]+(?:\\\\.[0-9]+)? sampled_seconds=[0-9]+ decision=([a-z_]+)') }}}}"
        camera_topic_sampled_seconds: "{{{{ (camera_topic_sample_matches | last | default(camera_topic_sample_seconds, true)) | int }}}}"
        camera_topic_adaptive_decision: "{{{{ camera_topic_decision_matches | last | default('initial_sample', true) }}}}"
        camera_topic_ok: "{{{{ camera_topic_rate_hz | float >= camera_topic_min_rate_hz | default(0.0) | float }}}}"
        camera_topic_low_rate: "{{{{ camera_topic_rate_hz | float > 0 and camera_topic_rate_hz | float < camera_topic_min_rate_hz | default(0.0) | float }}}}"
        camera_topic_status: "{{{{ 'OK' if camera_topic_ok | bool else 'WARN' if camera_topic_low_rate | bool else 'FAIL' }}}}"
        camera_topic_reason: "{{{{ '摄像头 topic 帧率不足' if camera_topic_low_rate | bool else 'camera topic no data' }}}}"
        camera_topic_suggestion: "{{{{ '检查' ~ item.item.label ~ '帧率是否稳定，确认相机连接、系统负载和 cos_teleop.service 状态。' if camera_topic_low_rate | bool else '检查' ~ item.item.label ~ ' Type-C 连接或重启 cos_teleop.service' }}}}"
        camera_topic_detail: >-
          {{{{
            'average rate: ' ~ camera_topic_rate_hz ~ ' Hz; adaptive sample: ' ~ camera_topic_sampled_seconds ~ 's (' ~ camera_topic_adaptive_decision ~ ')'
            if camera_topic_ok | bool
            else 'average rate: ' ~ camera_topic_rate_hz ~ ' Hz below minimum ' ~ camera_topic_min_rate_hz ~ ' Hz; adaptive sample: ' ~ camera_topic_sampled_seconds ~ 's (' ~ camera_topic_adaptive_decision ~ ')'
            if camera_topic_low_rate | bool
            else 'timeout ' ~ camera_topic_sample_seconds ~ 's ros2 topic hz ' ~ item.item.topic ~ ' did not receive messages'
          }}}}
        camera_topic_result: >-
          {{{{
            {{
              'topic': item.item.topic,
              'label': item.item.label,
              'status': camera_topic_status,
              'rate_hz': camera_topic_rate_hz | float,
              'sample_seconds': camera_topic_sampled_seconds | int,
              'adaptive_decision': camera_topic_adaptive_decision
            }}
            | combine({{}} if camera_topic_status == 'OK' else {{
              'reason': camera_topic_reason,
              'suggestion': camera_topic_suggestion,
              'detail': camera_topic_detail,
              'stdout': item.stdout | default(''),
              'stderr': item.stderr | default('')
            }})
          }}}}
      loop: "{{{{ camera_topic_hz_results | default([]) }}}}"
      when: should_check_camera_topics | bool

    - name: Build structured camera status map
      ansible.builtin.set_fact:
        camera_topic_by_topic: "{{{{ camera_topic_by_topic | default({{}}) | combine({{item.topic: item}}) }}}}"
      loop: "{{{{ camera_topic_results | default([]) }}}}"
      when: should_check_camera_topics | bool

    - name: 初始化标准化相机频率结果
      ansible.builtin.set_fact:
        camera_topic_results_normalized: []

    - name: 标准化相机频率结果
      ansible.builtin.set_fact:
        camera_topic_results_normalized: "{{{{ camera_topic_results_normalized + [camera_topic_result] }}}}"
      vars:
        existing_camera_topic: "{{{{ camera_topic_by_topic.get(item.topic, {{}}) }}}}"
        camera_topic_result: >-
          {{{{
            {{
              'topic': item.topic,
              'label': item.label,
              'status': existing_camera_topic.get('status', 'FAIL'),
              'rate_hz': existing_camera_topic.get('rate_hz', 0) | float,
              'sample_seconds': existing_camera_topic.get('sample_seconds', camera_topic_sample_seconds) | int,
              'adaptive_decision': existing_camera_topic.get('adaptive_decision', 'not_checked')
            }}
            | combine({{}} if existing_camera_topic.get('status', 'FAIL') == 'OK' else {{
              'reason': existing_camera_topic.get('reason', 'camera topic no data'),
              'suggestion': existing_camera_topic.get('suggestion', '检查' ~ item.label ~ ' Type-C 连接或重启 cos_teleop.service'),
              'detail': existing_camera_topic.get('detail', 'timeout ' ~ camera_topic_sample_seconds ~ 's ros2 topic hz ' ~ item.topic ~ ' did not return data'),
              'stdout': existing_camera_topic.get('stdout', ''),
              'stderr': existing_camera_topic.get('stderr', '')
            }})
          }}}}
      loop: "{{{{ camera_topics }}}}"
      when: should_check_camera_topics | bool

    - name: 替换为标准化相机频率结果
      ansible.builtin.set_fact:
        camera_topic_results: "{{{{ camera_topic_results_normalized }}}}"
      when: should_check_camera_topics | bool

    - name: 输出相机频率检查结果
      ansible.builtin.debug:
        msg: "__CAMERA_TOPIC_STATUS__ status={{{{ item.status }}}} topic={{{{ item.topic }}}} rate_hz={{{{ item.rate_hz | default(0) }}}}"
      loop: "{{{{ camera_topic_results | default([]) }}}}"
      loop_control:
        label: "{{{{ item.label | default('相机') }}}}（{{{{ item.topic | default('unknown') }}}}）"
      changed_when: false
      failed_when: false
      when: should_check_camera_topics | bool

    - name: Build structured camera status map
      ansible.builtin.set_fact:
        camera_status: >-
          {{{{
            camera_status | default({{}}) | combine({{
              (
                'cam_head'
                if item.topic == '/cam_head/compressed_image'
                else 'cam_left'
                if item.topic == '/cam_wrist_left/compressed_image'
                else 'cam_right'
              ): item.rate_hz | float
            }})
          }}}}
      loop: "{{{{ camera_topic_results | default([]) }}}}"
      when: should_check_camera_topics | bool

    - name: Initialize service issue lists
      ansible.builtin.set_fact:
        service_failed_items: []
        service_warn_items: []
        camera_failed_items: []

    - name: Build service issue lists
      ansible.builtin.set_fact:
        service_failed_items: >-
          {{{{
            service_result_map | dict2items
            | selectattr('value.status', 'equalto', 'FAIL')
            | map(attribute='key') | list
          }}}}
        service_warn_items: >-
          {{{{
            service_result_map | dict2items
            | selectattr('value.status', 'equalto', 'WARN')
            | map(attribute='key') | list
          }}}}
        camera_failed_items: >-
          {{{{
            camera_topic_results | default([])
            | rejectattr('status', 'equalto', 'OK')
            | map(attribute='topic') | list
          }}}}

    - name: Evaluate missing cameras
      ansible.builtin.set_fact:
        all_camera_missing: "{{{{ (should_check_camera_topics | bool) and ((camera_topic_results | default([]) | rejectattr('status', 'equalto', 'FAIL') | list | length) == 0) }}}}"

    - name: Build service subsystem statuses
      ansible.builtin.set_fact:
        system_services_status: "{{{{ 'SKIP' if not (should_check_services | bool) else 'FAIL' if service_failed_items | length > 0 else 'WARN' if service_warn_items | length > 0 else 'OK' }}}}"
        camera_streams_status: "{{{{ 'FAIL' if all_camera_missing | bool else 'WARN' if camera_failed_items | length > 0 else 'OK' if should_check_camera_topics | bool else 'SKIP' }}}}"

    - name: Build service final report
      ansible.builtin.set_fact:
        service_final_report:
          host: "{{{{ inventory_hostname }}}}"
          inventory_hostname: "{{{{ inventory_hostname }}}}"
          ansible_host: "{{{{ ansible_host | default(inventory_hostname) }}}}"
          robot_id: "{{{{ robot_id | default('unknown') }}}}"
          mode: "{{{{ service_action }}}}"
          camera_check_topic: "{{{{ camera_check_topic }}}}"
          system_services_status: "{{{{ system_services_status }}}}"
          camera_streams_status: "{{{{ camera_streams_status }}}}"
          overall_status: >-
            {{{{
              'FAIL'
              if (service_failed_items | length > 0 or (service_action in ['service_check', 'camera_check'] and all_camera_missing | bool))
              else 'WARN'
              if (service_warn_items | length > 0 or camera_failed_items | length > 0)
              else 'OK'
            }}}}
          summary: >-
            {{{{
              '相机帧率正常。'
              if service_action == 'camera_check' and camera_failed_items | length == 0
              else '相机帧率存在异常：' ~ (camera_failed_items | join(', '))
              if service_action == 'camera_check'
              else '服务状态正常。'
              if (service_failed_items | length == 0 and service_warn_items | length == 0 and camera_failed_items | length == 0)
              else '服务存在异常：' ~ ((service_failed_items + service_warn_items + camera_failed_items) | join(', '))
            }}}}
          services: "{{{{ service_result_map | default({{}}) }}}}"
          checks: "{{{{ service_result_map | dict2items | map(attribute='value') | list }}}}"
          camera_status: "{{{{ camera_status | default(camera_status_default) }}}}"
          camera_topics: "{{{{ camera_topic_results | default([]) }}}}"
          restart:
            service: "{{{{ service_restart_name }}}}"
            rc: "{{{{ service_restart_result.rc | default('') }}}}"
            stdout: "{{{{ service_restart_result.stdout | default('') }}}}"
            stderr: "{{{{ service_restart_result.stderr | default('') }}}}"

    - name: Ensure report directory exists
      ansible.builtin.file:
        path: "{ROOT_REPORTS_DIR}"
        state: directory
        mode: "0755"
      delegate_to: localhost
      run_once: true

    - name: Write service aggregate report
      ansible.builtin.copy:
        dest: "{{{{ service_report_file }}}}"
        content: |
          [
          {{% for host_name in ansible_play_hosts_all %}}
          {{{{ hostvars[host_name].service_final_report | default({{'inventory_hostname': host_name, 'overall_status': 'NO_REPORT'}}) | to_nice_json }}}}{{{{ ',' if not loop.last else '' }}}}
          {{% endfor %}}
          ]
        mode: "0644"
      delegate_to: localhost
      run_once: true
"""
    playbook_path.write_text(content, encoding="utf-8")
    return playbook_path


def build_ansible_command(
    action: str,
    robots: list[str],
    service_name: str = "",
    camera_topic: str = "",
) -> tuple[list[str], Path]:
    inventory = str(inventory_path())
    ansible_timeout = str(int(cfg("ansible", "ansible_timeout", default=15)))
    common = [
        ansible_playbook_executable(),
        "-k",
        "-K",
        "-T",
        ansible_timeout,
        "-i",
        inventory,
        "-e",
        json.dumps({"ansible_ssh_common_args": ssh_common_args()}, ensure_ascii=False),
    ]
    if action == "check":
        cmd = [
            *common,
            str(resolve_root_path(str(cfg("ansible", "check_playbook", default="check_base_env.yml")))),
            "-e",
            f"g1_report_dir={DEVELOPER_REPORTS_DIR}",
        ]
    elif action == "install":
        cmd = [
            *common,
            str(resolve_root_path(str(cfg("ansible", "check_playbook", default="check_base_env.yml")))),
            "-e",
            "g1_mode=install",
            "-e",
            f"g1_report_dir={DEVELOPER_REPORTS_DIR}",
        ]
    elif action == "cos_setup":
        cmd = [
            *common,
            str(resolve_root_path(str(cfg("ansible", "cos_setup_playbook", default="deploy_eva_robot检测/batch_cos_setup.yml")))),
            "-e",
            f"cos_setup_report_dir={RAW_REPORTS_DIR}",
        ]
    elif action in SERVICE_ACTIONS:
        cmd = [*common, str(service_action_playbook(action, service_name, camera_topic))]
    else:
        raise ValueError("未知操作")
    clean_robots = [item for item in robots if item]
    if clean_robots:
        cmd.extend(["--limit", ":".join(clean_robots)])
    return cmd, ROOT_DIR


def run_deploy_to_robot(ip: str, password: str, log_file: Path, append: bool = False) -> tuple[int, str, str]:
    robot_dir = ROOT_DIR / "deploy_eva_robot检测" / "robot"
    cmd = [
        "bash",
        "-c",
        f"export REMOTE_HOST={shlex.quote(ip)} && ./deploy_to_robot.sh",
    ]
    return run_pexpect_command(
        action=DEPLOY_ACTION,
        cmd=cmd,
        cwd=robot_dir,
        ssh_password=password,
        sudo_password=password,
        log_file=log_file,
        append=append,
    )


def set_job(**updates: Any) -> None:
    updates = dict(updates)
    explicit_stage_id = str(updates.pop("task_stage_id", "") or "")
    current_task = str(updates.pop("current_task", "") or "")
    stage_items = updates.pop("stage_items", {})
    with JOB_LOCK:
        job = current_job()
        starting_new = (
            updates.get("running") is True
            and updates.get("returncode") is None
            and "action" in updates
        )
        if starting_new:
            action = str(updates.get("action") or "")
            job.update(
                {
                    "stages": make_job_stages(action),
                    "current_stage": "",
                    "current_task": "",
                    "current_command": "",
                    "cancellable": False,
                    "cancel_requested": False,
                    "robot_label": "",
                    "hand_type": "",
                    "hand_side": "",
                    "hand_side_cn": "",
                }
            )
        job.update(updates)
        if current_task:
            job["current_task"] = current_task
        elif "message" in updates:
            job["current_task"] = str(updates.get("message") or "")
        if "cmd" in updates:
            job["current_command"] = str(updates.get("cmd") or "")
        if job.get("running"):
            stage_id = stage_id_for_job_update_locked(
                {
                    **updates,
                    "current_task": current_task or job.get("current_task", ""),
                },
                explicit_stage_id,
            )
            if stage_id:
                update_job_stage_locked(
                    stage_id,
                    task=current_task or str(updates.get("message") or job.get("current_task") or ""),
                    command=str(updates.get("cmd") or job.get("current_command") or ""),
                )
            if isinstance(stage_items, dict):
                merge_job_stage_items_by_stage_locked(stage_items)
        elif updates.get("running") is False:
            finalize_job_stages_locked(str(job.get("phase") or updates.get("phase") or ""))


def job_cancel_requested() -> bool:
    with JOB_LOCK:
        return bool(current_job(JOB_SCOPE_OPERATION).get("cancel_requested"))


def hand_test_command(robot: dict[str, str], side: str) -> tuple[list[str], Path, str]:
    hand_type = str(robot.get("end_effector", "")).strip().lower()
    host = str(robot.get("ansible_host", "")).strip()
    if side not in HAND_TEST_SIDES:
        raise ValueError("请选择左手或右手")
    if hand_type == "dex1":
        remote_dir = "/home/unitree/g1_setup/dex1_1_service/bin"
        side_arg = "-l" if side == "left" else "-r"
        remote_cmd = f"cd {remote_dir} && sudo ./test_dex1_1_gripper_server {side_arg}"
    elif hand_type == "brainco":
        remote_dir = "/home/unitree/g1_setup/brainco_hand_service/bin"
        remote_cmd = f"cd {remote_dir} && sudo ./test_brainco_hand_server {side}"
    else:
        raise ValueError("当前机器人未配置 dex1 或 brainco，无法执行手测试")
    cmd = ["ssh", "-tt", *shlex.split(ssh_common_args()), f"unitree@{host}", remote_cmd]
    return cmd, ROOT_DIR, remote_cmd


def selected_hand_test_robot(robots: list[str]) -> dict[str, str]:
    selected = selected_inventory_robots(robots)
    if len(selected) != 1:
        raise ValueError("验证手是否能动一次只能选择 1 台机器人")
    robot = selected[0]
    if str(robot.get("end_effector", "")).strip().lower() not in {"dex1", "brainco"}:
        raise ValueError("当前机器人 end_effector 不是 dex1 或 brainco，无法测试手")
    return robot


def cancel_current_job() -> dict[str, Any]:
    with JOB_LOCK:
        job = current_job(JOB_SCOPE_OPERATION)
        if not job.get("running"):
            return job_payload_locked(JOB_SCOPE_OPERATION)
        if job.get("action") != HAND_TEST_ACTION:
            raise ValueError("当前任务不支持从页面取消")
        job.update(
            {
                "cancel_requested": True,
                "message": "正在取消手测试，请稍候……",
                "phase": "cancelling",
                "cancellable": False,
            }
        )
    with PROCESS_LOCK:
        child = CURRENT_PROCESS.get("child")
    if child is not None and child.isalive():
        try:
            child.sendcontrol("c")
        except Exception:
            pass
        try:
            child.terminate(force=False)
        except Exception:
            pass
    with JOB_LOCK:
        return job_payload_locked(JOB_SCOPE_OPERATION)


def update_job_from_log(log_file: Path, fallback: str = "任务仍在执行，请稍候……") -> dict[str, Any]:
    if not log_file.exists():
        set_job(message=fallback, phase="running")
        return {"message": fallback, "phase": "running"}
    text = log_file.read_text(encoding="utf-8", errors="replace")
    progress = parse_ansible_progress(text)
    set_job(
        message=progress.get("message") or fallback,
        phase=progress.get("phase", "running"),
        current_task=progress.get("task_name", ""),
        task_stage_id=progress.get("task_stage_id", ""),
        stage_items=progress.get("stage_items", {}),
    )
    return progress


def run_pexpect_command(
    action: str,
    cmd: list[str],
    cwd: Path,
    ssh_password: str,
    sudo_password: str,
    log_file: Path,
    append: bool = False,
) -> tuple[int, str, str]:
    deadline = time.time() + action_timeout_seconds(action)
    env = os.environ.copy()
    env["ANSIBLE_SSH_RETRIES"] = str(int(cfg("ansible", "ssh_retries", default=1)))
    env["ANSIBLE_TIMEOUT"] = str(int(cfg("ansible", "ansible_timeout", default=15)))
    mode = "a" if append else "w"
    with log_file.open(mode, encoding="utf-8") as log:
        if append and log.tell() > 0:
            log.write("\n\n")
        log.write(f"$ {shlex.join(cmd)}\n")
        log.write(f"cwd={cwd}\n\n")
        log.flush()
        last_output_pos = log.tell()
        idle_notice_seconds = int(cfg("ansible", "no_output_notice_seconds", default=30))
        child = pexpect.spawn(cmd[0], cmd[1:], cwd=str(cwd), env=env, encoding="utf-8", timeout=int(cfg("ansible", "prompt_timeout", default=15)))
        child.logfile_read = log
        last_output_at = time.time()
        last_notice_at = 0.0
        ssh_prompt_attempts = 0
        become_prompt_attempts = 0
        generic_password_attempts = 0
        try:
            while True:
                index = child.expect(PASSWORD_PROMPTS)
                log.flush()
                current_pos = log.tell()
                if current_pos > last_output_pos:
                    last_output_pos = current_pos
                    last_output_at = time.time()
                    last_notice_at = 0.0
                if index in SSH_PROMPT_INDEXES:
                    ssh_prompt_attempts += 1
                    if ssh_prompt_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                        log.write("\n[operator_console] SSH password prompt repeated, aborting without exposing password\n")
                        log.flush()
                        child.terminate(force=True)
                        raise PermissionError("密码认证失败。请检查 SSH 密码和 sudo 密码是否正确。")
                    set_job(message="正在连接机器人……")
                    log.write("\n[operator_console] detected SSH password prompt, password sent\n")
                    log.flush()
                    child.sendline(ssh_password)
                elif index in BECOME_PROMPT_INDEXES:
                    become_prompt_attempts += 1
                    if become_prompt_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                        log.write("\n[operator_console] BECOME password prompt repeated, aborting without exposing password\n")
                        log.flush()
                        child.terminate(force=True)
                        raise PermissionError("sudo 密码未被正确识别或认证失败。请重新输入 sudo 密码后重试。")
                    set_job(message="正在执行脚本……")
                    log.write("\n[operator_console] detected BECOME password prompt, sudo password sent\n")
                    log.flush()
                    child.sendline(sudo_password or ssh_password)
                elif index == GENERIC_PASSWORD_PROMPT_INDEX:
                    generic_password_attempts += 1
                    if generic_password_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                        log.write("\n[operator_console] password prompt repeated, aborting without exposing password\n")
                        log.flush()
                        child.terminate(force=True)
                        raise PermissionError("密码认证失败。请检查 SSH 密码和 sudo 密码是否正确。")
                    set_job(message="正在连接机器人……")
                    log.write("\n[operator_console] detected generic password prompt, password sent\n")
                    log.flush()
                    child.sendline(ssh_password)
                elif index == HOST_KEY_PROMPT_INDEX:
                    child.sendline("yes")
                elif index == TIMEOUT_PROMPT_INDEX:
                    if not child.isalive():
                        child.close()
                        update_job_from_log(log_file, "任务执行结束，正在生成中文报告……")
                        break
                    if time.time() > deadline:
                        child.terminate(force=True)
                        raise TimeoutError("任务执行超时，请查看实时日志确认卡在哪一步。")
                    now = time.time()
                    progress = update_job_from_log(log_file)
                    if now - last_output_at >= idle_notice_seconds and now - last_notice_at >= idle_notice_seconds:
                        message = progress.get("message") or "任务仍在执行，当前暂无新日志输出，请稍候……"
                        set_job(message=f"{message}（当前暂无新日志输出）")
                        last_notice_at = now
                    continue
                elif index == EOF_PROMPT_INDEX:
                    update_job_from_log(log_file, "任务执行结束，正在生成中文报告……")
                    break
        finally:
            child.close()
            log.write(f"\nreturncode={child.exitstatus if child.exitstatus is not None else child.signalstatus or 1}\n")
            log.flush()
    text = log_file.read_text(encoding="utf-8", errors="replace")
    marker = "===== STDERR ====="
    returncode = child.exitstatus if child.exitstatus is not None else child.signalstatus or 1
    if marker in text:
        stdout, stderr = text.split(marker, 1)
    else:
        stdout, stderr = text, ""
    return returncode, stdout, stderr


def run_hand_test_pexpect(
    cmd: list[str],
    cwd: Path,
    ssh_password: str,
    sudo_password: str,
    log_file: Path,
    robot_label: str,
    side_cn: str,
) -> tuple[int, str, str, bool]:
    deadline = time.time() + action_timeout_seconds(HAND_TEST_ACTION)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    running_message = f"{robot_label} {side_cn} 正在测试，请观察手是否在动。需要停止时点击取消测试。"
    cancelled = False
    child: pexpect.spawn | None = None
    with log_file.open("w", encoding="utf-8") as log:
        log.write(f"$ {shlex.join(cmd)}\n")
        log.write(f"cwd={cwd}\n\n")
        log.flush()
        child = pexpect.spawn(cmd[0], cmd[1:], cwd=str(cwd), env=env, encoding="utf-8", timeout=int(cfg("ansible", "prompt_timeout", default=15)))
        child.logfile_read = log
        with PROCESS_LOCK:
            CURRENT_PROCESS["child"] = child
        ssh_prompt_attempts = 0
        sudo_prompt_attempts = 0
        generic_password_attempts = 0
        try:
            while True:
                if job_cancel_requested():
                    cancelled = True
                    log.write("\n[operator_console] 用户取消手测试，正在停止远程测试进程\n")
                    log.flush()
                    if child.isalive():
                        child.sendcontrol("c")
                        time.sleep(0.5)
                        child.terminate(force=True)
                    break
                index = child.expect(PASSWORD_PROMPTS)
                log.flush()
                if index in SSH_PROMPT_INDEXES:
                    ssh_prompt_attempts += 1
                    if ssh_prompt_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                        child.terminate(force=True)
                        raise PermissionError("密码认证失败。请检查 SSH 密码是否正确。")
                    set_job(message="正在连接机器人……", phase="running")
                    log.write("\n[operator_console] detected SSH password prompt, password sent\n")
                    log.flush()
                    child.sendline(ssh_password)
                elif index in BECOME_PROMPT_INDEXES:
                    sudo_prompt_attempts += 1
                    if sudo_prompt_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                        child.terminate(force=True)
                        raise PermissionError("sudo 密码未被正确识别或认证失败。请重新输入 sudo 密码后重试。")
                    set_job(message="正在获取 sudo 权限并启动手测试……", phase="running")
                    log.write("\n[operator_console] detected sudo password prompt, sudo password sent\n")
                    log.flush()
                    child.sendline(sudo_password or ssh_password)
                elif index == GENERIC_PASSWORD_PROMPT_INDEX:
                    generic_password_attempts += 1
                    if generic_password_attempts > MAX_PASSWORD_PROMPT_ATTEMPTS:
                        child.terminate(force=True)
                        raise PermissionError("密码认证失败。请检查 SSH 密码和 sudo 密码是否正确。")
                    if ssh_prompt_attempts > 0 or sudo_prompt_attempts > 0:
                        password = sudo_password or ssh_password
                    else:
                        password = ssh_password
                    set_job(message="正在连接机器人或获取 sudo 权限……", phase="running")
                    log.write("\n[operator_console] detected generic password prompt, password sent\n")
                    log.flush()
                    child.sendline(password)
                elif index == HOST_KEY_PROMPT_INDEX:
                    child.sendline("yes")
                elif index == TIMEOUT_PROMPT_INDEX:
                    if not child.isalive():
                        child.close()
                        break
                    if time.time() > deadline:
                        child.terminate(force=True)
                        raise TimeoutError("手测试执行超时，已停止远程测试进程。")
                    set_job(message=running_message, phase="running")
                    continue
                elif index == EOF_PROMPT_INDEX:
                    cancelled = job_cancel_requested()
                    break
        finally:
            with PROCESS_LOCK:
                if CURRENT_PROCESS.get("child") is child:
                    CURRENT_PROCESS["child"] = None
            if child is not None:
                child.close()
                returncode = child.exitstatus if child.exitstatus is not None else child.signalstatus or 1
            else:
                returncode = 1
            if cancelled:
                returncode = 130
            log.write(f"\nreturncode={returncode}\n")
            log.flush()
    text = log_file.read_text(encoding="utf-8", errors="replace")
    return returncode, text, "", cancelled


def append_agent_log(log_file: Path, message: str) -> None:
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"\n[deploy_setup_agent] {message}\n")
        log.flush()


def agent_report_file(robot: dict[str, str]) -> Path:
    return RAW_REPORTS_DIR / f"{DEPLOY_SETUP_AGENT_ACTION}_{robot['inventory_hostname']}.json"


def clear_cos_setup_reports(hostname: str) -> None:
    for path in (RAW_REPORTS_DIR / "cos_setup_summary.json", RAW_REPORTS_DIR / f"cos_setup_{hostname}.json"):
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def load_cos_setup_report(robot: dict[str, str]) -> dict[str, Any]:
    candidates = [
        RAW_REPORTS_DIR / f"cos_setup_{robot['inventory_hostname']}.json",
        RAW_REPORTS_DIR / "cos_setup_summary.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            reports = latest_report_candidates(read_json_file(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for report in reports:
            matched = find_inventory_robot(robot_identity(report), [robot])
            if matched:
                return dict(report)
    return {}


def cos_setup_report_text(report: dict[str, Any]) -> str:
    parts = [text_from_obj(report)]
    for key in ("first_attempt", "retry_attempt", "librealsense_cleanup", "remote_start", "time_sync"):
        value = report.get(key)
        if isinstance(value, dict):
            parts.append(text_from_obj(value))
    return "\n".join(parts)


def agent_match_rule(text: str) -> dict[str, str]:
    haystack = text.lower()
    for rule in AGENT_DIAGNOSIS_RULES:
        if any(str(keyword).lower() in haystack for keyword in rule["keywords"]):
            return {key: str(value) for key, value in rule.items() if key != "keywords"}
    return {}


def agent_diagnosis(text: str, cos_report: dict[str, Any] | None = None) -> dict[str, str]:
    report = cos_report or {}
    haystack = "\n".join([text, cos_setup_report_text(report)])
    if truthy(report.get("realsense_error_detected")):
        rule = next((item for item in AGENT_DIAGNOSIS_RULES if item["id"] == "realsense"), {})
        if rule:
            return {key: str(value) for key, value in rule.items() if key != "keywords"}
    matched = agent_match_rule(haystack)
    if matched:
        return matched
    classified = classify_error(haystack)
    return {
        "id": "generic",
        "reason": classified["reason"],
        "suggestion": classified["suggestion"],
        "repair_action": "",
    }


def report_status_for_agent(report: dict[str, Any], returncode: int) -> str:
    if report:
        try:
            status = normalize_report(report)["status"]
        except Exception:  # noqa: BLE001 - report normalization should not hide the raw result
            status = status_of(report)
        if status in ("OK", "WARN", "FAIL", "UNREACHABLE", "SKIP", "NO_REPORT"):
            return status
    return "OK" if returncode == 0 else "FAIL"


def build_agent_phase(
    name: str,
    label: str,
    status: str,
    rc: int,
    started_at: str,
    ended_at: str,
    detail: str = "",
    stdout: str = "",
    stderr: str = "",
    command: str = "",
    cwd: str = "",
    report_path: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "status": status,
        "rc": rc,
        "started_at": started_at,
        "ended_at": ended_at,
        "detail": detail,
        "stdout": stdout,
        "stderr": stderr,
        "command": command,
        "cwd": cwd,
        "report_path": report_path,
    }


def agent_phase_checks(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(phase.get("label") or phase.get("name") or "agent phase"),
            "status": str(phase.get("status") or "UNKNOWN").upper(),
            "detail": str(phase.get("detail") or f"rc={phase.get('rc', '')}"),
        }
        for phase in phases
    ]


def build_deploy_setup_agent_report(
    robot: dict[str, str],
    final_status: str,
    summary: str,
    diagnosis: dict[str, str],
    phases: list[dict[str, Any]],
    cos_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = dict(cos_report or {})
    issue_status = "WARN" if final_status == "WARN" else "FAIL"
    issue = {
        "name": "智能部署 / 修复",
        "status": issue_status,
        "reason": diagnosis.get("reason", ""),
        "suggestion": diagnosis.get("suggestion", ""),
        "detail": "\n".join(
            f"{phase.get('label')}: {phase.get('status')} rc={phase.get('rc')}"
            for phase in phases
        ),
    }
    checks = agent_phase_checks(phases)
    report.update(
        {
            "inventory_hostname": robot.get("inventory_hostname", ""),
            "robot_id": robot.get("robot_id", ""),
            "ansible_host": robot.get("ansible_host", ""),
            "end_effector": robot.get("end_effector", ""),
            "mode": DEPLOY_SETUP_AGENT_ACTION,
            "overall_status": final_status,
            "agent_status": final_status,
            "agent_summary": summary,
            "setup_success": final_status == "OK" or truthy(report.get("setup_success")),
            "checks": checks,
            "agent_phases": phases,
        }
    )
    if final_status == "OK":
        report["execution_error"] = ""
    elif final_status == "WARN":
        report["warn_items"] = [issue, *[item for item in report.get("warn_items", []) if isinstance(item, dict)]]
        report["execution_error"] = diagnosis.get("reason", "")
    else:
        report["failed_items"] = [issue, *[item for item in report.get("failed_items", []) if isinstance(item, dict)]]
        report["execution_error"] = diagnosis.get("reason", "")
    return report


def write_deploy_setup_agent_report(report: dict[str, Any], robot: dict[str, str]) -> None:
    write_json_file(agent_report_file(robot), report)
    write_json_file(report_source_for_action(DEPLOY_SETUP_AGENT_ACTION), [report])


def run_agent_deploy_phase(robot: dict[str, str], ssh_password: str, sudo_password: str, log_file: Path, name: str, label: str) -> dict[str, Any]:
    ip = robot["ansible_host"]
    command = f"export REMOTE_HOST={shlex.quote(ip)} && ./deploy_to_robot.sh"
    cwd = ROOT_DIR / "deploy_eva_robot检测" / "robot"
    set_job(message=f"agent：{label}……", cmd=command, cwd=str(cwd), phase="running")
    append_agent_log(log_file, f"{label} start: {robot['inventory_hostname']} ({ip})")
    started_at = now_iso()
    rc, stdout, stderr = run_deploy_to_robot(ip=ip, password=ssh_password, log_file=log_file, append=True)
    ended_at = now_iso()
    status = "OK" if rc == 0 else "FAIL"
    detail = "robot 文件部署完成" if status == "OK" else classify_error("\n".join([stdout, stderr]))["reason"]
    append_agent_log(log_file, f"{label} done: status={status} rc={rc}")
    return build_agent_phase(name, label, status, rc, started_at, ended_at, detail, stdout, stderr, command, str(cwd))


def run_agent_cos_setup_phase(robot: dict[str, str], ssh_password: str, sudo_password: str, log_file: Path, name: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    hostname = robot["inventory_hostname"]
    clear_cos_setup_reports(hostname)
    cmd, cwd = build_ansible_command("cos_setup", [hostname])
    command = shlex.join(cmd)
    set_job(message=f"agent：{label}……", cmd=command, cwd=str(cwd), phase="running")
    append_agent_log(log_file, f"{label} start: {hostname}")
    started_at = now_iso()
    rc, stdout, stderr = run_pexpect_command("cos_setup", cmd, cwd, ssh_password, sudo_password, log_file, append=True)
    combined_output = "\n".join([stdout, stderr])
    if rc != 0 and recap_success_from_text(combined_output):
        rc = 0
    elif recap_failed_from_text(combined_output):
        rc = rc or 1
    cos_report = load_cos_setup_report(robot)
    report_status = report_status_for_agent(cos_report, rc)
    phase_status = report_status if report_status in ("OK", "WARN") else "FAIL"
    ended_at = now_iso()
    if cos_report:
        detail = str(cos_report.get("setup_detail") or cos_report.get("agent_summary") or f"cos_setup status={report_status}")
        report_path = str((RAW_REPORTS_DIR / f"cos_setup_{hostname}.json").relative_to(ROOT_DIR))
    else:
        detail = "未生成 cos_setup 报告" if rc != 0 else "cos_setup 执行完成，但未读取到报告"
        report_path = ""
    append_agent_log(log_file, f"{label} done: status={phase_status} rc={rc}")
    phase = build_agent_phase(name, label, phase_status, rc, started_at, ended_at, detail, stdout, stderr, command, str(cwd), report_path)
    return phase, cos_report


def run_deploy_setup_agent(robots: list[str], ssh_password: str, sudo_password: str) -> None:
    JOB_CONTEXT.scope = JOB_SCOPE_DEVELOPER
    ensure_dirs()
    started_monotonic = time.monotonic()
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = ROOT_LOGS_DIR / f"{started}_{DEPLOY_SETUP_AGENT_ACTION}.log"
    execution = {
        "action": DEPLOY_SETUP_AGENT_ACTION,
        "robots": robots,
        "started_at": started,
        "start_time": start_time,
        "finished_at": "",
        "end_time": "",
        "duration_seconds": None,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "latest_output": "",
        "command": "deploy_setup_agent",
        "cwd": str(ROOT_DIR),
        "report_path": report_path_text(DEPLOY_SETUP_AGENT_ACTION),
        "log_file": str(log_file),
    }
    set_job(
        running=True,
        action=DEPLOY_SETUP_AGENT_ACTION,
        robots=[robot["inventory_hostname"] for robot in selected_inventory_robots(robots)],
        started_at=started,
        finished_at="",
        returncode=None,
        message="agent：正在检查本地依赖和机器人连接……",
        log_file=str(log_file),
        cmd="deploy_setup_agent",
        cwd=str(ROOT_DIR),
        start_time=start_time,
        end_time="",
        duration_seconds=None,
        report_file=report_path_text(DEPLOY_SETUP_AGENT_ACTION),
        phase="precheck",
    )
    phases: list[dict[str, Any]] = []
    cos_report: dict[str, Any] = {}
    robot: dict[str, str] | None = None

    def finish(final_status: str, message: str, diagnosis: dict[str, str], returncode: int) -> None:
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration = round(time.monotonic() - started_monotonic, 1)
        log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        execution.update(
            {
                "finished_at": end_time,
                "end_time": end_time,
                "duration_seconds": duration,
                "returncode": returncode,
                "stdout": log_text,
                "stderr": "" if returncode == 0 else diagnosis.get("reason", ""),
                "latest_output": tail_lines(log_text),
            }
        )
        write_json_file(RAW_REPORTS_DIR / f"{started}_{DEPLOY_SETUP_AGENT_ACTION}_execution.json", execution)
        if robot:
            report = build_deploy_setup_agent_report(robot, final_status, message, diagnosis, phases, cos_report)
            write_deploy_setup_agent_report(report, robot)
            write_json_file(latest_summary_path(JOB_SCOPE_DEVELOPER), attach_execution_metadata([report], execution))
        else:
            write_json_file(latest_summary_path(JOB_SCOPE_DEVELOPER), build_execution_failure_summary(DEPLOY_SETUP_AGENT_ACTION, execution))
        set_job(
            running=False,
            finished_at=end_time,
            returncode=returncode,
            message=message,
            end_time=end_time,
            duration_seconds=duration,
            phase="success" if final_status == "OK" else "partial" if final_status == "WARN" else "fail",
        )

    try:
        ensure_deploy_setup_agent_available()
        selected_robots = selected_inventory_robots(robots)
        if len(selected_robots) != 1:
            raise ValueError("智能部署 / 修复第一版一次只能选择 1 台机器人")
        robot = selected_robots[0]
        execution["robots"] = [robot["inventory_hostname"]]

        reachable, unreachable = precheck_ssh([robot], DEPLOY_SETUP_AGENT_ACTION)
        if unreachable and not reachable:
            diagnosis = {
                "reason": "机器人无法连接，请检查网络、IP 和 SSH 密码。",
                "suggestion": "请检查机器人是否开机、IP 是否正确、电脑和机器人是否在同一网络，以及 SSH 密码是否正确。",
                "repair_action": "",
            }
            phases.append(
                build_agent_phase(
                    "precheck",
                    "机器人连接检查",
                    "UNREACHABLE",
                    1,
                    now_iso(),
                    now_iso(),
                    str(unreachable[0].get("detail", "")),
                )
            )
            finish("UNREACHABLE", "agent 执行失败：机器人无法连接。", diagnosis, 1)
            return

        phases.append(build_agent_phase("precheck", "机器人连接检查", "OK", 0, now_iso(), now_iso(), "SSH 端口可连接"))

        deploy_phase = run_agent_deploy_phase(robot, ssh_password, sudo_password, log_file, "deploy_to_robot", "部署 robot 文件")
        phases.append(deploy_phase)
        if deploy_phase["status"] != "OK":
            diagnosis = agent_diagnosis("\n".join([deploy_phase.get("stdout", ""), deploy_phase.get("stderr", "")]))
            finish("FAIL", f"agent 执行失败：{diagnosis['reason']}", diagnosis, 1)
            return

        cos_phase, cos_report = run_agent_cos_setup_phase(robot, ssh_password, sudo_password, log_file, "cos_setup", "执行 cos_setup.sh")
        phases.append(cos_phase)
        if cos_phase["status"] == "OK":
            diagnosis = {"reason": "", "suggestion": "", "repair_action": ""}
            finish("OK", "agent 执行完成：deploy 和 cos_setup 均成功。", diagnosis, 0)
            return
        if cos_phase["status"] == "WARN":
            diagnosis = agent_diagnosis("\n".join([cos_phase.get("stdout", ""), cos_phase.get("stderr", "")]), cos_report)
            finish("WARN", f"agent 执行完成但存在警告：{diagnosis['reason']}", diagnosis, 0)
            return

        diagnosis = agent_diagnosis("\n".join([cos_phase.get("stdout", ""), cos_phase.get("stderr", "")]), cos_report)
        if diagnosis.get("repair_action") == "redeploy_and_retry_cos_setup":
            repair_phase = run_agent_deploy_phase(robot, ssh_password, sudo_password, log_file, "repair_deploy_to_robot", "自动修复：重新部署 robot 文件")
            phases.append(repair_phase)
            if repair_phase["status"] == "OK":
                retry_phase, cos_report = run_agent_cos_setup_phase(robot, ssh_password, sudo_password, log_file, "cos_setup_retry", "自动修复后重试 cos_setup.sh")
                phases.append(retry_phase)
                if retry_phase["status"] == "OK":
                    fixed = {"reason": "", "suggestion": "", "repair_action": ""}
                    finish("OK", "agent 执行完成：重新部署后 cos_setup 成功。", fixed, 0)
                    return
                diagnosis = agent_diagnosis("\n".join([retry_phase.get("stdout", ""), retry_phase.get("stderr", "")]), cos_report)
            else:
                diagnosis = agent_diagnosis("\n".join([repair_phase.get("stdout", ""), repair_phase.get("stderr", "")]))

        finish("FAIL", f"agent 执行失败：{diagnosis['reason']}", diagnosis, 1)
    except Exception as exc:  # noqa: BLE001 - surface readable agent failures to UI
        diagnosis = {"reason": str(exc), "suggestion": "请查看实时日志和技术日志，确认本地依赖、机器人选择和密码是否正确。", "repair_action": ""}
        append_agent_log(log_file, f"failed: {exc}")
        finish("FAIL", f"agent 执行失败：{exc}", diagnosis, 1)


def run_hand_test(robots: list[str], side: str, ssh_password: str, sudo_password: str) -> None:
    JOB_CONTEXT.scope = JOB_SCOPE_OPERATION
    ensure_dirs()
    started_monotonic = time.monotonic()
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = ROOT_LOGS_DIR / f"{started}_{HAND_TEST_ACTION}.log"
    side_cn = HAND_TEST_SIDE_LABELS.get(side, side)
    robot: dict[str, str] | None = None
    execution: dict[str, Any] = {
        "action": HAND_TEST_ACTION,
        "robots": robots,
        "side": side,
        "started_at": started,
        "start_time": start_time,
        "finished_at": "",
        "end_time": "",
        "duration_seconds": None,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "latest_output": "",
        "command": "",
        "cwd": "",
        "report_path": "",
        "log_file": str(log_file),
    }
    try:
        if shutil.which("ssh") is None:
            raise ValueError("控制电脑没有找到 ssh 命令")
        robot = selected_hand_test_robot(robots)
        hand_type = str(robot.get("end_effector", "")).strip().lower()
        robot_label = f"ID {robot.get('robot_id', '-')} · {robot.get('ansible_host', '-')} · {hand_type}"
        set_job(
            running=True,
            action=HAND_TEST_ACTION,
            robots=[robot["inventory_hostname"]],
            started_at=started,
            finished_at="",
            returncode=None,
            message="正在检查机器人连接……",
            log_file=str(log_file),
            cmd="",
            cwd="",
            start_time=start_time,
            end_time="",
            duration_seconds=None,
            report_file="",
            phase="precheck",
            cancellable=True,
            cancel_requested=False,
            robot_label=robot_label,
            hand_type=hand_type,
            hand_side=side,
            hand_side_cn=side_cn,
        )
        reachable, unreachable = precheck_ssh([robot], HAND_TEST_ACTION)
        if unreachable and not reachable:
            message = "机器人无法连接，请检查网络、IP 和 SSH 密码。"
            details = "\n".join(str(item.get("detail", "")) for item in unreachable)
            log_file.write_text(f"{message}\n{details}\n", encoding="utf-8")
            raise ConnectionError(message)
        cmd, cwd, remote_cmd = hand_test_command(robot, side)
        execution.update(
            {
                "robots": [robot["inventory_hostname"]],
                "hand_type": hand_type,
                "robot_id": robot.get("robot_id", ""),
                "ansible_host": robot.get("ansible_host", ""),
                "command": shlex.join(cmd),
                "remote_command": remote_cmd,
                "cwd": str(cwd),
            }
        )
        set_job(
            message=f"正在启动 {hand_type} {side_cn} 测试……",
            cmd=execution["command"],
            cwd=str(cwd),
            phase="running",
        )
        returncode, stdout, stderr, cancelled = run_hand_test_pexpect(
            cmd=cmd,
            cwd=cwd,
            ssh_password=ssh_password,
            sudo_password=sudo_password,
            log_file=log_file,
            robot_label=robot_label,
            side_cn=side_cn,
        )
        combined_output = "\n".join([stdout, stderr])
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration = round(time.monotonic() - started_monotonic, 1)
        execution.update(
            {
                "finished_at": end_time,
                "end_time": end_time,
                "duration_seconds": duration,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode,
                "cancelled": cancelled,
                "latest_output": tail_lines(combined_output),
            }
        )
        write_json_file(RAW_REPORTS_DIR / f"{started}_{HAND_TEST_ACTION}_execution.json", execution)
        if cancelled:
            message = "手测试已取消。"
            phase = "success"
            returncode = 0
        elif returncode == 0:
            message = "手测试程序已结束。"
            phase = "success"
        else:
            message = classify_error(combined_output)["reason"]
            phase = "fail"
        set_job(
            running=False,
            finished_at=end_time,
            returncode=returncode,
            message=message,
            end_time=end_time,
            duration_seconds=duration,
            phase=phase,
            cancellable=False,
            cancel_requested=False,
        )
    except Exception as exc:  # noqa: BLE001 - return readable failure to UI
        message = f"执行失败：{exc}"
        previous_log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        with log_file.open("a", encoding="utf-8") as log:
            if previous_log and not previous_log.endswith("\n"):
                log.write("\n")
            log.write(f"{message}\n")
        latest_output = tail_lines("\n".join([previous_log, message]))
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration = round(time.monotonic() - started_monotonic, 1)
        execution.update(
            {
                "finished_at": end_time,
                "end_time": end_time,
                "duration_seconds": duration,
                "returncode": 1,
                "stdout": previous_log,
                "stderr": message,
                "latest_output": latest_output,
            }
        )
        write_json_file(RAW_REPORTS_DIR / f"{started}_{HAND_TEST_ACTION}_execution.json", execution)
        set_job(
            running=False,
            action=HAND_TEST_ACTION,
            started_at=started,
            finished_at=end_time,
            returncode=1,
            message=message,
            log_file=str(log_file),
            end_time=end_time,
            duration_seconds=duration,
            phase="fail",
            cancellable=False,
            cancel_requested=False,
            hand_side=side,
            hand_side_cn=side_cn,
        )


def run_action(
    action: str,
    robots: list[str],
    ssh_password: str,
    sudo_password: str,
    service_name: str = "",
    camera_topic: str = "",
) -> None:
    JOB_CONTEXT.scope = job_scope_for_action(action)
    if action == DEPLOY_SETUP_AGENT_ACTION:
        run_deploy_setup_agent(robots, ssh_password, sudo_password)
        return
    ensure_dirs()
    started_monotonic = time.monotonic()
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = ROOT_LOGS_DIR / f"{started}_{action}.log"
    set_job(
        running=True,
        action=action,
        robots=[robot["inventory_hostname"] for robot in selected_inventory_robots(robots)],
        started_at=started,
        finished_at="",
        returncode=None,
        message="正在检查机器人连接……",
        log_file=str(log_file),
        cmd="",
        cwd="",
        start_time=start_time,
        end_time="",
        duration_seconds=None,
        report_file=report_path_text(action),
        phase="precheck",
    )
    execution = {
        "action": action,
        "robots": robots,
        "started_at": started,
        "start_time": start_time,
        "finished_at": "",
        "end_time": "",
        "duration_seconds": None,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "latest_output": "",
        "command": "",
        "cwd": "",
        "report_path": report_path_text(action),
        "log_file": str(log_file),
    }
    try:
        selected_robots = selected_inventory_robots(robots)
        if action == DEPLOY_ACTION and not selected_robots:
            direct_ip = next((str(item).strip() for item in robots if str(item).strip()), "")
            try:
                ipaddress.ip_address(direct_ip)
            except ValueError as exc:
                raise ValueError("请选择要部署的机器人") from exc
            selected_robots = [
                {
                    "inventory_hostname": direct_ip,
                    "ansible_host": direct_ip,
                    "robot_id": "",
                    "end_effector": "",
                }
            ]
        set_job(message="正在检查机器人连接……", phase="precheck")
        reachable_robots, unreachable_reports = precheck_ssh(selected_robots, action)
        if unreachable_reports and not reachable_robots:
            message = "机器人无法连接，请检查网络、IP 和 SSH 密码。"
            details = "\n".join(str(item.get("detail", "")) for item in unreachable_reports)
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            duration = round(time.monotonic() - started_monotonic, 1)
            log_file.write_text(f"{message}\n{details}\n", encoding="utf-8")
            execution.update(
                {
                    "finished_at": end_time,
                    "end_time": end_time,
                    "duration_seconds": duration,
                    "returncode": 1,
                    "stderr": f"{message}\n{details}",
                    "latest_output": tail_lines(f"{message}\n{details}"),
                }
            )
            write_json_file(RAW_REPORTS_DIR / f"{started}_{action}_execution.json", execution)
            write_json_file(latest_summary_path(job_scope_for_action(action)), attach_execution_metadata(unreachable_reports, execution))
            set_job(
                running=False,
                finished_at=end_time,
                returncode=1,
                message=message,
                end_time=end_time,
                duration_seconds=duration,
                phase="fail",
            )
            return

        set_job(message="机器人连接成功，正在启动任务……", phase="starting")
        reachable_names = [robot["inventory_hostname"] for robot in reachable_robots]
        if action == CAMERA_CHECK_ACTION:
            set_job(
                message="相机帧率检查准备中……",
                phase="running",
                current_task="等待相机帧率检查开始",
                task_stage_id="ros2",
            )
        elif action in SERVICE_ACTIONS:
            set_job(
                message="服务检查准备中……",
                phase="running",
                current_task="等待服务检查开始",
                task_stage_id="services",
                stage_items={"services": service_stage_items_for_robots(reachable_robots, service_name)},
            )
        if action == DEPLOY_ACTION:
            set_job(message="正在执行 deploy_to_robot……", phase="running")
            robot = reachable_robots[0] if reachable_robots else selected_robots[0]
            ip = robot["ansible_host"]
            cmd = [
                "bash",
                "-c",
                f"export REMOTE_HOST={shlex.quote(ip)} && ./deploy_to_robot.sh",
            ]
            cwd = ROOT_DIR / "deploy_eva_robot检测" / "robot"
            execution.update({"robots": [robot["inventory_hostname"]], "command": shlex.join(cmd), "cwd": str(cwd)})
            set_job(cmd=execution["command"], cwd=str(cwd))

            returncode, stdout, stderr = run_deploy_to_robot(
                ip=ip,
                password=ssh_password,
                log_file=log_file,
            )
            combined_output = "\n".join([stdout, stderr])
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            duration = round(time.monotonic() - started_monotonic, 1)
            execution.update(
                {
                    "finished_at": end_time,
                    "end_time": end_time,
                    "duration_seconds": duration,
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": returncode,
                    "latest_output": tail_lines(combined_output),
                }
            )
            write_json_file(RAW_REPORTS_DIR / f"{started}_{action}_execution.json", execution)
            write_json_file(latest_summary_path(JOB_SCOPE_DEVELOPER), build_single_execution_report(action, execution, robot))

            set_job(
                running=False,
                finished_at=end_time,
                message="deploy 完成" if returncode == 0 else "deploy 失败",
                phase="success" if returncode == 0 else "fail",
                returncode=returncode,
                end_time=end_time,
                duration_seconds=duration,
            )
            return

        cmd, cwd = build_ansible_command(action, reachable_names, service_name, camera_topic)
        execution.update({"robots": reachable_names, "command": shlex.join(cmd), "cwd": str(cwd)})
        set_job(message="正在执行脚本……", cmd=execution["command"], cwd=str(cwd), phase="running")

        # 每次执行前清理旧的报告，防止 playbook 崩溃时读取到历史成功记录
        source_report = report_source_for_action(action)
        if source_report.exists():
            try:
                source_report.unlink()
            except OSError:
                pass

        returncode, stdout, stderr = run_pexpect_command(action, cmd, cwd, ssh_password, sudo_password, log_file)
        set_job(message="任务执行结束，正在生成中文报告……", phase="reporting")
        if unreachable_reports:
            returncode = 1
        combined_output = "\n".join([stdout, stderr])
        recap_success = recap_success_from_text(combined_output)
        recap_failed = recap_failed_from_text(combined_output)
        if returncode != 0 and recap_success:
            returncode = 0
        elif recap_failed:
            returncode = returncode or 1
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration = round(time.monotonic() - started_monotonic, 1)
        execution.update(
            {
                "robots": reachable_names,
                "finished_at": end_time,
                "end_time": end_time,
                "duration_seconds": duration,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "latest_output": tail_lines(combined_output),
            }
        )
        write_json_file(RAW_REPORTS_DIR / f"{started}_{action}_execution.json", execution)
        merge_latest_summary(action, execution, unreachable_reports)
        if action in SERVICE_ACTIONS:
            service_items = latest_service_stage_items()
            if service_items:
                merge_job_stage_items({"services": service_items})
            camera_items = latest_camera_stage_items()
            if camera_items:
                merge_job_stage_items({"ros2": camera_items})
        if unreachable_reports:
            message = "部分机器人执行失败，请查看下方报告。"
            phase = "partial"
        else:
            message = "执行完成：任务成功。" if returncode == 0 else classify_error(stdout + "\n" + stderr)["reason"]
            phase = "success" if returncode == 0 else "fail"
        if (
            action in ("cos_setup", "check", "install", "service_check", "service_restart", CAMERA_CHECK_ACTION)
            and not unreachable_reports
            and (returncode == 0 or report_source_for_action(action).exists())
        ):
            report_returncode, report_message, report_phase = latest_action_outcome()
            returncode = report_returncode
            execution["returncode"] = returncode
            write_json_file(RAW_REPORTS_DIR / f"{started}_{action}_execution.json", execution)
            merge_latest_summary(action, execution, [])
            if action in SERVICE_ACTIONS:
                service_items = latest_service_stage_items()
                if service_items:
                    merge_job_stage_items({"services": service_items})
                camera_items = latest_camera_stage_items()
                if camera_items:
                    merge_job_stage_items({"ros2": camera_items})
            message = report_message
            phase = report_phase
        set_job(
            running=False,
            finished_at=end_time,
            returncode=returncode,
            message=message,
            end_time=end_time,
            duration_seconds=duration,
            phase=phase,
        )
    except Exception as exc:  # noqa: BLE001 - return readable failure to UI
        message = f"执行失败：{exc}"
        previous_log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        with log_file.open("a", encoding="utf-8") as log:
            if previous_log and not previous_log.endswith("\n"):
                log.write("\n")
            log.write(f"{message}\n")
        latest_output = tail_lines("\n".join([previous_log, message]))
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration = round(time.monotonic() - started_monotonic, 1)
        execution.update(
            {
                "finished_at": end_time,
                "end_time": end_time,
                "duration_seconds": duration,
                "returncode": 1,
                "stdout": previous_log,
                "stderr": message,
                "latest_output": latest_output,
            }
        )
        write_json_file(RAW_REPORTS_DIR / f"{started}_{action}_execution.json", execution)
        write_json_file(latest_summary_path(job_scope_for_action(action)), build_execution_failure_summary(action, execution))
        set_job(
            running=False,
            finished_at=end_time,
            returncode=1,
            message=message,
            end_time=end_time,
            duration_seconds=duration,
            phase="fail",
        )


def latest_log() -> dict[str, str]:
    logs = sorted(ROOT_LOGS_DIR.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not logs:
        return {"path": "", "content": ""}
    latest = logs[0]
    return {"path": str(latest), "content": latest.read_text(encoding="utf-8", errors="replace")}


def current_log_path(scope: str | None = None) -> Path | None:
    with JOB_LOCK:
        value = str(current_job(scope).get("log_file") or "")
    if value:
        path = Path(value)
        return path if path.exists() else None
    if scope is not None:
        return None
    logs = sorted(ROOT_LOGS_DIR.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def log_tail_payload(line_count: int = 100, scope: str | None = None) -> dict[str, Any]:
    path = current_log_path(scope)
    if not path:
        return {"path": "", "content": "", "last_lines": [], "updated_at": "-"}
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    return {
        "path": str(path.relative_to(ROOT_DIR) if path.is_relative_to(ROOT_DIR) else path),
        "content": "\n".join(lines[-line_count:]),
        "last_lines": lines[-line_count:],
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OperatorConsole/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_file(TEMPLATES_DIR / "index.html", "text/html; charset=utf-8")
        elif parsed.path == "/api/robot-record":
            try:
                self.send_json(load_robot_record())
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        elif parsed.path == "/api/robots/status":
            self.send_json(robots_status_payload())
        elif parsed.path.startswith("/api/robots/") and parsed.path.endswith("/services"):
            hostname = unquote(parsed.path.removeprefix("/api/robots/").removesuffix("/services").strip("/"))
            self.send_robot_services(hostname)
        elif parsed.path == "/api/robots":
            self.send_json({"robots": inventory_with_robot_record_status()})
        elif parsed.path in ("/api/report/latest", "/api/reports"):
            try:
                scope = normalize_job_scope(parse_qs(parsed.query).get("scope", [JOB_SCOPE_OPERATION])[0])
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(latest_report_payload(scope))
        elif parsed.path == "/api/logs/latest":
            self.send_json(latest_log())
        elif parsed.path == "/api/logs/tail":
            try:
                scope = normalize_job_scope(parse_qs(parsed.query).get("scope", [JOB_SCOPE_OPERATION])[0])
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(log_tail_payload(scope=scope))
        elif parsed.path == "/api/job":
            try:
                scope = normalize_job_scope(parse_qs(parsed.query).get("scope", [JOB_SCOPE_OPERATION])[0])
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            with JOB_LOCK:
                self.send_json(job_payload_locked(scope))
        elif parsed.path == "/api/local/checks":
            self.send_json(local_dependency_status())
        elif parsed.path.startswith("/static/"):
            self.send_file(STATIC_DIR / parsed.path.replace("/static/", "", 1))
        else:
            self.send_error(HTTPStatus.NOT_FOUND.value)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/robot-record":
                self.send_json(save_robot_record(self.read_payload().get("store")))
                return
            if parsed.path == "/api/robot-record/remote":
                payload = self.read_payload()
                try:
                    remote = read_remote_robot_record(payload.get("url"), payload.get("apiKey"))
                except PermissionError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
                    return
                except FileNotFoundError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
                    return
                except ConnectionError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                    return
                self.send_json(remote)
                return
            if parsed.path == "/api/robots":
                self.send_json({"robots": add_robot(self.read_payload())}, HTTPStatus.CREATED)
                return
            if parsed.path in HEARTBEAT_PATHS:
                self.send_json(record_robot_heartbeat(self.read_payload()), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/refresh":
                try:
                    self.send_json(
                        refresh_latest_report_with_status(
                            ssh_password=str(self.headers.get("X-SSH-Password", "")),
                            sudo_password=str(self.headers.get("X-Sudo-Password", "")),
                        )
                    )
                except RuntimeError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
                return
            if parsed.path == "/api/camera-binding/inspect":
                self.send_camera_binding("inspect", self.read_payload())
                return
            if parsed.path == "/api/camera-binding/reload":
                self.send_camera_binding("reload", self.read_payload())
                return
            if parsed.path.startswith("/api/run/"):
                action = parsed.path.rsplit("/", 1)[-1]
                self.start_action(action, self.read_payload())
                return
            if parsed.path == "/api/hand-test/cancel":
                self.send_json(cancel_current_job())
                return
            if parsed.path == "/api/run":
                payload = self.read_payload()
                self.start_action(str(payload.get("action", "")), payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND.value)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def send_robot_services(self, hostname: str) -> None:
        try:
            self.send_json(
                robot_services_payload(
                    hostname,
                    ssh_password=str(self.headers.get("X-SSH-Password", "")),
                    sudo_password=str(self.headers.get("X-Sudo-Password", "")),
                )
            )
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except TimeoutError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.REQUEST_TIMEOUT)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)

    def send_camera_binding(self, operation: str, payload: dict[str, Any]) -> None:
        robots = payload.get("robots") or []
        if not isinstance(robots, list):
            robots = []
        ssh_password = str(payload.get("ssh_password", ""))
        sudo_password = str(payload.get("sudo_password", ""))
        if not ssh_password or not sudo_password:
            self.send_json({"error": "缺少 SSH 或 sudo 密码"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            self.send_json(
                camera_binding_payload(
                    operation,
                    [str(item) for item in robots],
                    ssh_password,
                    sudo_password,
                )
            )
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except TimeoutError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.REQUEST_TIMEOUT)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/robots/"):
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        hostname = unquote(parsed.path.rsplit("/", 1)[-1])
        try:
            self.send_json({"robots": update_robot(hostname, self.read_payload())})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/robots/"):
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        hostname = unquote(parsed.path.rsplit("/", 1)[-1])
        try:
            self.send_json({"robots": delete_robot(hostname)})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def start_action(self, action: str, payload: dict[str, Any]) -> None:
        if action == HAND_TEST_ACTION:
            self.start_hand_test(payload)
            return
        if action not in ("cos_setup", "install", "check", "service_check", "service_restart", CAMERA_CHECK_ACTION, DEPLOY_ACTION, DEPLOY_SETUP_AGENT_ACTION):
            self.send_json({"error": "未知操作"}, HTTPStatus.BAD_REQUEST)
            return
        if action == DEPLOY_SETUP_AGENT_ACTION:
            try:
                ensure_deploy_setup_agent_available()
            except ValueError as exc:
                self.send_json(
                    {
                        "status": "FAIL",
                        "message": str(exc),
                        "reason": str(exc),
                        "suggestion": deploy_setup_agent_dependency_status().get("suggestion", "请安装缺失依赖后重试。"),
                    },
                    HTTPStatus.BAD_REQUEST,
                )
                return
        elif action != DEPLOY_ACTION:
            try:
                ensure_ansible_available()
            except ValueError:
                self.send_json(
                    {
                        "status": "FAIL",
                        "message": "控制电脑未安装 Ansible，请先安装 ansible 和 sshpass。",
                        "reason": "控制电脑缺少 ansible-playbook",
                        "suggestion": "请在控制电脑执行：sudo apt install -y ansible sshpass",
                    },
                    HTTPStatus.BAD_REQUEST,
                )
                return
        robots = payload.get("robots") or []
        if not isinstance(robots, list):
            robots = []
        scope = job_scope_for_action(action)
        robot_names = requested_robot_names([str(item) for item in robots])
        if action == DEPLOY_SETUP_AGENT_ACTION:
            selected_for_agent = selected_inventory_robots([str(item) for item in robots])
            if len(selected_for_agent) != 1:
                self.send_json({"error": "智能部署 / 修复第一版一次只能选择 1 台机器人"}, HTTPStatus.BAD_REQUEST)
                return
        service_name = str(payload.get("service_name", "")).strip()
        camera_topic = str(payload.get("camera_topic", "")).strip()
        if action == "service_restart" and service_name not in RESTARTABLE_SERVICES:
            self.send_json({"error": "请选择要重启的服务"}, HTTPStatus.BAD_REQUEST)
            return
        if action == "service_check" and service_name and service_name not in RESTARTABLE_SERVICES:
            self.send_json({"error": "请选择要复查的服务"}, HTTPStatus.BAD_REQUEST)
            return
        if camera_topic and (action != CAMERA_CHECK_ACTION or camera_topic not in CAMERA_TOPIC_NAMES):
            self.send_json({"error": "请选择有效的摄像头 topic"}, HTTPStatus.BAD_REQUEST)
            return
        if action == CAMERA_CHECK_ACTION and camera_topic and len(robot_names) != 1:
            self.send_json({"error": "单摄像头复查一次只能选择 1 台机器人"}, HTTPStatus.BAD_REQUEST)
            return
        ssh_password = str(payload.get("ssh_password", ""))
        sudo_password = str(payload.get("sudo_password", ""))
        if not ssh_password or not sudo_password:
            self.send_json({"status": "FAIL", "message": "缺少 SSH 或 sudo 密码"}, HTTPStatus.BAD_REQUEST)
            return
        with JOB_LOCK:
            job = current_job(scope)
            if job.get("running"):
                self.send_json({"error": "当前模块已有任务正在执行"}, HTTPStatus.CONFLICT)
                return
            conflict = conflicting_job_locked(scope, robot_names)
            if conflict:
                conflict_names = "、".join(sorted(set(robot_names).intersection(str(item) for item in conflict.get("robots", []))))
                self.send_json({"error": f"机器人 {conflict_names} 正在执行另一模块任务，请选择其他机器人或等待完成"}, HTTPStatus.CONFLICT)
                return
            job.update(
                {
                    "running": True,
                    "action": action,
                    "robots": robot_names,
                    "returncode": None,
                    "message": "任务正在启动……",
                    "phase": "starting",
                }
            )
        thread = threading.Thread(
            target=run_action,
            args=(action, [str(item) for item in robots], ssh_password, sudo_password, service_name, camera_topic),
            daemon=True,
        )
        thread.start()
        time.sleep(0.1)
        with JOB_LOCK:
            self.send_json(job_payload_locked(scope), HTTPStatus.ACCEPTED)

    def start_hand_test(self, payload: dict[str, Any]) -> None:
        robots = payload.get("robots") or []
        if not isinstance(robots, list):
            robots = []
        side = str(payload.get("side", "")).strip().lower()
        if side not in HAND_TEST_SIDES:
            self.send_json({"error": "请选择左手或右手"}, HTTPStatus.BAD_REQUEST)
            return
        if len(robots) != 1:
            self.send_json({"error": "验证手是否能动一次只能选择 1 台机器人"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            selected_hand_test_robot([str(item) for item in robots])
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        ssh_password = str(payload.get("ssh_password", ""))
        sudo_password = str(payload.get("sudo_password", ""))
        if not ssh_password or not sudo_password:
            self.send_json({"status": "FAIL", "message": "缺少 SSH 或 sudo 密码"}, HTTPStatus.BAD_REQUEST)
            return
        with JOB_LOCK:
            robot_names = requested_robot_names([str(item) for item in robots])
            job = current_job(JOB_SCOPE_OPERATION)
            if job.get("running"):
                self.send_json({"error": "操作模块已有任务正在执行"}, HTTPStatus.CONFLICT)
                return
            conflict = conflicting_job_locked(JOB_SCOPE_OPERATION, robot_names)
            if conflict:
                conflict_names = "、".join(sorted(set(robot_names).intersection(str(item) for item in conflict.get("robots", []))))
                self.send_json({"error": f"机器人 {conflict_names} 正在执行开发者升级，请选择其他机器人或等待升级完成"}, HTTPStatus.CONFLICT)
                return
            job.update(
                {
                    "running": True,
                    "action": HAND_TEST_ACTION,
                    "robots": robot_names,
                    "returncode": None,
                    "message": "手测试正在启动……",
                    "phase": "starting",
                }
            )
        thread = threading.Thread(
            target=run_hand_test,
            args=([str(item) for item in robots], side, ssh_password, sudo_password),
            daemon=True,
        )
        thread.start()
        time.sleep(0.1)
        with JOB_LOCK:
            self.send_json(job_payload_locked(JOB_SCOPE_OPERATION), HTTPStatus.ACCEPTED)


def main() -> None:
    parser = argparse.ArgumentParser(description="G1 operator web console")
    parser.add_argument("--host", default=str(cfg("server", "host", default="127.0.0.1")))
    parser.add_argument("--port", type=int, default=int(cfg("server", "port", default=8000)))
    args = parser.parse_args()
    ensure_dirs()
    local_status = local_dependency_status()
    for item in local_status["checks"]:
        print(f"Local check: {item['name']} {item['status_cn']} {item.get('path') or item.get('configured') or ''}")
    if local_status.get("reason"):
        print(f"Local dependency warning: {local_status['reason']}")
        print(local_status.get("suggestion", ""))
    start_robot_status_refresher()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Operator Console: {url}")
    if bool(cfg("server", "open_browser", default=True)):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
