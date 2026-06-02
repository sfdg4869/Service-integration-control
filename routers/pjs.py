from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper
from services.os_profiles import get_os_profile
from services.maxgauge_commands import (
    MAXGAUGE_USER_COMMAND,
    build_maxgauge_find_command,
    build_pjs_status_command,
)
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class ActionRequest(BaseModel):
    host: str
    port: int = 10022
    username: str
    password: str
    instance_id: str = ""


def is_pjs_running(status_output: str, instance_id: str, os_name: str, exit_status: int) -> bool:
    lower_out = (status_output or "").lower()
    broad_match = "pjs" in lower_out or "platformjs" in lower_out

    if os_name not in {"SunOS", "HP-UX"}:
        if not instance_id or instance_id == "default":
            return broad_match and exit_status == 0

        target = instance_id.split("/")[-1] if instance_id.startswith("/") else instance_id
        return target.lower() in lower_out and exit_status == 0

    if not instance_id or instance_id == "default":
        return broad_match

    target = instance_id.split("/")[-1] if instance_id.startswith("/") else instance_id
    target_lower = target.lower()
    specific_match = target_lower in lower_out or instance_id.lower() in lower_out
    if specific_match:
        return True

    if broad_match:
        return broad_match

    return exit_status == 0 and "node" in lower_out


def build_pjsctl_command(directory_expr: str, action: str) -> str:
    return f'''
PJS_DIR={directory_expr}
if [ -n "$PJS_DIR" ] && cd "$PJS_DIR" 2>/dev/null; then
  echo "PJS_DIR=$PJS_DIR"
  echo "RUN_USER=`id -un 2>/dev/null || whoami 2>/dev/null || echo unknown`"
  if [ -x ./pjsctl ]; then
    PJSCTL_CMD=./pjsctl
  else
    PJSCTL_CMD=pjsctl
  fi
  echo "PJSCTL_CMD=$PJSCTL_CMD"
  if [ -f ../.mxgrc ]; then
    MXG_RC=../.mxgrc
  elif [ -f ./.mxgrc ]; then
    MXG_RC=./.mxgrc
  else
    MXG_RC=
  fi
  echo "MXG_RC=$MXG_RC"
  if command -v ksh >/dev/null 2>&1; then
    PJS_SHELL=ksh
  else
    PJS_SHELL=sh
  fi
  echo "PJS_SHELL=$PJS_SHELL"
  if [ -n "$MXG_RC" ]; then
    PJSCTL_CMD="$PJSCTL_CMD" MXG_RC="$MXG_RC" "$PJS_SHELL" -c '. "$MXG_RC" && "$PJSCTL_CMD" {action}' 2>&1
  else
    PJSCTL_CMD="$PJSCTL_CMD" "$PJS_SHELL" -c '"$PJSCTL_CMD" {action}' 2>&1
  fi
  PJSCTL_EXIT=$?
  echo "PJSCTL_EXIT=$PJSCTL_EXIT"
  exit $PJSCTL_EXIT
else
  echo "PJS Path Not Found"
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
            status_user = actual_user if os_profile.name in {"SunOS", "HP-UX"} else None

            if req.instance_id and req.instance_id != "default":
                grep_target = req.instance_id.split("/")[-1] if req.instance_id.startswith("/") else req.instance_id
            else:
                grep_target = "pjs"

            res = await ssh.execute_command(
                process_user=status_user,
                command=build_pjs_status_command(os_profile, grep_target),
            )
            if req.instance_id and req.instance_id != "default":
                is_running = is_pjs_running(res["stdout"], req.instance_id, os_profile.name, res["exit_status"])
            else:
                is_running = is_pjs_running(res["stdout"], "", os_profile.name, res["exit_status"])
            logger.warning("PJS status host=%s os=%s instance=%s grep_target=%s exit_status=%s stdout=%r stderr=%r", req.host, os_profile.name, req.instance_id, grep_target, res["exit_status"], res["stdout"], res["stderr"])
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
                cmd = build_pjsctl_command(f'"{inst}"', "start")
            else:
                find_cmd = build_maxgauge_find_command(os_profile, "pjsctl", "f")
                cmd = build_pjsctl_command(f'$(dirname "$({find_cmd})")', "start")

            res = await ssh.execute_command(process_user=actual_user, command=cmd, timeout=180)
            grep_target = req.instance_id.split("/")[-1] if req.instance_id and req.instance_id.startswith("/") else "pjs"
            status_user = actual_user if os_profile.name in {"SunOS", "HP-UX"} else None
            status_res = await ssh.execute_command(
                process_user=status_user,
                command=build_pjs_status_command(os_profile, grep_target),
            )
            is_running = is_pjs_running(status_res["stdout"], req.instance_id, os_profile.name, status_res["exit_status"])
            success = res["exit_status"] == 0 or is_running
            logger.warning(
                "PJS start result host=%s os=%s instance=%s run_user=%s exit_status=%s is_running=%s command=%r stdout=%r stderr=%r",
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
                error = logs or "PJS start command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            elif res["exit_status"] != 0 and is_running:
                logs = f"{logs}\nProcess check confirmed PJS is running.".strip()
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/restart")
async def restart_service(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_profile = get_os_profile(os_res["stdout"].strip())
            user_res = await ssh.execute_command(process_user=None, command=MAXGAUGE_USER_COMMAND)
            actual_user = user_res["stdout"].strip().split("\n")[0] or "maxgauge"

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else ""
            if inst.startswith("/"):
                cmd = build_pjsctl_command(f'"{inst}"', "restart")
            else:
                find_cmd = build_maxgauge_find_command(os_profile, "pjsctl", "f")
                cmd = build_pjsctl_command(f'$(dirname "$({find_cmd})")', "restart")

            res = await ssh.execute_command(process_user=actual_user, command=cmd, timeout=240)
            grep_target = req.instance_id.split("/")[-1] if req.instance_id and req.instance_id.startswith("/") else "pjs"
            status_user = actual_user if os_profile.name in {"SunOS", "HP-UX"} else None
            status_res = await ssh.execute_command(
                process_user=status_user,
                command=build_pjs_status_command(os_profile, grep_target),
            )
            is_running = is_pjs_running(status_res["stdout"], req.instance_id, os_profile.name, status_res["exit_status"])
            success = res["exit_status"] == 0 or is_running
            logger.warning(
                "PJS restart result host=%s os=%s instance=%s run_user=%s exit_status=%s is_running=%s command=%r stdout=%r stderr=%r",
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
                error = logs or "PJS restart command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            elif res["exit_status"] != 0 and is_running:
                logs = f"{logs}\nProcess check confirmed PJS is running after restart.".strip()
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
                cmd = build_pjsctl_command(f'"{inst}"', "stop")
            else:
                find_cmd = build_maxgauge_find_command(os_profile, "pjsctl", "f")
                cmd = build_pjsctl_command(f'$(dirname "$({find_cmd})")', "stop")

            res = await ssh.execute_command(process_user=actual_user, command=cmd, timeout=120)
            grep_target = req.instance_id.split("/")[-1] if req.instance_id and req.instance_id.startswith("/") else "pjs"
            status_user = actual_user if os_profile.name in {"SunOS", "HP-UX"} else None
            status_res = await ssh.execute_command(
                process_user=status_user,
                command=build_pjs_status_command(os_profile, grep_target),
            )
            if req.instance_id and req.instance_id != "default":
                target = req.instance_id.split("/")[-1] if req.instance_id.startswith("/") else req.instance_id
                is_stopped = target not in status_res["stdout"] or status_res["exit_status"] != 0
            else:
                lower_out = status_res["stdout"].lower()
                is_stopped = "pjs" not in lower_out and "platformjs" not in lower_out
            success = res["exit_status"] == 0 or is_stopped
            logger.warning(
                "PJS stop result host=%s os=%s instance=%s run_user=%s exit_status=%s is_stopped=%s command=%r stdout=%r stderr=%r",
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
                error = logs or "PJS stop command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            elif res["exit_status"] != 0 and is_stopped:
                logs = f"{logs}\nProcess check confirmed PJS is stopped.".strip()
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}
