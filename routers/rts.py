from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper
from services.os_profiles import get_os_profile
from services.maxgauge_commands import (
    MAXGAUGE_USER_COMMAND,
    build_maxgauge_find_command,
    build_rts_status_command,
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


def is_rts_running(status_output: str, instance_id: str, os_name: str, exit_status: int) -> bool:
    lower_out = (status_output or "").lower()
    broad_match = any(token in lower_out for token in ["mxg_rts", "mxg_obsd", "mxg_updater", "mxg_sndf"])

    if os_name != "SunOS":
        if not instance_id or instance_id == "default":
            return "rts" in lower_out and exit_status == 0

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

    return exit_status == 0 and "mxg" in lower_out


def build_rtsctl_command(directory_expr: str, action: str) -> str:
    return f'''
RTS_DIR={directory_expr}
if [ -n "$RTS_DIR" ] && cd "$RTS_DIR" 2>/dev/null; then
  echo "RTS_DIR=$RTS_DIR"
  echo "RUN_USER=`id -un 2>/dev/null || whoami 2>/dev/null || echo unknown`"
  if [ -x ./rtsctl ]; then
    RTSCTL_CMD=./rtsctl
  else
    RTSCTL_CMD=rtsctl
  fi
  echo "RTSCTL_CMD=$RTSCTL_CMD"
  if command -v ksh >/dev/null 2>&1; then
    RTS_SHELL=ksh
  else
    RTS_SHELL=sh
  fi
  echo "RTS_SHELL=$RTS_SHELL"
  RTSCTL_CMD="$RTSCTL_CMD" "$RTS_SHELL" -c '. ./.mxgrc && "$RTSCTL_CMD" {action}' 2>&1
  RTSCTL_EXIT=$?
  echo "RTSCTL_EXIT=$RTSCTL_EXIT"
  exit $RTSCTL_EXIT
else
  echo "RTS Path Not Found"
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
            actual_user = user_res["stdout"].strip() or "maxgauge"
            status_user = actual_user if os_profile.name == "SunOS" else None

            if req.instance_id and req.instance_id != "default":
                grep_target = req.instance_id.split("/")[-1] if req.instance_id.startswith("/") else req.instance_id
            else:
                grep_target = "rts"

            res = await ssh.execute_command(
                process_user=status_user,
                command=build_rts_status_command(os_profile),
            )
            is_running = is_rts_running(res["stdout"], req.instance_id or grep_target, os_profile.name, res["exit_status"])
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
            actual_user = user_res["stdout"].strip() or "maxgauge"

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else "rts"
            if inst.startswith("/"):
                cmd = build_rtsctl_command(f'"{inst}"', "start")
            else:
                find_cmd = build_maxgauge_find_command(os_profile, inst, "d")
                cmd = build_rtsctl_command(f'$({find_cmd})', "start")

            res = await ssh.execute_command(process_user=actual_user, command=cmd, timeout=180)
            status_user = actual_user if os_profile.name == "SunOS" else None
            status_res = await ssh.execute_command(
                process_user=status_user,
                command=build_rts_status_command(os_profile),
            )
            is_running = is_rts_running(status_res["stdout"], inst, os_profile.name, status_res["exit_status"])
            success = res["exit_status"] == 0 or is_running
            logger.warning(
                "RTS start result host=%s os=%s instance=%s run_user=%s exit_status=%s is_running=%s command=%r stdout=%r stderr=%r",
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
                error = logs or "RTS start command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            elif res["exit_status"] != 0 and is_running:
                logs = f"{logs}\nProcess check confirmed RTS is running.".strip()
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        logger.exception("RTS start exception host=%s instance=%s", req.host, req.instance_id)
        return {"success": False, "error": str(e)}


@router.post("/stop")
async def stop_service(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_profile = get_os_profile(os_res["stdout"].strip())
            user_res = await ssh.execute_command(process_user=None, command=MAXGAUGE_USER_COMMAND)
            actual_user = user_res["stdout"].strip() or "maxgauge"

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else "rts"
            ps_cmd = os_profile.ps_command
            if inst.startswith("/"):
                dir_setup = f'RTS_DIR="{inst}"\nRTS_NAME=$(basename "$RTS_DIR")'
            else:
                find_cmd = build_maxgauge_find_command(os_profile, inst, "d")
                dir_setup = f'RTS_NAME="{inst}"\nRTS_DIR=$({find_cmd})'

            cmd = f'''
{dir_setup}
if [ -n "$RTS_DIR" ] && cd "$RTS_DIR" 2>/dev/null; then
  echo "RTS_DIR=$RTS_DIR"
  echo "RTS_NAME=$RTS_NAME"
  echo "RUN_USER=`id -un 2>/dev/null || whoami 2>/dev/null || echo unknown`"
  if [ -x ./rtsctl ]; then
    RTSCTL_CMD=./rtsctl
  else
    RTSCTL_CMD=rtsctl
  fi
  echo "RTSCTL_CMD=$RTSCTL_CMD"
  if command -v ksh >/dev/null 2>&1; then
    RTS_SHELL=ksh
  else
    RTS_SHELL=sh
  fi
  echo "RTS_SHELL=$RTS_SHELL"
  RTSCTL_CMD="$RTSCTL_CMD" "$RTS_SHELL" -c '. ./.mxgrc && "$RTSCTL_CMD" stop' 2>&1
  RTSCTL_EXIT=$?
  echo "RTSCTL_EXIT=$RTSCTL_EXIT"
  sleep 3
  ATTEMPT=1
  STOPPED=0
  while [ "$ATTEMPT" -le 3 ]; do
    PS_OUT="$({ps_cmd} | egrep 'mxg_(rts|obsd|updater|sndf)' | grep -v grep || true)"
    TARGET_PIDS="$(echo "$PS_OUT" | awk -v name="$RTS_NAME" -v path="$RTS_DIR" 'index($0, name) > 0 || index($0, path) > 0 {{print $2}}' | tr '\\n' ' ')"
    if [ -z "$TARGET_PIDS" ]; then
      for pid in $(echo "$PS_OUT" | awk '{{print $2}}'); do
        PROC_INFO="$(pwdx "$pid" 2>/dev/null || procwdx "$pid" 2>/dev/null || true)"
        PROC_LINE="$(echo "$PS_OUT" | awk -v target="$pid" '$2 == target {{print; exit}}')"
        if echo "$PROC_INFO" | grep -F "$RTS_DIR" >/dev/null 2>&1 || echo "$PROC_LINE" | grep -F " -c $RTS_NAME" >/dev/null 2>&1 || echo "$PROC_LINE" | grep -F "$RTS_NAME" >/dev/null 2>&1; then
          TARGET_PIDS="$TARGET_PIDS $pid"
        fi
      done
    fi
    if [ -z "$TARGET_PIDS" ]; then
      echo "No matching RTS processes found after stop request."
      STOPPED=1
      break
    fi
    echo "Attempt $ATTEMPT stopping RTS processes:$TARGET_PIDS"
    kill $TARGET_PIDS 2>/dev/null || true
    sleep 2
    STILL_PIDS=""
    for pid in $TARGET_PIDS; do
      kill -0 "$pid" 2>/dev/null && STILL_PIDS="$STILL_PIDS $pid" || true
    done
    if [ -z "$STILL_PIDS" ]; then
      echo "RTS processes stopped cleanly."
      STOPPED=1
      break
    fi
    echo "Hard killing RTS processes:$STILL_PIDS"
    kill -9 $STILL_PIDS 2>/dev/null || true
    sleep 2
    ATTEMPT=`expr "$ATTEMPT" + 1`
  done
  if [ "$STOPPED" -eq 1 ]; then
    exit 0
  fi
  echo "RTS stop failed after retries."
  exit 1
else
  echo "RTS Path Not Found"
  exit 1
fi
'''

            res = await ssh.execute_command(
                process_user=actual_user,
                command=cmd,
                timeout=120,
            )
            logger.warning(
                "RTS stop result host=%s os=%s instance=%s run_user=%s exit_status=%s command=%r stdout=%r stderr=%r",
                req.host,
                os_profile.name,
                inst,
                actual_user,
                res["exit_status"],
                res["command_executed"],
                res["stdout"],
                res["stderr"],
            )
            logs = res["stdout"]
            error = res["stderr"] or ""
            success = res["exit_status"] == 0
            if not success and not error:
                error = logs or "RTS stop command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}".strip()
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        logger.exception("RTS stop exception host=%s instance=%s", req.host, req.instance_id)
        return {"success": False, "error": str(e)}
