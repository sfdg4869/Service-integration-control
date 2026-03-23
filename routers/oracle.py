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
                command="ps -ef | grep pmon | grep -v grep"
            )
            # pmon이 떠 있으면 구동 중으로 간주
            is_running = ("pmon" in res["stdout"] and res["exit_status"] == 0)
            return {"status": "running" if is_running else "stopped", "details": res["stdout"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/start")
def start_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 1. 홈경로 프로파일(.ora*, .profile* 등)의 '내용'을 쫙 스캔하여 ORACLE_SID=해당숫자가 적힌 진짜 파일을 찾아 로드, 없으면 기본 profile
            sid = req.instance_id if req.instance_id and req.instance_id != "default" else "ORCL"
            profile_cmd = f"SID_NUM=$(echo '{sid}' | sed 's/^[Oo][Rr][Aa]//' | sed 's/^[Hh][Pp]//' | sed 's/^[Cc][Dd][Bb]//'); PROFILE=$(grep -i -l \"ORACLE_SID.*$SID_NUM\" ~/.ora* ~/.profile* ~/.bash_profile 2>/dev/null | grep -v \"_empty\" | head -n 1); if [ -n \"$PROFILE\" ]; then . \"$PROFILE\"; else . ~/.bash_profile 2>/dev/null; fi"
            cmd = f'{profile_cmd} && export ORACLE_SID={sid} && lsnrctl start && echo "startup;" | sqlplus -s / as sysdba'
            res = ssh.execute_command(
                process_user="oracle", 
                command=cmd
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/stop")
def stop_service(req: ActionRequest):
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 1. 홈경로 프로파일(.ora*, .profile* 등)의 '내용'을 쫙 스캔하여 ORACLE_SID=해당숫자가 적힌 진짜 파일을 찾아 로드, 없으면 기본 profile
            sid = req.instance_id if req.instance_id and req.instance_id != "default" else "ORCL"
            profile_cmd = f"SID_NUM=$(echo '{sid}' | sed 's/^[Oo][Rr][Aa]//' | sed 's/^[Hh][Pp]//' | sed 's/^[Cc][Dd][Bb]//'); PROFILE=$(grep -i -l \"ORACLE_SID.*$SID_NUM\" ~/.ora* ~/.profile* ~/.bash_profile 2>/dev/null | grep -v \"_empty\" | head -n 1); if [ -n \"$PROFILE\" ]; then . \"$PROFILE\"; else . ~/.bash_profile 2>/dev/null; fi"
            cmd = f'{profile_cmd} && export ORACLE_SID={sid} && lsnrctl stop && echo "shutdown immediate;" | sqlplus -s / as sysdba'
            res = ssh.execute_command(
                process_user="oracle", 
                command=cmd
            )
            return {"success": res["exit_status"] == 0, "logs": res["stdout"], "error": res["stderr"]}
    except Exception as e:
        return {"success": False, "error": str(e)}
