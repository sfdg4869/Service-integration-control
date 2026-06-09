from fastapi import APIRouter
from pydantic import BaseModel
import shlex

from services.os_profiles import get_os_profile
from services.ssh_client import SSHClientWrapper
from services.maxgauge_commands import MAXGAUGE_USER_COMMAND
from services.runtime_process_ports import (
    build_runtime_process_port_command,
    parse_runtime_process_port_output,
)


router = APIRouter()


class RuntimePortRequest(BaseModel):
    host: str
    port: int = 10022
    username: str
    password: str
    service_type: str


@router.post("/status")
async def get_runtime_ports(req: RuntimePortRequest):
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_profile = get_os_profile(os_res["stdout"].strip())
            normalized_type = (req.service_type or "").strip().lower()

            if os_profile.name != "Linux":
                return {"success": True, "service_type": req.service_type, "items": [], "supported": False}

            user_res = await ssh.execute_command(process_user=None, command=MAXGAUGE_USER_COMMAND)
            process_user = user_res["stdout"].strip().split("\n")[0] or "maxgauge"
            command = build_runtime_process_port_command(req.service_type, os_profile)
            if normalized_type == "oracle":
                command = f"bash -lc {shlex.quote(command)}"
            res = await ssh.execute_command(process_user=process_user, command=command, timeout=60)
            items = [
                {
                    "service_type": item.service_type,
                    "key": item.key,
                    "pid": item.pid,
                    "ports": list(item.ports),
                    "base_path": item.base_path,
                    "version": item.version,
                }
                for item in parse_runtime_process_port_output(req.service_type, res["stdout"])
            ]
            return {
                "success": True,
                "service_type": req.service_type,
                "supported": True,
                "items": items,
                "details": res["stdout"],
            }
    except Exception as e:
        return {"success": False, "service_type": req.service_type, "error": str(e), "items": []}
