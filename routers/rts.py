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
            # 상태 조회 (특정 instance_id 포함)
            grep_target = req.instance_id if req.instance_id and req.instance_id != "default" else "rts"
            cmd = f"ps -ef | grep mxg_rts | grep '{grep_target}' | grep -v grep"
            res = ssh.execute_command(
                process_user=None, 
                command=cmd
            )
            is_running = (grep_target in res["stdout"] and res["exit_status"] == 0)
            return {"status": "running" if is_running else "stopped", "details": res["stdout"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/start")
def start_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 경로 고정 대신, maxgauge 홈에서 인스턴스 이름과 동일한 폴더를 자동으로 스캔(.mxgrc가 있는 폴더)
            inst = req.instance_id if req.instance_id and req.instance_id != "default" else "rts"
            cmd = f'RTS_DIR=$(find ~ -type d -name "{inst}" 2>/dev/null | head -n 1); if [ -n "$RTS_DIR" ]; then cd "$RTS_DIR" && . .mxgrc && rtsctl start; else echo "RTS Path Not Found"; exit 1; fi'
            res = ssh.execute_command(
                process_user="maxgauge", 
                command=cmd
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/stop")
def stop_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            inst = req.instance_id if req.instance_id and req.instance_id != "default" else "rts"
            cmd = f'RTS_DIR=$(find ~ -type d -name "{inst}" 2>/dev/null | head -n 1); if [ -n "$RTS_DIR" ]; then cd "$RTS_DIR" && . .mxgrc && rtsctl stop; else echo "RTS Path Not Found"; exit 1; fi'
            res = ssh.execute_command(
                process_user="maxgauge", 
                command=cmd
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}
