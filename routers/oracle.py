from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper
from services.os_profiles import get_os_profile
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class ActionRequest(BaseModel):
    host: str
    port: int = 10022
    username: str
    password: str
    instance_id: str = ""


def build_oracle_profile_command(sid: str, os_name: str) -> str:
    shell_profiles = "~/.ora* ~/.profile ~/.bash_profile ~/.bashrc ~/.kshrc"
    return (
        f"SID_NUM=$(echo '{sid}' | sed 's/^[Oo][Rr][Aa]//' | sed 's/^[Hh][Pp]//' | sed 's/^[Cc][Dd][Bb]//'); "
        f'PROFILE=$(grep -i -l "ORACLE_SID.*$SID_NUM" {shell_profiles} 2>/dev/null | grep -v "_empty" | head -n 1); '
        f'if [ -n "$PROFILE" ]; then echo "ORACLE_PROFILE=$PROFILE"; . "$PROFILE" 2>/dev/null || true; '
        f'else echo "ORACLE_PROFILE=NONE"; . ~/.profile 2>/dev/null || . ~/.kshrc 2>/dev/null || true; fi; '
        f'export ORACLE_SID={sid}; '
        f'if [ -n "$ORACLE_HOME" ]; then ORACLE_HOME=$(printf "%s" "$ORACLE_HOME" | sed "s/[[:space:]]*$//"); export ORACLE_HOME; fi; '
        f'echo "ORACLE_SID=$ORACLE_SID"; '
        f'echo "RUN_USER=`id -un 2>/dev/null || whoami 2>/dev/null || echo unknown`"; '
        f'echo "ORACLE_HOME=${{ORACLE_HOME:-}}"; '
        f'if [ -n "$ORACLE_HOME" ]; then export PATH="$ORACLE_HOME/bin:$PATH"; fi; '
        f'echo "SQLPLUS_PATH_BEFORE=`command -v sqlplus 2>/dev/null || echo NOT_FOUND`"; '
        f'echo "LSNRCTL_PATH_BEFORE=`command -v lsnrctl 2>/dev/null || echo NOT_FOUND`"; '
        f'if ! command -v sqlplus >/dev/null 2>&1 || ! command -v lsnrctl >/dev/null 2>&1; then '
        f'if [ -f ~/.bash_profile ]; then echo "FALLBACK_PROFILE=$HOME/.bash_profile"; . ~/.bash_profile 2>/dev/null || true; fi; '
        f'if [ -n "$ORACLE_HOME" ]; then ORACLE_HOME=$(printf "%s" "$ORACLE_HOME" | sed "s/[[:space:]]*$//"); export ORACLE_HOME; export PATH="$ORACLE_HOME/bin:$PATH"; fi; '
        f'fi; '
        f'echo "SQLPLUS_PATH_AFTER=`command -v sqlplus 2>/dev/null || echo NOT_FOUND`"; '
        f'echo "LSNRCTL_PATH_AFTER=`command -v lsnrctl 2>/dev/null || echo NOT_FOUND`"; '
        f'if [ "{os_name}" = "SunOS" ] && [ -n "$ORACLE_HOME" ]; then export LD_LIBRARY_PATH="$ORACLE_HOME/lib:${{LD_LIBRARY_PATH:-}}"; fi'
    )


def build_oracle_status_command(os_name: str, sid: str = "") -> str:
    ps_cmd = "UNIX95=1 ps -ef" if os_name in {"AIX", "HP-UX", "SunOS"} else "ps -ef"
    if sid and sid != "default":
        pattern = f"ora_pmon_{sid}"
    else:
        pattern = "pmon"
    return f"{ps_cmd} | grep '{pattern}' | grep -v grep || true"


@router.post("/status")
async def check_status(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_name = get_os_profile(os_res["stdout"].strip()).name
            sid = req.instance_id if req.instance_id and req.instance_id != "default" else ""
            res = await ssh.execute_command(
                process_user=None,
                command=build_oracle_status_command(os_name, sid),
            )
            is_running = ("pmon" in res["stdout"] and res["exit_status"] == 0)
            return {"status": "running" if is_running else "stopped", "details": res["stdout"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/start")
async def start_service(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_name = get_os_profile(os_res["stdout"].strip()).name
            sid = req.instance_id if req.instance_id and req.instance_id != "default" else "ORCL"
            profile_cmd = build_oracle_profile_command(sid, os_name)
            cmd = f'{profile_cmd}; lsnrctl start; printf "startup;\\nexit;\\n" | sqlplus -s / as sysdba'
            res = await ssh.execute_command(
                process_user="oracle",
                command=cmd,
                timeout=180,
            )
            status_res = await ssh.execute_command(
                process_user=None,
                command=build_oracle_status_command(os_name, sid),
            )
            is_running = f"ora_pmon_{sid}" in status_res["stdout"] and status_res["exit_status"] == 0
            success = res["exit_status"] == 0 or is_running
            logs = res["stdout"]
            error = res["stderr"] or ""
            if not success and not error:
                error = logs or "Oracle start command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            logger.warning(
                "Oracle start result host=%s os=%s sid=%s exit_status=%s is_running=%s stdout=%r stderr=%r",
                req.host,
                os_name,
                sid,
                res["exit_status"],
                is_running,
                res["stdout"],
                res["stderr"],
            )
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/stop")
async def stop_service(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_name = get_os_profile(os_res["stdout"].strip()).name
            sid = req.instance_id if req.instance_id and req.instance_id != "default" else "ORCL"
            profile_cmd = build_oracle_profile_command(sid, os_name)
            cmd = f'{profile_cmd}; printf "shutdown immediate;\\nexit;\\n" | sqlplus -s / as sysdba; SQL_EXIT=$?; lsnrctl stop; exit $SQL_EXIT'
            res = await ssh.execute_command(
                process_user="oracle",
                command=cmd,
                timeout=240,
            )
            status_res = await ssh.execute_command(
                process_user=None,
                command=build_oracle_status_command(os_name, sid),
            )
            is_stopped = f"ora_pmon_{sid}" not in status_res["stdout"] or status_res["exit_status"] != 0
            success = res["exit_status"] == 0 or is_stopped
            logs = res["stdout"]
            error = res["stderr"] or ""
            if not success and not error:
                error = logs or "Oracle stop command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            logger.warning(
                "Oracle stop result host=%s os=%s sid=%s exit_status=%s is_stopped=%s stdout=%r stderr=%r",
                req.host,
                os_name,
                sid,
                res["exit_status"],
                is_stopped,
                res["stdout"],
                res["stderr"],
            )
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}
