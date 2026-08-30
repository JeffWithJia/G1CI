#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INVENTORY="$SCRIPT_DIR/inventory.ini"
LIMIT=""
DRY_RUN=false
CONTINUE_ON_ERROR=false
REPORT_DIR="$SCRIPT_DIR/reports"
DEPLOY_SCRIPT="$SCRIPT_DIR/robot/deploy_to_robot.sh"

usage() {
  cat <<'EOF'
Usage: ./batch_deploy_to_robot.sh [options]

Options:
  --inventory PATH        Inventory file, default: inventory.ini
  --limit HOST           Deploy one inventory host name or IP only
  --dry-run              Print targets and write reports without deploying
  --continue-on-error    Continue deploying remaining robots after a failure
  -h, --help             Show this help
EOF
}

log() {
  printf '[batch_deploy_to_robot] %s\n' "$*"
}

die() {
  printf '[batch_deploy_to_robot] ERROR: %s\n' "$*" >&2
  exit 1
}

json_write() {
  local dest="$1"
  local hostname="$2"
  local host="$3"
  local user="$4"
  local status="$5"
  local rc="$6"
  local started_at="$7"
  local ended_at="$8"
  local stdout_file="$9"
  local stderr_file="${10}"

  python3 - "$dest" "$hostname" "$host" "$user" "$status" "$rc" "$started_at" "$ended_at" "$stdout_file" "$stderr_file" <<'PY'
import json
import pathlib
import sys

dest, hostname, host, user, status, rc, started_at, ended_at, stdout_file, stderr_file = sys.argv[1:]
payload = {
    "inventory_hostname": hostname,
    "ansible_host": host,
    "ansible_user": user,
    "remote": f"{user}@{host}",
    "status": status,
    "rc": int(rc),
    "started_at": started_at,
    "ended_at": ended_at,
    "stdout": pathlib.Path(stdout_file).read_text(errors="replace") if stdout_file else "",
    "stderr": pathlib.Path(stderr_file).read_text(errors="replace") if stderr_file else "",
}
pathlib.Path(dest).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
PY
}

write_summary() {
  local summary="$REPORT_DIR/deploy_to_robot_summary.json"
  python3 - "$summary" "$REPORT_DIR" <<'PY'
import json
import pathlib
import sys

summary = pathlib.Path(sys.argv[1])
report_dir = pathlib.Path(sys.argv[2])
reports = []
for path in sorted(report_dir.glob("deploy_to_robot_*.json")):
    if path.name == "deploy_to_robot_summary.json":
        continue
    reports.append(json.loads(path.read_text(errors="replace")))
summary.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
PY
}

parse_inventory() {
  awk '
    BEGIN { in_group=0; default_user="unitree" }
    /^[[:space:]]*\[g1_robots\][[:space:]]*$/ { in_group=1; next }
    /^[[:space:]]*\[/ {
      if (in_group == 1 && $0 ~ /^[[:space:]]*\[g1_robots:vars\][[:space:]]*$/) {
        in_group=2
        next
      }
      if (in_group != 0) {
        in_group=0
      }
    }
    in_group == 2 {
      line=$0
      sub(/[[:space:]]*[#;].*$/, "", line)
      if (line ~ /^[[:space:]]*ansible_user=/) {
        split(line, parts, "=")
        default_user=parts[2]
      }
      next
    }
    in_group == 1 {
      line=$0
      sub(/[[:space:]]*[#;].*$/, "", line)
      if (line ~ /^[[:space:]]*$/) next
      n=split(line, fields, /[[:space:]]+/)
      name=fields[1]
      host=name
      user=default_user
      for (i=2; i<=n; i++) {
        if (fields[i] ~ /^ansible_host=/) {
          split(fields[i], kv, "=")
          host=kv[2]
        }
        if (fields[i] ~ /^ansible_user=/) {
          split(fields[i], kv, "=")
          user=kv[2]
        }
      }
      print name "\t" host "\t" user
    }
  ' "$INVENTORY"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --inventory)
      [ "$#" -ge 2 ] || die "--inventory requires a path"
      INVENTORY="$2"
      shift 2
      ;;
    --limit)
      [ "$#" -ge 2 ] || die "--limit requires a host name or IP"
      LIMIT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --continue-on-error)
      CONTINUE_ON_ERROR=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

[ -f "$INVENTORY" ] || die "Inventory not found: $INVENTORY"
[ -x "$DEPLOY_SCRIPT" ] || die "Deploy script is not executable: $DEPLOY_SCRIPT"
command -v python3 >/dev/null 2>&1 || die "Missing required command: python3"
command -v awk >/dev/null 2>&1 || die "Missing required command: awk"
mkdir -p "$REPORT_DIR"

matched=0
failed=0

while IFS=$'\t' read -r inventory_hostname ansible_host ansible_user; do
  [ -n "$inventory_hostname" ] || continue
  if [ -n "$LIMIT" ] && [ "$LIMIT" != "$inventory_hostname" ] && [ "$LIMIT" != "$ansible_host" ]; then
    continue
  fi

  matched=$((matched + 1))
  remote="${ansible_user}@${ansible_host}"
  report="$REPORT_DIR/deploy_to_robot_${inventory_hostname}.json"
  stdout_tmp="$(mktemp)"
  stderr_tmp="$(mktemp)"
  started_at="$(date -Iseconds)"

  log "Deploying $inventory_hostname ($remote)"
  if [ "$DRY_RUN" = true ]; then
    printf 'dry-run: would execute %s %s\n' "$DEPLOY_SCRIPT" "$remote" >"$stdout_tmp"
    rc=0
  else
    set +e
    "$DEPLOY_SCRIPT" "$remote" >"$stdout_tmp" 2>"$stderr_tmp"
    rc=$?
    set -e
  fi

  ended_at="$(date -Iseconds)"
  if [ "$rc" -eq 0 ]; then
    status="OK"
  else
    status="FAIL"
    failed=$((failed + 1))
  fi

  json_write "$report" "$inventory_hostname" "$ansible_host" "$ansible_user" "$status" "$rc" "$started_at" "$ended_at" "$stdout_tmp" "$stderr_tmp"
  rm -f "$stdout_tmp" "$stderr_tmp"

  if [ "$rc" -ne 0 ] && [ "$CONTINUE_ON_ERROR" != true ]; then
    write_summary
    die "$inventory_hostname failed; report: $report"
  fi
done < <(parse_inventory)

[ "$matched" -gt 0 ] || die "No matching uncommented hosts found under [g1_robots]"
write_summary

if [ "$failed" -gt 0 ]; then
  log "Done with failures: $failed/$matched. Summary: $REPORT_DIR/deploy_to_robot_summary.json"
  exit 1
fi

log "Done: $matched target(s). Summary: $REPORT_DIR/deploy_to_robot_summary.json"
