from dataclasses import dataclass


@dataclass(frozen=True)
class OSProfile:
    name: str
    ps_command: str
    ps_pid_command: str
    find_maxdepth: str
    maxgauge_home_command: str


LINUX_PROFILE = OSProfile(
    name="Linux",
    ps_command="ps -ef",
    ps_pid_command="ps -fp",
    find_maxdepth="-maxdepth 5",
    maxgauge_home_command="getent passwd maxgauge | cut -d: -f6 2>/dev/null || getent passwd MaxGauge | cut -d: -f6 2>/dev/null",
)

AIX_PROFILE = OSProfile(
    name="AIX",
    ps_command="UNIX95=1 ps -ef",
    ps_pid_command="UNIX95=1 ps -fp",
    find_maxdepth="",
    maxgauge_home_command="grep '^maxgauge:' /etc/passwd | cut -d: -f6 2>/dev/null || grep '^MaxGauge:' /etc/passwd | cut -d: -f6 2>/dev/null",
)

HPUX_PROFILE = OSProfile(
    name="HP-UX",
    ps_command="UNIX95=1 ps -ef",
    ps_pid_command="UNIX95=1 ps -fp",
    find_maxdepth="",
    maxgauge_home_command="grep '^maxgauge:' /etc/passwd | cut -d: -f6 2>/dev/null || grep '^MaxGauge:' /etc/passwd | cut -d: -f6 2>/dev/null",
)

SUNOS_PROFILE = OSProfile(
    name="SunOS",
    ps_command="UNIX95=1 ps -ef",
    ps_pid_command="UNIX95=1 ps -fp",
    find_maxdepth="",
    maxgauge_home_command="grep '^maxgauge:' /etc/passwd | cut -d: -f6 2>/dev/null || grep '^MaxGauge:' /etc/passwd | cut -d: -f6 2>/dev/null",
)

DEFAULT_PROFILE = LINUX_PROFILE


def get_os_profile(os_name: str) -> OSProfile:
    normalized = (os_name or "").strip()
    if normalized == "AIX":
        return AIX_PROFILE
    if normalized == "HP-UX":
        return HPUX_PROFILE
    if normalized == "SunOS":
        return SUNOS_PROFILE
    if normalized == "Linux":
        return LINUX_PROFILE
    return DEFAULT_PROFILE
