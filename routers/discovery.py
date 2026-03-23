from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper
from typing import List

router = APIRouter()

class ActionRequest(BaseModel):
    host: str
    port: int = 10022
    username: str
    password: str
    target_services: List[str] = ["oracle", "postgres", "rts"]

@router.post("/run")
def discover_services(req: ActionRequest):
    services = []
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 1. Oracle 발견
            if "oracle" in req.target_services:
                res_ora = ssh.execute_command(
                    process_user=None, 
                    command="cat /etc/oratab 2>/dev/null; cat /var/opt/oracle/oratab 2>/dev/null; cat ~/.bash_profile 2>/dev/null; cat /home/oracle/.bash_profile 2>/dev/null; ps -ef | grep pmon | grep -v grep"
                )
            # 출력물에서 ORACLE_SID 파싱 (export ORACLE_SID=ORCL 또는 ora_pmon_ORCL 형태 또는 /etc/oratab)
            ora_found = res_ora["stdout"].strip().split("\n")
            for line in ora_found:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                sid = None
                if ":" in line and len(line.split(":")) >= 2 and line.split(":")[1].startswith("/"):
                    # /etc/oratab format (SID:ORACLE_HOME:Y/N)
                    sid = line.split(":")[0].strip()
                    # +ASM 같은 시스템 계정 제외 처리 원하면 if not sid.startswith("+"): 추가 고려
                elif "ORACLE_SID=" in line:
                    sid = line.split("ORACLE_SID=")[-1].strip()
                elif "ora_pmon_" in line:
                    # 프로세스 명에서 SID 추출
                    parts = line.split("ora_pmon_")
                    if len(parts) > 1:
                        sid = parts[-1].strip()
                
                if sid and sid not in [s["instance_id"] for s in services if s["type"] == "oracle"]:
                    services.append({"type": "oracle", "name": f"Oracle DB ({sid})", "instance_id": sid, "run_as": "oracle"})
            
            # (임시) 만약 bash_profile에서 찾지 못하면 디폴트 1개 추가
            if "oracle" in req.target_services:
                if not any(s["type"] == "oracle" for s in services):
                    services.append({"type": "oracle", "name": "Oracle DB (Default)", "instance_id": "ORCL", "run_as": "oracle"})

            # 2. PostgreSQL 발견 (임의 로직)
            if "postgres" in req.target_services:
                services.append({
                    "type": "postgres", 
                    "name": "PostgreSQL (Default)", 
                    "instance_id": "main", 
                    "run_as": "postgres"
                })

            # 3. RTS 발견 (동적 파싱)
            if "rts" in req.target_services:
                res_rts = ssh.execute_command(
                    process_user=None,
                    command="ps -ef | grep mxg_rts | grep -v grep || echo ''"
                )
                rts_lines = res_rts["stdout"].strip().split("\n")
                rts_found = False
                for line in rts_lines:
                    if "-c" in line:
                        parts = line.split("-c")
                        if len(parts) > 1:
                            instance_name = parts[1].strip().split()[0]
                            if instance_name and instance_name not in [s["instance_id"] for s in services if s["type"] == "rts"]:
                                services.append({"type": "rts", "name": f"RTS ({instance_name})", "instance_id": instance_name, "run_as": "maxgauge"})
                                rts_found = True
                
                # 실행 중인 프로세스가 없으면 기본 생성
                if not rts_found:
                    services.append({
                        "type": "rts", 
                        "name": "RTS Process (Default)", 
                        "instance_id": "default", 
                        "run_as": "maxgauge"
                    })

        return {"success": True, "services": services}
    except Exception as e:
        return {"success": False, "error": str(e)}
