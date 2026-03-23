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
            # 상태 조회는 접속한 계정 권한(None)으로 가볍게 ps -ef 스캔
            res = ssh.execute_command(
                process_user=None, 
                command="ps -ef | grep postgres | grep -v grep"
            )
            # 출력물에 postgres가 존재하고 exit_status가 0이면 구동 중으로 간주
            is_running = ("postgres" in res["stdout"] and res["exit_status"] == 0)
            return {"status": "running" if is_running else "stopped", "details": res["stdout"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/start")
def start_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 임시 시작 명령어 (환경에 맞게 수정 필요)
            res = ssh.execute_command(
                process_user="postgres", 
                command="pg_ctl start -D /var/lib/pgsql/data"
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/stop")
def stop_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 임시 정지 명령어 (환경에 맞게 수정 필요)
            res = ssh.execute_command(
                process_user="postgres", 
                command="pg_ctl stop -D /var/lib/pgsql/data"
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}
