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
            if req.instance_id and req.instance_id != "default":
                if req.instance_id.startswith("/"):
                    # instance_id가 절대 경로일 경우 폴더명을 추출하여 프로세스 grep에 사용
                    grep_target = req.instance_id.split("/")[-1]
                else:
                    grep_target = req.instance_id
            else:
                grep_target = "rts"

            user_res = ssh.execute_command(process_user=None, command="id -un maxgauge 2>/dev/null || id -un MaxGauge 2>/dev/null || echo 'maxgauge'")
            actual_user = user_res["stdout"].strip().split("\n")[0]

            cmd = "ps -ef | grep mxg_rts | grep -v grep; echo '---PWDX_INFO---'; for pid in $(ps -ef | grep mxg_rts | grep -v grep | awk '{print $2}'); do pwdx $pid 2>/dev/null || procwdx $pid 2>/dev/null || true; done"
            res = ssh.execute_command(
                process_user=actual_user, 
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
            user_res = ssh.execute_command(process_user=None, command="grep -i '^maxgauge' /etc/passwd | cut -d: -f1 | head -n 1")
            actual_user = user_res["stdout"].strip() or "maxgauge"

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else "rts"
            if inst.startswith("/"):
                # 인스턴스 ID가 절대경로일 경우 검색할 필요 없이 즉시 cd
                cmd = f'cd "{inst}" 2>/dev/null && . .mxgrc && rtsctl start'
            else:
                cmd = f'RTS_DIR=$(find ~ -type d -name "{inst}" 2>/dev/null | head -n 1); if [ -n "$RTS_DIR" ]; then cd "$RTS_DIR" && . .mxgrc && rtsctl start; else echo "RTS Path Not Found"; exit 1; fi'
            res = ssh.execute_command(
                process_user=actual_user, 
                command=cmd
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/stop")
def stop_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            user_res = ssh.execute_command(process_user=None, command="grep -i '^maxgauge' /etc/passwd | cut -d: -f1 | head -n 1")
            actual_user = user_res["stdout"].strip() or "maxgauge"

            inst = req.instance_id if req.instance_id and req.instance_id != "default" else "rts"
            if inst.startswith("/"):
                # 인스턴스 ID가 절대경로일 경우 검색할 필요 없이 즉시 cd
                cmd = f'cd "{inst}" 2>/dev/null && . .mxgrc && rtsctl stop'
            else:
                cmd = f'RTS_DIR=$(find ~ -type d -name "{inst}" 2>/dev/null | head -n 1); if [ -n "$RTS_DIR" ]; then cd "$RTS_DIR" && . .mxgrc && rtsctl stop; else echo "RTS Path Not Found"; exit 1; fi'
            res = ssh.execute_command(
                process_user=actual_user, 
                command=cmd
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}

