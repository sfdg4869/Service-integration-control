from services.os_profiles import OSProfile

MAXGAUGE_USER_COMMAND = "id -un maxgauge 2>/dev/null || id -un MaxGauge 2>/dev/null || true"


def build_rts_status_command(profile: OSProfile) -> str:
    if profile.name == "SunOS":
        proc_filter = "egrep -i 'mxg.*(rts|obsd|updater|sndf)|mxg_(rts|obsd|updater|sndf)'"
    else:
        proc_filter = "grep -E 'mxg_(rts|obsd|updater|sndf)'"
    return (
        f"{profile.ps_command} | {proc_filter} | grep -v grep; "
        f"echo '---PWDX_INFO---'; "
        f"for pid in $({profile.ps_command} | {proc_filter} | grep -v grep | awk '{{print $2}}'); do "
        f"pwdx $pid 2>/dev/null || procwdx $pid 2>/dev/null || true; "
        f"done"
    )


def build_dg_status_command(profile: OSProfile) -> str:
    if profile.name == "SunOS":
        proc_filter = "egrep -i 'mxg.*(dgs|dg)|mxg_(dgs|dg)|DGServer|DataGather|dgsctl'"
    else:
        proc_filter = "egrep 'mxg_(dgs|dg)|DGServer|DataGather|dgsctl'"
    return (
        f"{profile.ps_command} | {proc_filter} | grep -v grep; "
        f"echo '---PWDX_INFO---'; "
        f"for pid in $({profile.ps_command} | {proc_filter} | grep -v grep | awk '{{print $2}}'); do "
        f"pwdx $pid 2>/dev/null || procwdx $pid 2>/dev/null || true; "
        f"done"
    )


def build_pjs_status_command(profile: OSProfile, grep_target: str) -> str:
    if grep_target == "pjs":
        if profile.name == "SunOS":
            grep_cmd_part = "egrep -i 'pjs|platformjs|node|npm|mxg.*pjs'"
        else:
            grep_cmd_part = "egrep -i 'pjs|platformjs|node|npm'"
    else:
        grep_cmd_part = f"grep -i '{grep_target}'"
    cmd_base = f"{grep_cmd_part} | grep -v grep | grep -v bash | grep -v ssh | grep -v pjsctl"
    return (
        f"{profile.ps_command} | {cmd_base}; "
        f"echo '---PWDX_INFO---'; "
        f"for pid in $({profile.ps_command} | {cmd_base} | awk '{{print $2}}'); do "
        f"pwdx $pid 2>/dev/null || procwdx $pid 2>/dev/null || true; "
        f"done"
    )


def build_maxgauge_find_command(profile: OSProfile, name: str, target_type: str = "d") -> str:
    maxdepth_part = f" {profile.find_maxdepth}" if profile.find_maxdepth else ""
    return (
        f'D=$({profile.maxgauge_home_command}); '
        f'[ -z "$D" ] && D=~; '
        f'find "$D"{maxdepth_part} -type {target_type} -name "{name}" 2>/dev/null | head -n 1'
    )


def build_maxgauge_find_all_command(profile: OSProfile, pattern: str, type_expr: str = "") -> str:
    maxdepth_part = f" {profile.find_maxdepth}" if profile.find_maxdepth else ""
    type_part = f" {type_expr}" if type_expr else ""
    return (
        f'D=$({profile.maxgauge_home_command}); '
        f'[ -z "$D" ] && D=~; '
        f'find "$D"{maxdepth_part}{type_part} 2>/dev/null | egrep -i "{pattern}"'
    )
