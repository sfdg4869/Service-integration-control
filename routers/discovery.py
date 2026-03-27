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
    target_services: List[str] = ["oracle", "postgres", "rts", "dg", "pjs"]

@router.post("/run")
def discover_services(req: ActionRequest):
    services = []
    try:
        with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 0. OS 판별 (Linux, HP-UX, AIX, SunOS 등 지원)
            os_res = ssh.execute_command(process_user=None, command="uname -s")
            os_info = os_res["stdout"].strip()

            if os_info in ["HP-UX", "AIX", "SunOS"]:
                # 전통적 유닉스에서는 getent 대신 cat /etc/passwd 및 표준 grep 사용
                get_dir_cmd = "grep '^maxgauge:' /etc/passwd | cut -d: -f6 2>/dev/null || grep '^MaxGauge:' /etc/passwd | cut -d: -f6 2>/dev/null"
                find_opt = "" # 리눅스 전용 -maxdepth 제외
            else:
                # 리눅스는 getent 및 고속 -maxdepth 지원
                get_dir_cmd = "getent passwd maxgauge | cut -d: -f6 2>/dev/null || getent passwd MaxGauge | cut -d: -f6 2>/dev/null"
                find_opt = "-maxdepth 5"

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
                if ":" in line and len(line.split(":")) >= 2 and line.split(":")[1].strip().startswith("/"):
                    # /etc/oratab format (SID:ORACLE_HOME:Y/N)
                    potential_sid = line.split(":")[0].strip()
                    # 엉뚱한 쉘 환경변수(PATH=..., export ...)가 잡히지 않도록 필터링 (순수 SID는 공백과 '=' 가 없음)
                    if " " not in potential_sid and "=" not in potential_sid and not potential_sid.startswith("#"):
                        sid = potential_sid
                elif "ORACLE_SID=" in line:
                    # export ORACLE_SID=ORCL 파싱
                    sid = line.split("ORACLE_SID=")[-1].strip().split()[0]
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

            # 3. RTS 발견 (실행 중 프로세스 + 정지된 인스턴스 폴더 스캔)
            if "rts" in req.target_services or "dg" in req.target_services: # rts나 dg 스캔이 필요할 때만
                rts_instances = {}
                dg_instances = {}

                # 3-0. RTS/MaxGauge 실제 계정명만 간단히 추출 (run_as 표시를 위함)
                mxg_info_res = ssh.execute_command(
                    process_user=None,
                    command="id -un maxgauge 2>/dev/null || id -un MaxGauge 2>/dev/null || echo 'maxgauge'"
                )
                actual_mxg_user = mxg_info_res["stdout"].strip().split("\n")[0]

                # 3-1. 실행 중인 프로세스에서 찾기 (프로세스 기반)
                res_rts = ssh.execute_command(
                    process_user=None,
                    command="ps -ef | grep -E 'mxg_rts|mxg_dgs' | grep -v grep || echo ''"
                )
                for line in res_rts["stdout"].strip().split("\n"):
                    if "-c" in line:
                        parts = line.split("-c")
                        if len(parts) > 1:
                            instance_name = parts[1].strip().split()[0]
                            if instance_name:
                                if "mxg_dgs" in line or "DGServer" in instance_name:
                                    dg_instances[instance_name] = "알 수 없음 (Process)"
                                else:
                                    rts_instances[instance_name] = "알 수 없음 (Process)"
                                
                # 3-2. 가장 원시적이고 순수한 방법으로 대상 경로만 잡아내 뒤지기 (오류 방지)
                # sudo 구문이나 bash 중첩 등을 제거하여 작은따옴표 에러조차 발생하지 않게 확정 검색
                command_str = f"""
TARGET_DIR=$({get_dir_cmd})
[ -z "$TARGET_DIR" ] && TARGET_DIR=~
# OS별 맞춤형 탐색 옵션 적용 (-L 제거하여 심볼릭 루프 방지)
find "$TARGET_DIR" {find_opt} -type f -name '.mxgrc' 2>/dev/null
"""
                res_file = ssh.execute_command(
                    process_user=None,
                    command=command_str.strip()
                )
                for line in res_file["stdout"].strip().split("\n"):
                    line = line.strip()
                    if line and line.endswith('.mxgrc'):
                        folder_path = line.replace("/.mxgrc", "")
                        instance_name = folder_path.split("/")[-1]
                        if instance_name and instance_name not in ["", ".", ".."]:
                            # DG와 RTS 완벽 분리 적용
                            if "DGServer" in instance_name:
                                if instance_name in dg_instances:
                                    del dg_instances[instance_name]
                                if instance_name in rts_instances:
                                    del rts_instances[instance_name]
                                dg_instances[folder_path] = instance_name
                            else:
                                if instance_name in rts_instances:
                                    del rts_instances[instance_name]
                                if instance_name in dg_instances:
                                    del dg_instances[instance_name]
                                rts_instances[folder_path] = instance_name

                # 서비스 배열에 추가 (RTS)
                for key, val in rts_instances.items():
                    inst_id = key
                    services.append({
                        "type": "rts", 
                        "name": f"RTS ({inst_id})", 
                        "instance_id": inst_id, 
                        "run_as": actual_mxg_user
                    })

                # 서비스 배열에 추가 (DG)
                for key, val in dg_instances.items():
                    inst_id = key
                    services.append({
                        "type": "dg", 
                        "name": f"DG ({inst_id})", 
                        "instance_id": inst_id, 
                        "run_as": actual_mxg_user
                    })
                
                # 실행 중인 프로세스도 없고 파일도 하나도 없으면 기본적으로 1개 노출
                if not rts_instances:
                    services.append({
                        "type": "rts", 
                        "name": "RTS Process (Default)", 
                        "instance_id": "default", 
                        "path": "알 수 없음",
                        "run_as": actual_mxg_user
                    })

            # 4. PJS 발견 로직 추가
            if "pjs" in req.target_services:
                pjs_instances = {}
                mxg_info_res = ssh.execute_command(
                    process_user=None,
                    command="id -un maxgauge 2>/dev/null || id -un MaxGauge 2>/dev/null || echo 'maxgauge'"
                )
                actual_mxg_user = mxg_info_res["stdout"].strip().split("\n")[0]
                
                command_str = f"""
TARGET_DIR=$({get_dir_cmd})
[ -z "$TARGET_DIR" ] && TARGET_DIR=~
# OS별 맞춤형 탐색 옵션 적용 (-L 제거하여 심볼릭 루프 방지)
find "$TARGET_DIR" {find_opt} -type f -name 'pjsctl' 2>/dev/null
"""
                res_pjs = ssh.execute_command(process_user=None, command=command_str.strip())
                for line in res_pjs["stdout"].strip().split("\n"):
                    line = line.strip()
                    if line and line.endswith('/pjsctl'):
                        folder_path = line.replace("/pjsctl", "") # /.../pjs 형식 추출
                        if folder_path and folder_path not in ["", ".", ".."]:
                            pjs_instances[folder_path] = folder_path.split("/")[-1]

                for key, val in pjs_instances.items():
                    inst_id = key
                    services.append({
                        "type": "pjs", 
                        "name": f"PJS ({inst_id})", 
                        "instance_id": inst_id, 
                        "run_as": actual_mxg_user
                    })

        return {"success": True, "services": services}
    except Exception as e:
        return {"success": False, "error": str(e)}
