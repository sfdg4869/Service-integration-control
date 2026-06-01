from fastapi import APIRouter
from pydantic import BaseModel
from services.ssh_client import SSHClientWrapper
from typing import List
from services.os_profiles import get_os_profile
from services.maxgauge_commands import (
    MAXGAUGE_USER_COMMAND,
    build_maxgauge_find_all_command,
)
import logging
import re

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


def _extract_instance_name(line: str) -> str:
    match = re.search(r"(?:^|\s)-c\s+(\S+)", line)
    if match:
        return match.group(1)
    match = re.search(r"(?:^|\s)c\s+(\S+)", line)
    if match:
        return match.group(1)
    return ""


def _extract_process_name(line: str) -> str:
    match = re.search(r"(mxg_(?:rts|obsd|updater|sndf|dgs|dg))", line, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    lower_line = line.lower()
    if "dgserver" in lower_line:
        return "dgserver"
    if "datagather" in lower_line:
        return "datagather"
    return ""


def _normalize_process_path(raw_path: str, instance_name: str = "") -> str:
    path = raw_path.strip().rstrip("/")
    if not path:
        return ""

    conf_suffix = f"/conf/{instance_name}" if instance_name else ""
    if conf_suffix and path.endswith(conf_suffix):
        return path[: -len(conf_suffix)]

    if "/conf/" in path:
        return path.split("/conf/")[0]

    return path


def _parse_pwdx_section(status_output: str) -> dict[str, str]:
    parts = status_output.split("---PWDX_INFO---", 1)
    if len(parts) < 2:
        return {}

    path_map: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        pid, path = line.split(":", 1)
        pid = pid.strip()
        path = path.strip()
        if pid and path:
            path_map[pid] = path
    return path_map


def _build_rts_child_processes(running_processes: set[str], updater_available: bool) -> dict[str, dict[str, bool]]:
    return {
        "obsd": {
            "running": "obsd" in running_processes,
            "available": True,
        },
        "sndf": {
            "running": "sndf" in running_processes,
            "available": True,
        },
        "updater": {
            "running": "updater" in running_processes,
            "available": updater_available or "updater" in running_processes,
        },
    }


def _build_updater_presence_command(instance_path: str) -> str:
    return (
        f'if [ -f "{instance_path}/bin/mxg_updater" ] || [ -f "{instance_path}/mxg_updater" ]; then '
        'echo yes; '
        'else '
        'echo no; '
        'fi'
    )


def _build_obsd_presence_command(instance_path: str) -> str:
    return (
        f'if [ -f "{instance_path}/conf/observer.conf" ] || [ -f "{instance_path}/observer.conf" ]; then '
        'echo yes; '
        'elif find "' + instance_path + '" -type f \\( -name "observer.conf" -o -name "*observer*.conf" -o -name "mxg_obsd" \\) 2>/dev/null | head -n 1 | grep -q .; then '
        'echo yes; '
        'else '
        'echo no; '
        'fi'
    )


def _build_single_child_process(process_name: str, running_processes: set[str], available: bool) -> dict[str, dict[str, bool]]:
    return {
        process_name: {
            "running": process_name in running_processes,
            "available": available or process_name in running_processes,
        }
    }


def _extract_process_state_map(status_output: str, process_names: set[str]) -> dict[str, set[str]]:
    path_map = _parse_pwdx_section(status_output)
    state_map: dict[str, set[str]] = {}

    for line in status_output.split("---PWDX_INFO---", 1)[0].splitlines():
        line = line.strip()
        if not line:
            continue

        proc_name = _extract_process_name(line).replace("mxg_", "")
        if proc_name not in process_names:
            continue

        fields = line.split()
        pid = fields[1] if len(fields) > 1 else ""
        instance_name = _extract_instance_name(line)
        runtime_path = _normalize_process_path(path_map.get(pid, ""), instance_name)
        keys = [runtime_path, instance_name]
        for key in keys:
            if not key:
                continue
            state_map.setdefault(key, set()).add(proc_name)

    return state_map


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
            os_res = await ssh.execute_command(process_user=None, command="uname -s")
            os_info = os_res["stdout"].strip()
            os_profile = get_os_profile(os_info)
            debug_info.append(f"OS Info: {os_info}")

            if "oracle" in req.target_services:
                res_ora = await ssh.execute_command(
                    None,
                    "cat /etc/oratab /var/opt/oracle/oratab 2>/dev/null; ps -ef | grep pmon | grep -v grep",
                )
                ora_lines = res_ora["stdout"].strip().split("\n")
                added_ora = False
                for line in ora_lines:
                    line = line.strip()
                    if ":" in line and "/" in line and not line.startswith("#"):
                        sid = line.split(":")[0]
                        if sid:
                            services.append(
                                {
                                    "type": "oracle",
                                    "name": f"Oracle DB ({sid})",
                                    "instance_id": sid,
                                    "run_as": "oracle",
                                }
                            )
                            added_ora = True
                    elif "ora_pmon_" in line:
                        sid = line.split("ora_pmon_")[-1].strip()
                        if sid and not any(
                            s["instance_id"] == sid for s in services if s["type"] == "oracle"
                        ):
                            services.append(
                                {
                                    "type": "oracle",
                                    "name": f"Oracle DB ({sid})",
                                    "instance_id": sid,
                                    "run_as": "oracle",
                                }
                            )
                            added_ora = True

                if not added_ora:
                    services.append(
                        {
                            "type": "oracle",
                            "name": "Oracle DB (Default)",
                            "instance_id": "ORCL",
                            "run_as": "oracle",
                        }
                    )

            if "postgres" in req.target_services:
                services.append(
                    {
                        "type": "postgres",
                        "name": "PostgreSQL (Default)",
                        "instance_id": "main",
                        "run_as": "postgres",
                    }
                )

            needs_rts = "rts" in req.target_services
            needs_dg = "dg" in req.target_services
            needs_pjs = "pjs" in req.target_services

            actual_user = "maxgauge"
            status_user = None
            if needs_rts or needs_dg or needs_pjs:
                user_res = await ssh.execute_command(None, MAXGAUGE_USER_COMMAND)
                actual_user = user_res["stdout"].strip().split("\n")[0] or "maxgauge"
                status_user = actual_user if os_profile.name == "SunOS" else None

            if needs_rts or needs_dg:
                rts_instances = {}
                dg_instances = {}

                if needs_rts:
                    if os_profile.name == "SunOS":
                        mxgrc_cmd = _build_targeted_find_command(
                            os_profile.maxgauge_home_command,
                            "f",
                            [".mxgrc"],
                        )
                        res_mxgrc = await ssh.execute_command(None, mxgrc_cmd, timeout=90)
                    else:
                        mxgrc_cmd = build_maxgauge_find_all_command(
                            os_profile,
                            r"/\.mxgrc$",
                            "-type f",
                        )
                        res_mxgrc = await ssh.execute_command(None, mxgrc_cmd)
                    for line in res_mxgrc["stdout"].strip().split("\n"):
                        line = line.strip()
                        if line.endswith(".mxgrc"):
                            path = line.replace("/.mxgrc", "")
                            name = path.split("/")[-1]
                            if name not in ["", ".", ".."]:
                                existing = rts_instances.pop(name, None)
                                if existing:
                                    entry = rts_instances.setdefault(
                                        path,
                                        {
                                            "name": name,
                                        },
                                    )
                                    if existing.get("name"):
                                        entry["name"] = existing["name"]
                                else:
                                    rts_instances[path] = {
                                        "name": name,
                                    }

                if needs_dg:
                    if os_profile.name == "SunOS":
                        dgs_cmd = _build_targeted_find_command(
                            os_profile.maxgauge_home_command,
                            "f",
                            ["dgsctl", "dgsctl.sh", "dgctl", "dgctl.sh"],
                        )
                        res_dgs = await ssh.execute_command(None, dgs_cmd, timeout=90)
                    else:
                        dgs_cmd = build_maxgauge_find_all_command(
                            os_profile,
                            r"/(dgsctl|dgctl)(\.sh)?$",
                        )
                        res_dgs = await ssh.execute_command(None, dgs_cmd)
                    logger.warning(
                        "DG discovery host=%s os=%s cmd=%r stdout=%r stderr=%r",
                        req.host,
                        os_profile.name,
                        dgs_cmd,
                        res_dgs["stdout"],
                        res_dgs["stderr"],
                    )
                    for line in res_dgs["stdout"].strip().split("\n"):
                        line = line.strip()
                        if any(token in line.lower() for token in ["dgsctl", "dgctl"]):
                            bin_p = _strip_known_suffix(
                                line,
                                ["/dgsctl", "/dgsctl.sh", "/dgctl", "/dgctl.sh"],
                            )
                            dg_p = bin_p[:-4] if bin_p.endswith("/bin") else bin_p
                            if dg_p in rts_instances:
                                del rts_instances[dg_p]
                            dg_instances[dg_p] = {
                                "name": dg_p.split("/")[-1],
                            }

                if needs_rts:
                    for p, meta in rts_instances.items():
                        updater_available = False
                        if p.startswith("/"):
                            updater_check = await ssh.execute_command(
                                None,
                                _build_updater_presence_command(p),
                            )
                            updater_available = updater_check["stdout"].strip().lower() == "yes"

                        child_processes = _build_rts_child_processes(
                            set(),
                            updater_available,
                        )
                        services.append(
                            {
                                "type": "rts",
                                "name": f"RTS ({meta['name']})",
                                "instance_id": p,
                                "display_id": meta["name"],
                                "path": p if p.startswith("/") else "",
                                "child_processes": child_processes,
                                "run_as": actual_user,
                            }
                        )
                    debug_info.append(f"RTS discovered: {list(rts_instances.keys())}")
                    if not any(s["type"] == "rts" for s in services):
                        services.append(
                            {
                                "type": "rts",
                                "name": "RTS (Default)",
                                "instance_id": "default",
                                "child_processes": _build_rts_child_processes(set(), False),
                                "run_as": actual_user,
                            }
                        )

                if needs_dg:
                    for p, meta in dg_instances.items():
                        obsd_available = False
                        if p.startswith("/"):
                            obsd_check = await ssh.execute_command(
                                None,
                                _build_obsd_presence_command(p),
                            )
                            obsd_available = obsd_check["stdout"].strip().lower() == "yes"
                        services.append(
                            {
                                "type": "dg",
                                "name": f"DG ({meta['name']})",
                                "instance_id": p,
                                "display_id": meta["name"],
                                "path": p if p.startswith("/") else "",
                                "child_processes": _build_single_child_process(
                                    "obsd",
                                    set(),
                                    obsd_available,
                                ),
                                "run_as": actual_user,
                            }
                        )
                    debug_info.append(f"DG discovered: {list(dg_instances.keys())}")
                    if not any(s["type"] == "dg" for s in services):
                        services.append(
                            {
                                "type": "dg",
                                "name": "DG (Default)",
                                "instance_id": "default",
                                "child_processes": _build_single_child_process("obsd", set(), False),
                                "run_as": actual_user,
                            }
                        )

            if needs_pjs:
                pjs_instances = {}

                if os_profile.name == "SunOS":
                    pjs_cmd = _build_targeted_find_command(
                        os_profile.maxgauge_home_command,
                        "f",
                        ["pjsctl", "pjsctl.sh"],
                    )
                    res_pjs = await ssh.execute_command(None, pjs_cmd, timeout=90)
                else:
                    pjs_cmd = build_maxgauge_find_all_command(
                        os_profile,
                        r"/pjsctl(\.sh)?$",
                    )
                    res_pjs = await ssh.execute_command(None, pjs_cmd)
                logger.warning(
                    "PJS discovery host=%s os=%s cmd=%r stdout=%r stderr=%r",
                    req.host,
                    os_profile.name,
                    pjs_cmd,
                    res_pjs["stdout"],
                    res_pjs["stderr"],
                )
                for line in res_pjs["stdout"].strip().split("\n"):
                    line = line.strip()
                    if any(line.endswith(suffix) for suffix in ["/pjsctl", "/pjsctl.sh"]):
                        p = _strip_known_suffix(line, ["/pjsctl", "/pjsctl.sh"])
                        pjs_instances[p] = {
                            "name": p.split("/")[-1],
                        }

                for p, meta in pjs_instances.items():
                    obsd_available = False
                    if p.startswith("/"):
                        obsd_check = await ssh.execute_command(
                            None,
                            _build_obsd_presence_command(p),
                        )
                        obsd_available = obsd_check["stdout"].strip().lower() == "yes"
                    services.append(
                        {
                            "type": "pjs",
                            "name": f"PJS ({meta['name']})",
                            "instance_id": p,
                            "display_id": meta["name"],
                            "path": p if p.startswith("/") else "",
                            "child_processes": _build_single_child_process(
                                "obsd",
                                set(),
                                obsd_available,
                            ),
                            "run_as": actual_user,
                        }
                    )
                if not any(s["type"] == "pjs" for s in services):
                    services.append(
                        {
                            "type": "pjs",
                            "name": "PJS (Default)",
                            "instance_id": "default",
                            "child_processes": _build_single_child_process("obsd", set(), False),
                            "run_as": actual_user,
                        }
                    )
                debug_info.append(f"PJS discovered: {list(pjs_instances.keys())}")

        return {"success": True, "services": services, "debug": debug_info}
    except Exception as e:
        return {"success": False, "error": str(e), "debug": debug_info}
