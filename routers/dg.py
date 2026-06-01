from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper
from services.os_profiles import get_os_profile
from services.maxgauge_commands import (
    MAXGAUGE_USER_COMMAND,
    build_dg_status_command,
    build_maxgauge_find_command,
)
import logging
import re

router = APIRouter()
logger = logging.getLogger(__name__)


class ActionRequest(BaseModel):
    host: str
    port: int = 10022
    username: str
    password: str
    instance_id: str = ""


def _parse_pwdx_map(status_output: str) -> dict[str, str]:
    parts = (status_output or "").split("---PWDX_INFO---", 1)
    if len(parts) < 2:
        return {}

    path_map: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        pid, path = line.split(":", 1)
        pid = pid.strip()
        path = path.strip().rstrip("/")
        if pid and path:
            path_map[pid] = path
    return path_map


def _normalize_target(instance_id: str) -> tuple[str, str, str]:
    if not instance_id or instance_id == "default":
        return "", "", ""
    normalized_path = instance_id.rstrip("/").lower() if instance_id.startswith("/") else ""
    short_target = instance_id.split("/")[-1] if instance_id.startswith("/") else instance_id
    dg_suffix = short_target.replace("DGServer_", "")
    return normalized_path, short_target.lower(), dg_suffix.lower()


def is_dg_running(status_output: str, instance_id: str, os_name: str, exit_status: int) -> bool:
    lower_out = (status_output or "").lower()
    broad_match = any(token in lower_out for token in ["mxg_dgs", "mxg_dg", "dgserver", "datagather"])

    if os_name != "SunOS":
        if not instance_id or instance_id == "default":
            return "dgserver.jar" in lower_out and exit_status == 0

        normalized_path, short_target, dg_suffix = _normalize_target(instance_id)
        path_map = _parse_pwdx_map(status_output)
        ps_section = status_output.split("---PWDX_INFO---", 1)[0]

        for line in ps_section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if "grep" in lowered:
                continue
            if "dgserver.jar" not in lowered or "java" not in lowered:
                continue

            fields = stripped.split()
            pid = fields[1] if len(fields) > 1 else ""
            cwd = path_map.get(pid, "").lower()
            if normalized_path and cwd:
                normalized_cwd = cwd.rstrip("/")
                if normalized_cwd == normalized_path or normalized_cwd.startswith(f"{normalized_path}/"):
                    return True
                continue

            if short_target and cwd and short_target in cwd:
                return True

            dg_match = re.search(r"-dg_([^\s]+)", lowered)
            if normalized_path:
                continue
            if dg_match and dg_suffix:
                token = dg_match.group(1)
                if token.endswith(dg_suffix):
                    return True

        return False

    if not instance_id or instance_id == "default":
        return broad_match

    target = instance_id.split("/")[-1] if instance_id.startswith("/") else instance_id
    target_lower = target.lower()
    specific_match = target_lower in lower_out or instance_id.lower() in lower_out
    if specific_match:
        return True

    if broad_match:
        return broad_match

    return exit_status == 0 and "mxg" in lower_out


def build_dgsctl_command(directory_expr: str, action: str) -> str:
    return f'''
DGS_DIR={directory_expr}
if [ -n "$DGS_DIR" ] && cd "$DGS_DIR" 2>/dev/null; then
  echo "DGS_DIR=$DGS_DIR"
  echo "RUN_USER=`id -un 2>/dev/null || whoami 2>/dev/null || echo unknown`"
  if [ -x ./dgsctl ]; then
    DGSCTL_CMD=./dgsctl
  else
    DGSCTL_CMD=dgsctl
  fi
  echo "DGSCTL_CMD=$DGSCTL_CMD"
  if [ -f ../.mxgrc ]; then
    MXG_RC=../.mxgrc
  elif [ -f ./.mxgrc ]; then
    MXG_RC=./.mxgrc
  else
    MXG_RC=
  fi
  echo "MXG_RC=$MXG_RC"
  if command -v ksh >/dev/null 2>&1; then
    DGS_SHELL=ksh
  else
    DGS_SHELL=sh
  fi
  echo "DGS_SHELL=$DGS_SHELL"
  if [ -n "$MXG_RC" ]; then
    DGSCTL_CMD="$DGSCTL_CMD" MXG_RC="$MXG_RC" "$DGS_SHELL" -c '. "$MXG_RC" && "$DGSCTL_CMD" {action}' 2>&1
  else
    DGSCTL_CMD="$DGSCTL_CMD" "$DGS_SHELL" -c '"$DGSCTL_CMD" {action}' 2>&1
  fi
  DGSCTL_EXIT=$?
  echo "DGSCTL_EXIT=$DGSCTL_EXIT"
  exit $DGSCTL_EXIT
else
  echo "DG Path Not Found"
  exit 1
fi
'''


@router.post("/status")
async def check_status(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_profile = get_os_profile(os_res["stdout"].strip())
            user_res = await ssh.execute_command(process_user=None, command=MAXGAUGE_USER_COMMAND)
            actual_user = user_res["stdout"].strip().split("\n")[0] or "maxgauge"
            status_user = actual_user if os_profile.name == "SunOS" else None

            res = await ssh.execute_command(
                process_user=status_user,
                command=build_dg_status_command(os_profile),
            )
            if req.instance_id and req.instance_id != "default":
                is_running = is_dg_running(res["stdout"], req.instance_id, os_profile.name, res["exit_status"])
            else:
                is_running = is_dg_running(res["stdout"], "", os_profile.name, res["exit_status"])
            logger.warning("DG status host=%s os=%s instance=%s exit_status=%s stdout=%r stderr=%r", req.host, os_profile.name, req.instance_id, res["exit_status"], res["stdout"], res["stderr"])
            return {"status": "running" if is_running else "stopped", "details": res["stdout"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/start")
async def start_service(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_profile = get_os_profile(os_res["stdout"].strip())
            user_res = await ssh.execute_command(process_user=None, command=MAXGAUGE_USER_COMMAND)
            actual_user = user_res["stdout"].strip().split("\n")[0] or "maxgauge"

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else ""
            if inst.startswith("/"):
                cmd = build_dgsctl_command(f'"{inst}"', "start")
            elif inst:
                find_cmd = build_maxgauge_find_command(os_profile, inst, "d")
                cmd = build_dgsctl_command(f'$({find_cmd})', "start")
            else:
                find_cmd = build_maxgauge_find_command(os_profile, "dgsctl", "f")
                cmd = build_dgsctl_command(f'$(dirname "$({find_cmd})")', "start")

            res = await ssh.execute_command(process_user=actual_user, command=cmd, timeout=180)
            status_user = actual_user if os_profile.name == "SunOS" else None
            status_res = await ssh.execute_command(
                process_user=status_user,
                command=build_dg_status_command(os_profile),
            )
            is_running = is_dg_running(status_res["stdout"], inst, os_profile.name, status_res["exit_status"])
            success = res["exit_status"] == 0 or is_running
            logger.warning(
                "DG start result host=%s os=%s instance=%s run_user=%s exit_status=%s is_running=%s command=%r stdout=%r stderr=%r",
                req.host,
                os_profile.name,
                inst,
                actual_user,
                res["exit_status"],
                is_running,
                res["command_executed"],
                res["stdout"],
                res["stderr"],
            )
            logs = res["stdout"]
            error = res["stderr"] or ""
            if not success and not error:
                error = logs or "DG start command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            elif res["exit_status"] != 0 and is_running:
                logs = f"{logs}\nProcess check confirmed DG is running.".strip()
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/stop")
async def stop_service(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_profile = get_os_profile(os_res["stdout"].strip())
            user_res = await ssh.execute_command(process_user=None, command=MAXGAUGE_USER_COMMAND)
            actual_user = user_res["stdout"].strip().split("\n")[0] or "maxgauge"

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else ""
            if inst.startswith("/"):
                cmd = build_dgsctl_command(f'"{inst}"', "stop")
            elif inst:
                find_cmd = build_maxgauge_find_command(os_profile, inst, "d")
                cmd = build_dgsctl_command(f'$({find_cmd})', "stop")
            else:
                find_cmd = build_maxgauge_find_command(os_profile, "dgsctl", "f")
                cmd = build_dgsctl_command(f'$(dirname "$({find_cmd})")', "stop")

            res = await ssh.execute_command(process_user=actual_user, command=cmd, timeout=120)
            status_user = actual_user if os_profile.name == "SunOS" else None
            status_res = await ssh.execute_command(
                process_user=status_user,
                command=build_dg_status_command(os_profile),
            )
            if inst.startswith("/"):
                grep_target = inst.split("/")[-1]
                is_stopped = grep_target not in status_res["stdout"] or status_res["exit_status"] != 0
            elif inst:
                is_stopped = inst not in status_res["stdout"] or status_res["exit_status"] != 0
            else:
                lower_out = status_res["stdout"].lower()
                is_stopped = not (
                    "mxg_dgs" in lower_out
                    or "mxg_dg" in lower_out
                    or "dgserver" in lower_out
                    or "datagather" in lower_out
                )
            success = res["exit_status"] == 0 or is_stopped
            logger.warning(
                "DG stop result host=%s os=%s instance=%s run_user=%s exit_status=%s is_stopped=%s command=%r stdout=%r stderr=%r",
                req.host,
                os_profile.name,
                inst,
                actual_user,
                res["exit_status"],
                is_stopped,
                res["command_executed"],
                res["stdout"],
                res["stderr"],
            )
            logs = res["stdout"]
            error = res["stderr"] or ""
            if not success and not error:
                error = logs or "DG stop command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            elif res["exit_status"] != 0 and is_stopped:
                logs = f"{logs}\nProcess check confirmed DG is stopped.".strip()
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}
