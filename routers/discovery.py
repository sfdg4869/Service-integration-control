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


def _is_same_or_child_path(base_path: str, candidate_path: str) -> bool:
    normalized_base = base_path.rstrip("/")
    normalized_candidate = candidate_path.rstrip("/")
    return (
        normalized_candidate == normalized_base
        or normalized_candidate.startswith(f"{normalized_base}/")
    )


def _paths_overlap(first_path: str, second_path: str) -> bool:
    return _is_same_or_child_path(first_path, second_path) or _is_same_or_child_path(second_path, first_path)


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


def _sanitize_version_output(service_type: str, output: str) -> str:
    lines = [(raw_line or "").strip() for raw_line in (output or "").splitlines()]

    if service_type == "rts":
        for line in lines:
            lower_line = line.lower()
            if not line:
                continue
            if "rts version:" in lower_line and "not found" not in lower_line:
                return line

    for line in lines:
        if not line:
            continue
        lower_line = line.lower()
        if lower_line.startswith(("run_user=", "rts_dir=", "dgs_dir=", "pjs_dir=", "mxg_rc=", "dgs_shell=", "pjs_shell=", "rts_shell=")):
            continue
        if lower_line.startswith(("picked up _java_options", "openjdk", "java version", "warning:")):
            continue
        if (
            lower_line.startswith(("command not found", "no such file", "not found", "permission denied"))
            or " not found" in lower_line
            or ": not found" in lower_line
            or "no such file" in lower_line
            or "permission denied" in lower_line
        ):
            continue
        return line
    return ""


def _normalize_full_version_output(service_type: str, output: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in (output or "").splitlines():
        line = (raw_line or "").strip()
        if not line:
            continue
        lower_line = line.lower()
        if (
            lower_line.startswith(("run_user=", "rts_dir=", "dgs_dir=", "pjs_dir=", "mxg_rc=", "dgs_shell=", "pjs_shell=", "rts_shell="))
            or lower_line.startswith(("picked up _java_options", "openjdk", "java version", "warning:"))
            or "need to apply .mxgrc" in lower_line
            or " not found" in lower_line
            or ": not found" in lower_line
            or "command not found" in lower_line
            or "no such file" in lower_line
            or "permission denied" in lower_line
        ):
            continue

        if service_type == "rts":
            if not any(token in lower_line for token in ("version", "build date")):
                continue

        cleaned_lines.append(line.rstrip())
    return "\n".join(cleaned_lines).strip()


def _build_version_command(service_type: str, instance_path: str) -> str:
    if service_type == "rts":
        attempts = [
            'if command -v ksh >/dev/null 2>&1; then RTS_SHELL=ksh; else RTS_SHELL=sh; fi; [ -f "./.mxgrc" ] && RTS_SHELL="$RTS_SHELL" "$RTS_SHELL" -c \'. ./.mxgrc && mxg_rts -v\'',
            'if command -v ksh >/dev/null 2>&1; then RTS_SHELL=ksh; else RTS_SHELL=sh; fi; [ -f "./.mxgrc" ] && RTS_SHELL="$RTS_SHELL" "$RTS_SHELL" -c \'. ./.mxgrc && mxg_rts --version\'',
            '[ -x "./bin/mxg_rts" ] && ./bin/mxg_rts --version',
            '[ -x "./bin/mxg_rts" ] && ./bin/mxg_rts -v',
        ]
    elif service_type == "dg":
        attempts = [
            '[ -f "./bin/DGServer.jar" ] && cd "./bin" && java -jar DGServer.jar -v',
            '[ -f "./DGServer.jar" ] && java -jar DGServer.jar -v',
        ]
    else:
        attempts = [
            '[ -f "./svc/www/WEB-INF/lib/exem_platformjs.jar" ] && cd "./svc/www/WEB-INF/lib" && java -jar exem_platformjs.jar -v',
            '[ -f "./WEB-INF/lib/exem_platformjs.jar" ] && cd "./WEB-INF/lib" && java -jar exem_platformjs.jar -v',
        ]

    joined_attempts = " || ".join(f"({attempt})" for attempt in attempts)
    return f'''
if [ -n "{instance_path}" ] && cd "{instance_path}" 2>/dev/null; then
  ({joined_attempts}) 2>&1 || true
else
  echo ""
fi
'''


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
                status_user = actual_user if os_profile.name in {"SunOS", "HP-UX"} else None

            if needs_rts or needs_dg:
                rts_instances = {}
                dg_instances = {}

                if needs_rts:
                    rts_cmd = build_maxgauge_find_all_command(
                        os_profile,
                        r"/bin/mxg_rts$",
                        "-type f",
                    )
                    res_rts = await ssh.execute_command(None, rts_cmd, timeout=90 if os_profile.name in {"SunOS", "HP-UX"} else 30)
                    for line in res_rts["stdout"].strip().split("\n"):
                        line = line.strip()
                        if line.endswith("/bin/mxg_rts"):
                            path = line[: -len("/bin/mxg_rts")]
                            name = path.split("/")[-1]
                            if name not in ["", ".", ".."]:
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

                    if needs_rts and dg_instances:
                        rts_instances = {
                            path: meta
                            for path, meta in rts_instances.items()
                            if not any(
                                path.startswith("/") and _is_same_or_child_path(dg_path, path)
                                for dg_path in dg_instances.keys()
                            )
                        }

                if needs_rts:
                    for p, meta in rts_instances.items():
                        updater_available = False
                        version = "Unknown"
                        version_full = "Unknown"
                        if p.startswith("/"):
                            updater_check = await ssh.execute_command(
                                None,
                                _build_updater_presence_command(p),
                            )
                            updater_available = updater_check["stdout"].strip().lower() == "yes"
                            version_res = await ssh.execute_command(
                                None,
                                _build_version_command("rts", p),
                            )
                            version_full = _normalize_full_version_output("rts", version_res["stdout"]) or "Unknown"
                            version = _sanitize_version_output("rts", version_res["stdout"]) or "Unknown"

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
                                "version": version,
                                "version_full": version_full,
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
                                "version": "Unknown",
                                "version_full": "Unknown",
                                "child_processes": _build_rts_child_processes(set(), False),
                                "run_as": actual_user,
                            }
                        )

                if needs_dg:
                    for p, meta in dg_instances.items():
                        obsd_available = False
                        version = "Unknown"
                        version_full = "Unknown"
                        if p.startswith("/"):
                            obsd_check = await ssh.execute_command(
                                None,
                                _build_obsd_presence_command(p),
                            )
                            obsd_available = obsd_check["stdout"].strip().lower() == "yes"
                            version_res = await ssh.execute_command(
                                None,
                                _build_version_command("dg", p),
                            )
                            version_full = _normalize_full_version_output("dg", version_res["stdout"]) or "Unknown"
                            version = _sanitize_version_output("dg", version_res["stdout"]) or "Unknown"
                        services.append(
                            {
                                "type": "dg",
                                "name": f"DG ({meta['name']})",
                                "instance_id": p,
                                "display_id": meta["name"],
                                "path": p if p.startswith("/") else "",
                                "version": version,
                                "version_full": version_full,
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
                                "version": "Unknown",
                                "version_full": "Unknown",
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
                    version = "Unknown"
                    version_full = "Unknown"
                    if p.startswith("/"):
                        obsd_check = await ssh.execute_command(
                            None,
                            _build_obsd_presence_command(p),
                        )
                        obsd_available = obsd_check["stdout"].strip().lower() == "yes"
                        version_res = await ssh.execute_command(
                            None,
                            _build_version_command("pjs", p),
                        )
                        version_full = _normalize_full_version_output("pjs", version_res["stdout"]) or "Unknown"
                        version = _sanitize_version_output("pjs", version_res["stdout"]) or "Unknown"
                    services.append(
                        {
                            "type": "pjs",
                            "name": f"PJS ({meta['name']})",
                            "instance_id": p,
                            "display_id": meta["name"],
                            "path": p if p.startswith("/") else "",
                            "version": version,
                            "version_full": version_full,
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
                            "version": "Unknown",
                            "version_full": "Unknown",
                            "child_processes": _build_single_child_process("obsd", set(), False),
                            "run_as": actual_user,
                        }
                    )
                debug_info.append(f"PJS discovered: {list(pjs_instances.keys())}")

            if needs_rts:
                blocked_paths = {
                    service["path"]
                    for service in services
                    if service["type"] in {"dg", "pjs"} and service.get("path")
                }
                if blocked_paths:
                    services = [
                        service
                        for service in services
                        if service["type"] != "rts"
                        or not service.get("path")
                        or not any(_paths_overlap(blocked_path, service["path"]) for blocked_path in blocked_paths)
                    ]

        return {"success": True, "services": services, "debug": debug_info}
    except Exception as e:
        return {"success": False, "error": str(e), "debug": debug_info}
