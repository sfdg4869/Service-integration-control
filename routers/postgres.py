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


def build_postgres_status_command(os_name: str) -> str:
    ps_cmd = "UNIX95=1 ps -ef" if os_name in {"AIX", "HP-UX", "SunOS"} else "ps -ef"
    return f"{ps_cmd} | grep postgres | grep -v grep || true"


def build_pg_ctl_command(action: str) -> str:
    return f"""
if [ -f ~/.profile ]; then . ~/.profile 2>/dev/null || true; fi
if [ -f ~/.bash_profile ]; then . ~/.bash_profile 2>/dev/null || true; fi
if [ -f ~/.bashrc ]; then . ~/.bashrc 2>/dev/null || true; fi
if [ -f ~/.kshrc ]; then . ~/.kshrc 2>/dev/null || true; fi
echo "RUN_USER=`id -un 2>/dev/null || whoami 2>/dev/null || echo unknown`"
echo "PATH=$PATH"
PG_CTL_BIN="${{PG_CTL_BIN:-}}"
if [ -z "$PG_CTL_BIN" ]; then
  PG_CTL_BIN=`command -v pg_ctl 2>/dev/null || true`
fi
if [ -z "$PG_CTL_BIN" ]; then
  for p in /usr/pgsql-*/bin/pg_ctl /usr/local/pgsql/bin/pg_ctl /usr/postgres/*/bin/pg_ctl /opt/postgres/*/bin/pg_ctl; do
    if [ -x "$p" ]; then
      PG_CTL_BIN="$p"
      break
    fi
  done
fi
echo "PG_CTL_BIN=$PG_CTL_BIN"
PGDATA_DIR="${{PGDATA:-}}"
if [ -z "$PGDATA_DIR" ]; then
  for d in /var/lib/pgsql/data /var/lib/pgsql/*/data /pgdata /pgdata/* /postgres/data /var/opt/postgres/data; do
    if [ -d "$d" ]; then
      PGDATA_DIR="$d"
      break
    fi
  done
fi
echo "PGDATA_DIR=$PGDATA_DIR"
if [ -z "$PG_CTL_BIN" ]; then
  echo "pg_ctl not found"
  exit 1
fi
if [ -z "$PGDATA_DIR" ]; then
  echo "PGDATA directory not found"
  exit 1
fi
"$PG_CTL_BIN" {action} -D "$PGDATA_DIR" 2>&1
PG_EXIT=$?
echo "PG_EXIT=$PG_EXIT"
exit $PG_EXIT
"""


@router.post("/status")
async def check_status(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_name = get_os_profile(os_res["stdout"].strip()).name
            res = await ssh.execute_command(
                process_user=None,
                command=build_postgres_status_command(os_name),
            )
            is_running = ("postgres" in res["stdout"] and res["exit_status"] == 0)
            return {"status": "running" if is_running else "stopped", "details": res["stdout"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/start")
async def start_service(req: ActionRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_name = get_os_profile(os_res["stdout"].strip()).name
            res = await ssh.execute_command(
                process_user="postgres",
                command=build_pg_ctl_command("start"),
                timeout=180,
            )
            status_res = await ssh.execute_command(
                process_user=None,
                command=build_postgres_status_command(os_name),
            )
            is_running = "postgres" in status_res["stdout"] and status_res["exit_status"] == 0
            success = res["exit_status"] == 0 or is_running
            logs = res["stdout"]
            error = res["stderr"] or ""
            if not success and not error:
                error = logs or "PostgreSQL start command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            logger.warning(
                "Postgres start result host=%s os=%s exit_status=%s is_running=%s stdout=%r stderr=%r",
                req.host,
                os_name,
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
            res = await ssh.execute_command(
                process_user="postgres",
                command=build_pg_ctl_command("stop"),
                timeout=180,
            )
            status_res = await ssh.execute_command(
                process_user=None,
                command=build_postgres_status_command(os_name),
            )
            is_stopped = "postgres" not in status_res["stdout"] or status_res["exit_status"] != 0
            success = res["exit_status"] == 0 or is_stopped
            logs = res["stdout"]
            error = res["stderr"] or ""
            if not success and not error:
                error = logs or "PostgreSQL stop command failed without stderr output."
            if not success:
                logs = f"{logs}\nEXIT_STATUS={res['exit_status']}\nCOMMAND={res['command_executed']}\nSTATUS_OUTPUT={status_res['stdout']}".strip()
            logger.warning(
                "Postgres stop result host=%s os=%s exit_status=%s is_stopped=%s stdout=%r stderr=%r",
                req.host,
                os_name,
                res["exit_status"],
                is_stopped,
                res["stdout"],
                res["stderr"],
            )
            return {"success": success, "logs": logs, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}
