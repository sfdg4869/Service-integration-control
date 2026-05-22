from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper
from typing import List
from services.os_profiles import get_os_profile
from services.maxgauge_commands import MAXGAUGE_USER_COMMAND, build_maxgauge_find_all_command, build_maxgauge_find_command
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


def _strip_known_suffix(path: str, suffixes: list[str]) -> str:
    for suffix in suffixes:
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return path


def _build_targeted_find_command(home_command: str, target_type: str, names: list[str]) -> str:
    name_expr = " -o ".join([f'-name "{name}"' for name in names])
    return (
        f'D=$({home_command}); '
        f'[ -z "$D" ] && D=~; '
        f'find "$D" -type {target_type} \\( {name_expr} \\) 2>/dev/null'
    )

class ActionRequest(BaseModel):
    host: str
    port: int = 10022
    username: str
    password: str
    target_services: List[str] = ["oracle", "postgres", "rts", "dg", "pjs"]

@router.post("/run")
async def discover_services(req: ActionRequest):
    services = []
    debug_info = []
    try:
        async with SSHClientWrapper(req.host, req.port, req.username, req.password) as ssh:
            # 0. OS 판별
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_info = os_res["stdout"].strip()
            os_profile = get_os_profile(os_info)
            debug_info.append(f"OS Info: {os_info}")

            # 1. Oracle 발견
            if "oracle" in req.target_services:
                res_ora = await ssh.execute_command(None, "cat /etc/oratab /var/opt/oracle/oratab 2>/dev/null; ps -ef | grep pmon | grep -v grep")
                ora_lines = res_ora["stdout"].strip().split("\n")
                added_ora = False
                for line in ora_lines:
                    line = line.strip()
                    if ":" in line and "/" in line and not line.startswith("#"):
                        sid = line.split(":")[0]
                        if sid:
                            services.append({"type": "oracle", "name": f"Oracle DB ({sid})", "instance_id": sid, "run_as": "oracle"})
                            added_ora = True
                    elif "ora_pmon_" in line:
                        sid = line.split("ora_pmon_")[-1].strip()
                        if sid and not any(s["instance_id"] == sid for s in services if s["type"] == "oracle"):
                            services.append({"type": "oracle", "name": f"Oracle DB ({sid})", "instance_id": sid, "run_as": "oracle"})
                            added_ora = True
                
                # 하나도 발견되지 않았더라도 최소 1개는 표시
                if not added_ora:
                    services.append({"type": "oracle", "name": "Oracle DB (Default)", "instance_id": "ORCL", "run_as": "oracle"})

            # 2. PostgreSQL
            if "postgres" in req.target_services:
                services.append({"type": "postgres", "name": "PostgreSQL (Default)", "instance_id": "main", "run_as": "postgres"})

            # 3. RTS & DG 발견
            if "rts" in req.target_services or "dg" in req.target_services:
                rts_instances = {}
                dg_instances = {}
                
                user_res = await ssh.execute_command(None, MAXGAUGE_USER_COMMAND)
                actual_user = user_res["stdout"].strip().split("\n")[0]
                status_user = actual_user if os_profile.name == "SunOS" else None

                # ps 기반 선탐색 (POSIX 호환)
                ps_res = await ssh.execute_command(status_user, f"{os_profile.ps_command} | egrep 'mxg_(rts|dgs|dg|obsd|updater|sndf)|DGServer|DataGather' | grep -v grep || true")
                for line in ps_res["stdout"].strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue

                    if "mxg_dgs" in line or "DGServer" in line:
                        if "-c" in line:
                            parts = line.split("-c")
                            if len(parts) > 1:
                                iname = parts[1].strip().split()[0]
                                if iname:
                                    dg_instances[iname] = "Process"
                                    continue
                        dg_instances["default"] = "DGServer"
                    elif "mxg_rts" in line:
                        if "-c" in line:
                            parts = line.split("-c")
                            if len(parts) > 1:
                                iname = parts[1].strip().split()[0]
                                if iname:
                                    rts_instances[iname] = "Process"

                # 파일 기반 정밀 탐색
                if os_profile.name == "SunOS":
                    mxgrc_cmd = _build_targeted_find_command(os_profile.maxgauge_home_command, "f", [".mxgrc"])
                    res_mxgrc = await ssh.execute_command(None, mxgrc_cmd, timeout=90)
                else:
                    mxgrc_cmd = build_maxgauge_find_all_command(os_profile, r"/\.mxgrc$", "-type f")
                    res_mxgrc = await ssh.execute_command(None, mxgrc_cmd)
                for line in res_mxgrc["stdout"].strip().split("\n"):
                    line = line.strip()
                    if line.endswith(".mxgrc"):
                        path = line.replace("/.mxgrc", "")
                        name = path.split("/")[-1]
                        if name not in ["", ".", ".."]:
                            if name in rts_instances: del rts_instances[name]
                            rts_instances[path] = name

                # dgsctl 검색하여 DG로 분류
                if os_profile.name == "SunOS":
                    dgs_cmd = _build_targeted_find_command(os_profile.maxgauge_home_command, "f", ["dgsctl", "dgsctl.sh", "dgctl", "dgctl.sh"])
                    res_dgs = await ssh.execute_command(None, dgs_cmd, timeout=90)
                else:
                    dgs_cmd = build_maxgauge_find_all_command(os_profile, r"/(dgsctl|dgctl)(\.sh)?$")
                    res_dgs = await ssh.execute_command(None, dgs_cmd)
                logger.warning("DG discovery host=%s os=%s cmd=%r stdout=%r stderr=%r", req.host, os_profile.name, dgs_cmd, res_dgs["stdout"], res_dgs["stderr"])
                for line in res_dgs["stdout"].strip().split("\n"):
                    line = line.strip()
                    if any(token in line.lower() for token in ["dgsctl", "dgctl"]):
                        bin_p = _strip_known_suffix(line, ["/dgsctl", "/dgsctl.sh", "/dgctl", "/dgctl.sh"])
                        dg_p = bin_p[:-4] if bin_p.endswith("/bin") else bin_p
                        if dg_p in rts_instances:
                            del rts_instances[dg_p]
                        dg_instances[dg_p] = dg_p.split("/")[-1]

                for p, n in rts_instances.items():
                    services.append({"type": "rts", "name": f"RTS ({p})", "instance_id": p, "run_as": actual_user})
                for p, n in dg_instances.items():
                    services.append({"type": "dg", "name": f"DG ({p})", "instance_id": p, "run_as": actual_user})
                debug_info.append(f"RTS discovered: {list(rts_instances.keys())}")
                debug_info.append(f"DG discovered: {list(dg_instances.keys())}")

                if not any(s["type"] == "rts" for s in services):
                    services.append({"type": "rts", "name": "RTS (Default)", "instance_id": "default", "run_as": actual_user})
                if not any(s["type"] == "dg" for s in services):
                    services.append({"type": "dg", "name": "DG (Default)", "instance_id": "default", "run_as": actual_user})

            # 4. PJS 발견
            if "pjs" in req.target_services:
                pjs_instances = {}
                pjs_proc_cmd = f"{os_profile.ps_command} | egrep -i 'pjs|platformjs|node|npm' | grep -v grep | grep -v bash | grep -v ssh | grep -v pjsctl || true"
                res_pjs_proc = await ssh.execute_command(status_user if 'status_user' in locals() else None, pjs_proc_cmd)
                for line in res_pjs_proc["stdout"].strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if "pjsctl" in line:
                        continue
                    pjs_instances["default"] = "PlatformJS"

                if os_profile.name == "SunOS":
                    pjs_cmd = _build_targeted_find_command(os_profile.maxgauge_home_command, "f", ["pjsctl", "pjsctl.sh"])
                    res_pjs = await ssh.execute_command(None, pjs_cmd, timeout=90)
                else:
                    pjs_cmd = build_maxgauge_find_all_command(os_profile, r"/pjsctl(\.sh)?$")
                    res_pjs = await ssh.execute_command(None, pjs_cmd)
                logger.warning("PJS discovery host=%s os=%s cmd=%r stdout=%r stderr=%r", req.host, os_profile.name, pjs_cmd, res_pjs["stdout"], res_pjs["stderr"])
                for line in res_pjs["stdout"].strip().split("\n"):
                    line = line.strip()
                    if any(line.endswith(suffix) for suffix in ["/pjsctl", "/pjsctl.sh"]):
                        p = _strip_known_suffix(line, ["/pjsctl", "/pjsctl.sh"])
                        pjs_instances[p] = p.split("/")[-1]

                m_user = actual_user if 'actual_user' in locals() else "maxgauge"
                for p, n in pjs_instances.items():
                    services.append({"type": "pjs", "name": f"PJS ({p})", "instance_id": p, "run_as": m_user})
                if not any(s["type"] == "pjs" for s in services):
                    services.append({"type": "pjs", "name": "PJS (Default)", "instance_id": "default", "run_as": m_user})
                debug_info.append(f"PJS discovered: {list(pjs_instances.keys())}")

        return {"success": True, "services": services, "debug": debug_info}
    except Exception as e:
        return {"success": False, "error": str(e), "debug": debug_info}
