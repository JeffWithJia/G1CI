#!/usr/bin/env python3
"""Local Chinese operator console for G1 robot environment operations."""

from __future__ import annotations

import argparse
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
from urllib.parse import unquote, urlparse

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
ROOT_LOGS_DIR = ROOT_DIR / "logs"
LATEST_SUMMARY_PATH = ROOT_REPORTS_DIR / "latest_summary.json"

STATUS_ORDER = {"FAIL": 4, "UNREACHABLE": 4, "WARN": 3, "NO_REPORT": 2, "SKIP": 1, "OK": 0}
COS_REQUIRED_SERVICES = ("cos_agent", "xrobotoolkit-pc-service", "cos_teleop")
COS_ACTIVE_SERVICES = ("cos_agent", "cos_teleop")
COS_XROBOT_SERVICE = "xrobotoolkit-pc-service"
SERVICE_ACTIONS = {"service_check", "service_restart"}
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
COS_TELEOP_CAMERA_WARNING_RE = re.compile(
    r"RealSense camera\[0\] frame wait timed out|"
    r"frame wait timed out|"
    r"Failed to read direct MJPEG frame|"
    r"Failed to open V4L2 device|"
    r"Failed to initialize V4L2|"
    r"Camera initialization failed|"
    r"No such file or directory",
    re.IGNORECASE,
)
INVENTORY_VARS = [
    "ansible_user=unitree",
    "ansible_python_interpreter=/usr/bin/python3",
    "ansible_ssh_common_args='-o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'",
]
END_EFFECTORS = {"dex1", "brainco", "none"}
PASSWORD_PROMPTS = [
    r"(?im)^SSH password.*:",
    r"(?im)^ssh password.*:",
    r"(?im)^BECOME password.*:",
    r"(?im)^become password.*:",
    r"(?im)^\[sudo\] password.*:",
    r"(?im)^sudo password.*:",
    r"(?im)^password.*:",
    r"(?i)Are you sure you want to continue connecting",
    pexpect.TIMEOUT,
    pexpect.EOF,
]
SSH_PROMPT_INDEXES = {0, 1}
BECOME_PROMPT_INDEXES = {2, 3, 4, 5}
GENERIC_PASSWORD_PROMPT_INDEX = 6
HOST_KEY_PROMPT_INDEX = 7
TIMEOUT_PROMPT_INDEX = 8
EOF_PROMPT_INDEX = 9
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

CURRENT_JOB: dict[str, Any] = {
    "running": False,
    "action": "",
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
}
JOB_LOCK = threading.Lock()


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


def local_dependency_status() -> dict[str, Any]:
    ansible_status = executable_status("ansible-playbook", ansible_playbook_configured_path())
    checks = [
        ansible_status,
        executable_status("sshpass"),
        executable_status("python3"),
        file_status("inventory.ini", inventory_path()),
        file_status("check_base_env.yml", resolve_root_path(str(cfg("ansible", "check_playbook", default="check_base_env.yml")))),
        file_status("batch_cos_setup.yml", resolve_root_path(str(cfg("ansible", "cos_setup_playbook", default="deploy_eva_robot检测/batch_cos_setup.yml")))),
    ]
    missing = [item for item in checks if not item["ok"]]
    return {
        "ok": not missing,
        "checks": checks,
        "reason": "控制电脑没有找到 ansible-playbook 命令。" if not ansible_status["ok"] else "",
        "suggestion": "请在控制电脑安装 Ansible 和 sshpass：\nsudo apt update\nsudo apt install -y ansible sshpass" if not ansible_status["ok"] else "",
    }


def ansible_playbook_executable() -> str:
    status = executable_status("ansible-playbook", ansible_playbook_configured_path())
    return status["path"] or ansible_playbook_configured_path()


def ensure_ansible_available() -> None:
    status = executable_status("ansible-playbook", ansible_playbook_configured_path())
    if not status["ok"]:
        raise ValueError("控制电脑缺少 ansible-playbook")


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


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
    mapping = {
        "/cam_head/compressed_image": "头部相机",
        "/cam_wrist_left/compressed_image": "左腕相机",
        "/cam_wrist_right/compressed_image": "右腕相机",
    }
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
    statuses = [base_status] if base_status and base_status != "UNKNOWN" else []
    statuses.extend(str(item.get("status", "")).upper() for item in issues if isinstance(item, dict))
    statuses.extend(str(item.get("status", "")).upper() for item in checks if isinstance(item, dict))
    for key in ("services", "service_health"):
        value = report.get(key)
        service_items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        statuses.extend(str(item.get("status", "")).upper() for item in service_items if isinstance(item, dict))
    if any(status in ("FAIL", "UNREACHABLE") for status in statuses):
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    if statuses and all(status == "OK" for status in statuses):
        return "OK"
    return base_status


def service_loaded(service: dict[str, Any]) -> bool:
    stdout = str(service.get("stdout", "") or "")
    return bool(service.get("loaded")) or "Loaded: loaded" in stdout


def service_enabled(service: dict[str, Any]) -> bool:
    stdout = str(service.get("stdout", "") or "")
    enabled_value = service.get("enabled")
    return bool(enabled_value) or "\nenabled\n" in f"\n{stdout}\n" or "; enabled;" in stdout


def service_active(service: dict[str, Any]) -> bool:
    stdout = str(service.get("stdout", "") or "")
    return bool(service.get("active")) or "\nactive\n" in f"\n{stdout}\n" or "Active: active" in stdout


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


def parse_ansible_progress(text: str) -> dict[str, Any]:
    progress: dict[str, Any] = {"message": "", "phase": "running", "recap_success": False, "recap_failed": False}
    recap_seen = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        play_match = re.match(r"PLAY \[(.+?)\]\s+\*+", line)
        if play_match:
            progress.update({"message": f"开始执行：{clean_ansible_heading(play_match.group(1))}", "phase": "running"})
            continue
        task_match = re.match(r"TASK \[(.+?)\]\s+\*+", line)
        if task_match:
            progress.update({"message": f"正在执行：{clean_ansible_heading(task_match.group(1))}", "phase": "running"})
            continue
        host_match = re.match(r"(ok|changed|failed|fatal): \[([^\]]+)\]", line)
        if host_match:
            status, host = host_match.groups()
            if status in ("failed", "fatal"):
                progress.update({"message": f"{host}：执行失败，请查看日志", "phase": "fail"})
            else:
                progress.update({"message": f"{host}：该步骤完成", "phase": "running"})
            continue
        if "PLAY RECAP" in line:
            recap_seen = True
            progress.update({"message": "任务执行结束，正在生成中文报告……", "phase": "reporting"})
            continue
        if recap_seen:
            counts = parse_recap_counts(line)
            if not counts:
                continue
            if counts.get("failed", 0) == 0 and counts.get("unreachable", 0) == 0:
                progress.update({"message": "执行完成：任务成功。", "phase": "success", "recap_success": True})
            else:
                progress.update({"message": "执行失败：存在失败或无法连接的机器人", "phase": "fail", "recap_failed": True})
    return progress


def report_source_for_action(action: str) -> Path:
    if action == "cos_setup":
        return RAW_REPORTS_DIR / "cos_setup_summary.json"
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


def raw_outputs(report: dict[str, Any]) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []

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
    return {
        "inventory_hostname": robot_identity(report),
        "robot_id": str(report.get("robot_id", "")),
        "ansible_host": str(report.get("ansible_host", "")),
        "end_effector": str(report.get("end_effector", "")),
        "mode": str(report.get("mode", "cos_setup" if report.get("setup_status") else "")),
        "status": status,
        "status_cn": translate_status(status),
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
        "camera_topics": [
            {
                "topic": str(item.get("topic", "")),
                "label": str(item.get("label", "") or camera_label_from_topic(str(item.get("topic", "") or ""))),
                "status": str(item.get("status", "")).upper(),
                "status_cn": translate_status(item.get("status", "")),
                "rate_hz": item.get("rate_hz", 0),
                "reason": translate_phrase(item.get("reason", "")),
                "suggestion": translate_phrase(item.get("suggestion", "")),
            }
            for item in report.get("camera_topics", [])
            if isinstance(item, dict)
        ],
        "raw_outputs": raw_outputs(report),
    }


def latest_report_payload() -> dict[str, Any]:
    data: Any = []
    source = str(LATEST_SUMMARY_PATH.relative_to(ROOT_DIR))
    if LATEST_SUMMARY_PATH.exists():
        data = read_json_file(LATEST_SUMMARY_PATH)
    elif (ROOT_REPORTS_DIR / "g1_env_summary.json").exists():
        data = read_json_file(ROOT_REPORTS_DIR / "g1_env_summary.json")
        write_json_file(LATEST_SUMMARY_PATH, data)
        source = "reports/g1_env_summary.json"
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
    updated_at = datetime.fromtimestamp(LATEST_SUMMARY_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if LATEST_SUMMARY_PATH.exists() else "-"
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


def latest_action_outcome() -> tuple[int, str, str]:
    if not LATEST_SUMMARY_PATH.exists():
        return 1, "执行失败：未生成报告。", "fail"
    data = read_json_file(LATEST_SUMMARY_PATH)
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
    if action != "cos_setup":
        copy_raw_jsons("g1_robot_*.json")
    if source.exists():
        data = read_json_file(source)
        reports = [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        write_json_file(LATEST_SUMMARY_PATH, attach_execution_metadata(reports, execution))
        if action == "cos_setup":
            for item in RAW_REPORTS_DIR.glob("cos_setup_*.json"):
                if item.name != "cos_setup_summary.json":
                    shutil.copy2(item, RAW_REPORTS_DIR / item.name)
        return
    write_json_file(LATEST_SUMMARY_PATH, build_execution_summary(action, execution))


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
        if not names or robot["inventory_hostname"] in names or robot.get("robot_id") in selected_ids
    ]


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
    if action != "cos_setup":
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
    write_json_file(LATEST_SUMMARY_PATH, attach_execution_metadata(list(by_host.values()), execution))


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


def build_execution_failure_summary(action: str, execution: dict[str, Any]) -> list[dict[str, Any]]:
    return build_execution_summary(action, execution)


def action_timeout_seconds(action: str) -> int:
    defaults = {"check": 600, "install": 1800, "cos_setup": 1800, "service_check": 300, "service_restart": 300}
    return int(cfg("ansible", f"{action}_timeout_seconds", default=defaults.get(action, 900)))


def ssh_common_args() -> str:
    connect_timeout = int(cfg("ansible", "ssh_connect_timeout", default=5))
    return (
        "-o PreferredAuthentications=password -o PubkeyAuthentication=no "
        "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout={connect_timeout} -o ConnectionAttempts=1"
    )


def service_action_playbook(action: str, service_name: str = "") -> Path:
    playbook_path = Path("/tmp") / f"operator_console_{action}.yml"
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
    camera_topic_min_rate_hz: 0.0
    camera_topics:
      - topic: /cam_head/compressed_image
        label: 头部相机
      - topic: /cam_wrist_left/compressed_image
        label: 左腕相机
      - topic: /cam_wrist_right/compressed_image
        label: 右腕相机

  tasks:
{restart_line}
    - name: Select services for this robot
      ansible.builtin.set_fact:
        selected_service_names: >-
          {{{{
            ['cos_agent.service', 'cos_teleop.service', 'xrobotoolkit-pc-service.service']
            + (['dex1_gripper.service'] if (end_effector | default('none') | lower) == 'dex1' else [])
            + (['brainco_hand.service'] if (end_effector | default('none') | lower) == 'brainco' else [])
          }}}}

    - name: Check selected services
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
            journal="$(journalctl -u "$service" -n 120 --no-pager -o cat 2>&1)"
          elif [ "$service" = "cos_teleop.service" ]; then
            journal="$(journalctl -u "$service" -n 200 --no-pager -o cat 2>&1)"
          else
            journal="$(journalctl -u "$service" -n 150 --no-pager 2>&1)"
          fi
          if printf '%s\\n' "$status" | grep -q 'Loaded: loaded'; then loaded=1; else loaded=0; fi
        }}
        evaluate_health() {{
          log_issue=0
          health_ok=0
          detail="not active"
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
            if printf '%s\\n' "$journal" | grep -q 'Available Serial Ports' &&
               printf '%s\\n' "$journal" | grep -q 'Detected motors' &&
               printf '%s\\n' "$journal" | grep -q 'Side: right' &&
               printf '%s\\n' "$journal" | grep -q 'Side: left' &&
               printf '%s\\n' "$journal" | grep -q 'Dex1-1 Gripper Server started'; then
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
          if [ "$service" = "cos_teleop.service" ] && printf '%s\\n' "$journal" | grep -Eiq 'RealSense camera\\[0\\] frame wait timed out|frame wait timed out|Failed to read direct MJPEG frame|Failed to open V4L2 device|Failed to initialize V4L2|Failed to initialize V4L2 direct MJPEG capture|Camera initialization failed|No such file or directory'; then
            log_issue=1
            detail="cos_teleop journal has recent camera initialization error"
            return
          fi
          health_ok=1
          detail="active/loaded/enabled"
        }}
        check_service
        evaluate_health
        if [ "$service" != "xrobotoolkit-pc-service.service" ] && {{ [ "$active_rc" -ne 0 ] || [ "$log_issue" -eq 1 ]; }}; then
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
                  ((item.stdout | default('') | lower) is regex('realsense camera\\[0\\] frame wait timed out|frame wait timed out|failed to read direct mjpeg frame|failed to open v4l2 device|failed to initialize v4l2|failed to initialize v4l2 direct mjpeg capture|camera initialization failed|no such file or directory'))
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
                    ((item.stdout | default('') | lower) is regex('realsense camera\\[0\\] frame wait timed out|frame wait timed out|failed to read direct mjpeg frame|failed to open v4l2 device|failed to initialize v4l2|failed to initialize v4l2 direct mjpeg capture|camera initialization failed|no such file or directory'))
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

    - name: Build service check items
      ansible.builtin.set_fact:
        service_check_items: >-
          {{{{
            service_result_map | dict2items | map(attribute='value') | list
          }}}}

    - name: Check ROS2 camera topic heartbeats
      ansible.builtin.shell: |
        set +e
        export HOME=/home/unitree
        export ROS_VERSION=2
        export ROS_DISTRO=foxy
        export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
        export CYCLONEDDS_URI=/home/unitree/cyclonedds_ws/cyclonedds.xml
        export PYTHONUNBUFFERED=1
        source /opt/ros/foxy/setup.bash
        if [ -f /home/unitree/cyclonedds_ws/install/setup.bash ]; then
          source /home/unitree/cyclonedds_ws/install/setup.bash
        fi
        if [ -f /home/unitree/cos_ws/install/setup.bash ]; then
          source /home/unitree/cos_ws/install/setup.bash
        fi
        python3 - "{{{{ item.topic }}}}" <<'PY'
        import sys
        import time

        topic = sys.argv[1]
        duration_seconds = 6.0
        count = 0
        first_time = None
        last_time = None

        try:
            import rclpy
            from sensor_msgs.msg import CompressedImage
        except Exception as exc:
            print("ros_hz_error: import failed: %s" % exc, file=sys.stderr, flush=True)
            sys.exit(2)

        def callback(_msg):
            global count, first_time, last_time
            now = time.monotonic()
            if first_time is None:
                first_time = now
            last_time = now
            count += 1

        try:
            rclpy.init(args=None)
            node = rclpy.create_node("operator_console_camera_hz_check")
            node.create_subscription(CompressedImage, topic, callback, 10)
            deadline = time.monotonic() + duration_seconds
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.2)
            node.destroy_node()
            rclpy.shutdown()
        except Exception as exc:
            print("ros_hz_error: %s" % exc, file=sys.stderr, flush=True)
            try:
                rclpy.shutdown()
            except Exception:
                pass
            sys.exit(3)

        if count >= 2 and first_time is not None and last_time is not None and last_time > first_time:
            rate = (count - 1) / (last_time - first_time)
            print("average rate: %.3f" % rate, flush=True)
            sys.exit(0)

        print("ros_hz_warning: no messages received on %s count=%d" % (topic, count), flush=True)
        sys.exit(1)
        PY
      args:
        executable: /bin/bash
      loop: "{{{{ camera_topics }}}}"
      register: camera_topic_hz_checks
      changed_when: false
      failed_when: false

    - name: Initialize ROS2 camera topic heartbeat results
      ansible.builtin.set_fact:
        camera_topic_results: []

    - name: Record ROS2 camera topic heartbeat status
      ansible.builtin.set_fact:
        camera_topic_results: "{{{{ camera_topic_results + [camera_topic_result] }}}}"
      vars:
        camera_topic_rate_matches: "{{{{ item.stdout | default('') | regex_findall('average rate:\\\\s*([0-9]+(?:\\\\.[0-9]+)?)') }}}}"
        camera_topic_rate_hz: "{{{{ (camera_topic_rate_matches | first | default('0', true)) | float }}}}"
        camera_topic_ok: "{{{{ (item.rc | default(1) == 0) and (camera_topic_rate_hz | float > camera_topic_min_rate_hz | default(0.0) | float) }}}}"
        camera_topic_reason: "{{{{ item.item.label }}}}无数据"
        camera_topic_suggestion: "检查{{{{ item.item.label }}}} Type-C 连接或重启 cos_teleop.service"
        camera_topic_detail: >-
          {{{{
            'average rate: ' ~ camera_topic_rate_hz ~ ' Hz'
            if camera_topic_ok | bool
            else 'timeout 10 ros2 topic hz ' ~ item.item.topic ~ ' did not receive messages'
          }}}}
        camera_topic_result: >-
          {{{{
            {{
              'topic': item.item.topic,
              'label': item.item.label,
              'status': ('OK' if camera_topic_ok | bool else 'WARN'),
              'rate_hz': camera_topic_rate_hz | float
            }}
            | combine({{}} if camera_topic_ok | bool else {{
              'reason': camera_topic_reason,
              'suggestion': camera_topic_suggestion,
              'detail': camera_topic_detail,
              'stdout': item.stdout | default(''),
              'stderr': item.stderr | default('')
            }})
          }}}}
      loop: "{{{{ camera_topic_hz_checks.results | default([]) }}}}"

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
        camera_warn_items: >-
          {{{{
            camera_topic_results | default([])
            | selectattr('status', 'equalto', 'WARN')
            | map(attribute='topic') | list
          }}}}

    - name: Build service final report
      ansible.builtin.set_fact:
        service_final_report:
          host: "{{{{ inventory_hostname }}}}"
          inventory_hostname: "{{{{ inventory_hostname }}}}"
          ansible_host: "{{{{ ansible_host | default(inventory_hostname) }}}}"
          robot_id: "{{{{ robot_id | default('unknown') }}}}"
          mode: "{{{{ service_action }}}}"
          overall_status: >-
            {{{{
              'FAIL'
              if service_failed_items | length > 0
              else 'WARN'
              if (service_warn_items | length > 0 or camera_warn_items | length > 0)
              else 'OK'
            }}}}
          summary: >-
            {{{{
              '服务状态正常。'
              if (service_failed_items | length == 0 and service_warn_items | length == 0 and camera_warn_items | length == 0)
              else '服务存在异常：' ~ ((service_failed_items + service_warn_items + camera_warn_items) | join(', '))
            }}}}
          services: "{{{{ service_result_map | default({{}}) }}}}"
          checks: "{{{{ service_result_map | dict2items | map(attribute='value') | list }}}}"
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


def build_ansible_command(action: str, robots: list[str], service_name: str = "") -> tuple[list[str], Path]:
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
        cmd = [*common, str(resolve_root_path(str(cfg("ansible", "check_playbook", default="check_base_env.yml"))))]
    elif action == "install":
        cmd = [
            *common,
            str(resolve_root_path(str(cfg("ansible", "check_playbook", default="check_base_env.yml")))),
            "-e",
            "g1_mode=install",
        ]
    elif action == "cos_setup":
        cmd = [
            *common,
            str(resolve_root_path(str(cfg("ansible", "cos_setup_playbook", default="deploy_eva_robot检测/batch_cos_setup.yml")))),
            "-e",
            f"cos_setup_report_dir={RAW_REPORTS_DIR}",
        ]
    elif action in SERVICE_ACTIONS:
        cmd = [*common, str(service_action_playbook(action, service_name))]
    else:
        raise ValueError("未知操作")
    clean_robots = [item for item in robots if item]
    if clean_robots:
        cmd.extend(["--limit", ":".join(clean_robots)])
    return cmd, ROOT_DIR


def set_job(**updates: Any) -> None:
    with JOB_LOCK:
        CURRENT_JOB.update(updates)


def update_job_from_log(log_file: Path, fallback: str = "任务仍在执行，请稍候……") -> dict[str, Any]:
    if not log_file.exists():
        set_job(message=fallback, phase="running")
        return {"message": fallback, "phase": "running"}
    text = log_file.read_text(encoding="utf-8", errors="replace")
    progress = parse_ansible_progress(text)
    set_job(message=progress.get("message") or fallback, phase=progress.get("phase", "running"))
    return progress


def run_pexpect_command(action: str, cmd: list[str], cwd: Path, ssh_password: str, sudo_password: str, log_file: Path) -> tuple[int, str, str]:
    deadline = time.time() + action_timeout_seconds(action)
    env = os.environ.copy()
    env["ANSIBLE_SSH_RETRIES"] = str(int(cfg("ansible", "ssh_retries", default=1)))
    env["ANSIBLE_TIMEOUT"] = str(int(cfg("ansible", "ansible_timeout", default=15)))
    with log_file.open("w", encoding="utf-8") as log:
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


def run_action(action: str, robots: list[str], ssh_password: str, sudo_password: str, service_name: str = "") -> None:
    ensure_dirs()
    started_monotonic = time.monotonic()
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = ROOT_LOGS_DIR / f"{started}_{action}.log"
    set_job(
        running=True,
        action=action,
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
            write_json_file(LATEST_SUMMARY_PATH, attach_execution_metadata(unreachable_reports, execution))
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
        cmd, cwd = build_ansible_command(action, reachable_names, service_name)
        execution.update({"robots": reachable_names, "command": shlex.join(cmd), "cwd": str(cwd)})
        set_job(message="正在执行脚本……", cmd=execution["command"], cwd=str(cwd), phase="running")
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
        if unreachable_reports:
            message = "部分机器人执行失败，请查看下方报告。"
            phase = "partial"
        else:
            message = "执行完成：任务成功。" if returncode == 0 else classify_error(stdout + "\n" + stderr)["reason"]
            phase = "success" if returncode == 0 else "fail"
        if action in ("cos_setup", "check", "install", "service_check", "service_restart") and not unreachable_reports:
            report_returncode, report_message, report_phase = latest_action_outcome()
            returncode = max(returncode, report_returncode)
            execution["returncode"] = returncode
            write_json_file(RAW_REPORTS_DIR / f"{started}_{action}_execution.json", execution)
            merge_latest_summary(action, execution, [])
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
        write_json_file(LATEST_SUMMARY_PATH, build_execution_failure_summary(action, execution))
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


def current_log_path() -> Path | None:
    with JOB_LOCK:
        value = str(CURRENT_JOB.get("log_file") or "")
    if value:
        path = Path(value)
        return path if path.exists() else None
    logs = sorted(ROOT_LOGS_DIR.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def log_tail_payload(line_count: int = 100) -> dict[str, Any]:
    path = current_log_path()
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
        elif parsed.path == "/api/robots":
            self.send_json({"robots": parse_inventory()})
        elif parsed.path in ("/api/report/latest", "/api/reports"):
            self.send_json(latest_report_payload())
        elif parsed.path == "/api/logs/latest":
            self.send_json(latest_log())
        elif parsed.path == "/api/logs/tail":
            self.send_json(log_tail_payload())
        elif parsed.path == "/api/job":
            with JOB_LOCK:
                self.send_json(dict(CURRENT_JOB))
        elif parsed.path == "/api/local/checks":
            self.send_json(local_dependency_status())
        elif parsed.path.startswith("/static/"):
            self.send_file(STATIC_DIR / parsed.path.replace("/static/", "", 1))
        else:
            self.send_error(HTTPStatus.NOT_FOUND.value)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/robots":
                self.send_json({"robots": add_robot(self.read_payload())}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/refresh":
                self.send_json(latest_report_payload())
                return
            if parsed.path.startswith("/api/run/"):
                action = parsed.path.rsplit("/", 1)[-1]
                self.start_action(action, self.read_payload())
                return
            if parsed.path == "/api/run":
                payload = self.read_payload()
                self.start_action(str(payload.get("action", "")), payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND.value)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

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
        if action not in ("cos_setup", "install", "check", "service_check", "service_restart"):
            self.send_json({"error": "未知操作"}, HTTPStatus.BAD_REQUEST)
            return
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
        service_name = str(payload.get("service_name", "")).strip()
        if action == "service_restart" and service_name not in RESTARTABLE_SERVICES:
            self.send_json({"error": "请选择要重启的服务"}, HTTPStatus.BAD_REQUEST)
            return
        ssh_password = str(payload.get("ssh_password", ""))
        sudo_password = str(payload.get("sudo_password", ""))
        if not ssh_password or not sudo_password:
            self.send_json({"status": "FAIL", "message": "缺少 SSH 或 sudo 密码"}, HTTPStatus.BAD_REQUEST)
            return
        with JOB_LOCK:
            if CURRENT_JOB.get("running"):
                self.send_json({"error": "已有任务正在执行"}, HTTPStatus.CONFLICT)
                return
        thread = threading.Thread(target=run_action, args=(action, [str(item) for item in robots], ssh_password, sudo_password, service_name), daemon=True)
        thread.start()
        time.sleep(0.1)
        with JOB_LOCK:
            self.send_json(dict(CURRENT_JOB), HTTPStatus.ACCEPTED)


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
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Operator Console: {url}")
    if bool(cfg("server", "open_browser", default=True)):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
