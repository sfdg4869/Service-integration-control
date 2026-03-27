from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper

router = APIRouter()

class ActionRequest(BaseModel):
    host: str
    port: int = 10022
    username: str
    password: str
    instance_id: str = ""

@router.post("/status")
def check_status(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            user_res = ssh.execute_command(process_user=None, command="id -un maxgauge 2>/dev/null || id -un MaxGauge 2>/dev/null || echo 'maxgauge'")
            actual_user = user_res["stdout"].strip().split("\n")[0]

            if req.instance_id and req.instance_id != "default":
                grep_target = req.instance_id.split("/")[-1] if req.instance_id.startswith("/") else req.instance_id
            else:
                grep_target = "pjs"

            # PlatformJS 실제 데몬만 찾기 위해 본체 이름으로 grep하고, 가짜 SSH 통신 껍데기(bash, ssh, pjsctl)는 완벽히 걸러냄!
            cmd = f"ps -ef | grep -i '{grep_target}' | grep -v grep | grep -v bash | grep -v ssh | grep -v pjsctl; echo '---PWDX_INFO---'; for pid in $(ps -ef | grep -i '{grep_target}' | grep -v grep | grep -v bash | grep -v ssh | grep -v pjsctl | awk '{{print $2}}'); do pwdx $pid 2>/dev/null || procwdx $pid 2>/dev/null || true; done"
            res = ssh.execute_command(process_user=actual_user, command=cmd)
                
            is_running = (grep_target in res["stdout"] and res["exit_status"] == 0)
            return {"status": "running" if is_running else "stopped", "details": res["stdout"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/start")
def start_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            user_res = ssh.execute_command(process_user=None, command="id -un maxgauge 2>/dev/null || id -un MaxGauge 2>/dev/null || echo 'maxgauge'")
            actual_user = user_res["stdout"].strip().split("\n")[0]

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else ""
            if inst.startswith("/"):
                cmd = f'cd "{inst}" && ./pjsctl start >/dev/null 2>&1'
            else:
                cmd = f'PJS_DIR=$(find ~ -type f -name "pjsctl" 2>/dev/null | head -n 1); if [ -n "$PJS_DIR" ]; then cd "$(dirname "$PJS_DIR")" && ./pjsctl start >/dev/null 2>&1; else echo "PJS Path Not Found"; exit 1; fi'
            
            res = ssh.execute_command(process_user=actual_user, command=cmd)
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/stop")
def stop_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            user_res = ssh.execute_command(process_user=None, command="id -un maxgauge 2>/dev/null || id -un MaxGauge 2>/dev/null || echo 'maxgauge'")
            actual_user = user_res["stdout"].strip().split("\n")[0]

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else ""
            if inst.startswith("/"):
                cmd = f'cd "{inst}" && ./pjsctl stop >/dev/null 2>&1'
            else:
                cmd = f'PJS_DIR=$(find ~ -type f -name "pjsctl" 2>/dev/null | head -n 1); if [ -n "$PJS_DIR" ]; then cd "$(dirname "$PJS_DIR")" && ./pjsctl stop >/dev/null 2>&1; else echo "PJS Path Not Found"; exit 1; fi'
            
            res = ssh.execute_command(process_user=actual_user, command=cmd)
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}
