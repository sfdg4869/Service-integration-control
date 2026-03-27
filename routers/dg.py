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

            # DG 상태 조회 (mxg_dgs 프로세스 또는 폴더명 기준 스캔 및 구동 경로 pwdx 색출)
            cmd = "ps -ef | grep -E 'mxg_dgs|DGServer' | grep -v grep; echo '---PWDX_INFO---'; for pid in $(ps -ef | grep -E 'mxg_dgs|DGServer' | grep -v grep | awk '{print $2}'); do pwdx $pid 2>/dev/null || procwdx $pid 2>/dev/null || true; done"
            res = ssh.execute_command(process_user=actual_user, command=cmd)
            
            if req.instance_id and req.instance_id != "default":
                grep_target = req.instance_id.split("/")[-1] if req.instance_id.startswith("/") else req.instance_id
            else:
                grep_target = "DGServer"
                
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
                cmd = f'cd "{inst}" 2>/dev/null && . .mxgrc && dgsctl start'
            else:
                cmd = f'DGS_DIR=$(find ~ -type d -name "{inst}" 2>/dev/null | head -n 1); if [ -n "$DGS_DIR" ]; then cd "$DGS_DIR" && . .mxgrc && dgsctl start; else echo "DG Path Not Found"; exit 1; fi'
            
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
                cmd = f'cd "{inst}" 2>/dev/null && . .mxgrc && dgsctl stop'
            else:
                cmd = f'DGS_DIR=$(find ~ -type d -name "{inst}" 2>/dev/null | head -n 1); if [ -n "$DGS_DIR" ]; then cd "$DGS_DIR" && . .mxgrc && dgsctl stop; else echo "DG Path Not Found"; exit 1; fi'
            
            res = ssh.execute_command(process_user=actual_user, command=cmd)
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}
