from dataclasses import dataclass
from typing import Iterable

from services.os_profiles import OSProfile


@dataclass(frozen=True)
class RuntimeProcessPortInfo:
    service_type: str
    key: str
    pid: str
    ports: tuple[str, ...]
    base_path: str = ""
    version: str = ""


def _normalize_ports(raw_ports: str) -> tuple[str, ...]:
    ports = []
    for part in (raw_ports or "").split(","):
        value = part.strip()
        if value and value not in ports:
            ports.append(value)
    return tuple(ports)


def _parse_key_value_line(line: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in (line or "").strip().split("\t"):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def parse_runtime_process_port_output(
    service_type: str,
    output: str,
) -> list[RuntimeProcessPortInfo]:
    rows: list[RuntimeProcessPortInfo] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line or "PID=" not in line:
            continue
        fields = _parse_key_value_line(line)
        pid = fields.get("PID", "")
        if not pid:
            continue
        rows.append(
            RuntimeProcessPortInfo(
                service_type=service_type,
                key=fields.get("KEY", "") or fields.get("DATA", "") or fields.get("SID", ""),
                pid=pid,
                ports=_normalize_ports(fields.get("PORT", "")),
                base_path=fields.get("BASE", ""),
                version=fields.get("VER", ""),
            )
        )
    return rows


def build_runtime_process_port_command(service_type: str, profile: OSProfile) -> str:
    normalized = (service_type or "").strip().lower()
    if profile.name == "SunOS" and normalized == "rts":
        return _build_rts_runtime_port_command_sunos(profile)
    if profile.name == "AIX" and normalized == "rts":
        return _build_rts_runtime_port_command_aix(profile)
    if profile.name == "HP-UX" and normalized == "rts":
        return _build_rts_runtime_port_command_hpux(profile)
    if profile.name != "Linux":
        return 'echo "UNSUPPORTED_OS"'

    if normalized == "rts":
        return _build_rts_runtime_port_command(profile)
    if normalized == "dg":
        return _build_dg_runtime_port_command(profile)
    if normalized == "pjs":
        return _build_pjs_runtime_port_command(profile)
    if normalized == "oracle":
        return _build_oracle_runtime_info_command(profile)
    if normalized == "postgres":
        return _build_postgres_runtime_info_command(profile)
    raise ValueError(f"Unsupported service type: {service_type}")


def _build_port_lookup_shell() -> str:
    return (
        'ports=$(ss -ltnp 2>/dev/null | grep "pid=$pid," | '
        "sed -n 's/.*:\\([0-9][0-9]*\\)[[:space:]].*/\\1/p' | "
        "sort -u | paste -sd, -); "
        'if [ -z "$ports" ] && command -v lsof >/dev/null 2>&1; then '
        '  ports=$(lsof -Pan -p "$pid" -iTCP -sTCP:LISTEN 2>/dev/null | '
        "awk 'NR > 1 {n=split($9, a, \":\"); if (a[n] != \"*\") seen[a[n]]=1} "
        "END {first=1; for (p in seen) {printf \"%s%s\", (first ? \"\" : \",\"), p; first=0}}'); "
        "fi; "
        'if [ -z "$ports" ] && command -v netstat >/dev/null 2>&1; then '
        '  ports=$(netstat -lntp 2>/dev/null | awk -v pid="$pid" '
        "'$0 ~ (pid \"/\") {n=split($4, a, \":\"); if (a[n] != \"*\") seen[a[n]]=1} "
        "END {first=1; for (p in seen) {printf \"%s%s\", (first ? \"\" : \",\"), p; first=0}}'); "
        "fi; "
    )


def _build_rts_runtime_port_command(profile: OSProfile) -> str:
    port_lookup = _build_port_lookup_shell()
    return f"""
{profile.ps_command} | grep '[m]xg_rts' | while read -r l; do
  pid=$(printf "%s\\n" "$l" | awk '{{print $2}}')
  key=$(printf "%s\\n" "$l" | sed -n 's/.*-c[[:space:]]\\([^[:space:]]*\\).*/\\1/p')
  base=$(pwdx "$pid" 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//' | sed 's:/*$::')
  if [ -z "$base" ]; then
    base=$(procwdx "$pid" 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//' | sed 's:/*$::')
  fi
  {port_lookup}
  printf "KEY=%s\\tPID=%s\\tPORT=%s\\tBASE=%s\\n" "$key" "$pid" "${{ports:-}}" "$base"
done | sort
""".strip()


def _build_rts_runtime_port_command_sunos(profile: OSProfile) -> str:
    return f"""
for pid in `{profile.ps_command} | grep '[m]xg_rts' | awk '{{print $2}}'`; do
  key=`{profile.ps_command} | grep "[m]xg_rts" | grep " $pid " | sed -n 's/.*-c \\([^ ]*\\).*/\\1/p'`
  port=`pfiles $pid 2>/dev/null | awk '
    /sockname: AF_INET/ {{ p=$NF }}
    /SO_ACCEPTCONN/      {{ print p }}' | sort -u | paste -sd, -`
  printf "KEY=%s\\tPID=%s\\tPORT=%s\\n" "$key" "$pid" "${{port:-none}}"
done | sort
""".strip()


def _build_rts_runtime_port_command_aix(profile: OSProfile) -> str:
    return """
ps -ef | grep mxg_rts | grep -v grep | while read -r line; do
  pid=`echo "$line" | awk '{print $2}'`
  key=`echo "$line" | awk '{for(i=1;i<=NF;i++) if($i=="c"){print $(i+1);break}}'`
  wd=`procwdx $pid 2>/dev/null | awk '{print $2}'`
  port=`grep -i 'daemon_port' ${wd}conf/${key}/rts.conf 2>/dev/null | head -1 | awk -F'=' '{gsub(/[^0-9]/,"",$2); print $2}'`
  echo "KEY=$key\tPID=$pid\tPORT=${port:-?}"
done
""".strip()


def _build_rts_runtime_port_command_hpux(profile: OSProfile) -> str:
    return f"""
LSOF_BIN=`command -v lsof 2>/dev/null || true`
if [ -z "$LSOF_BIN" ]; then
  for p in /usr/sbin/lsof /usr/bin/lsof /usr/local/bin/lsof /opt/lsof/bin/lsof; do
    if [ -x "$p" ]; then
      LSOF_BIN="$p"
      break
    fi
  done
fi
{profile.ps_command} | grep '[m]xg_rts' | grep -v grep | while read -r line; do
  pid=`echo "$line" | awk '{{print $2}}'`
  key=`echo "$line" | sed -n 's/.*-c \\([^ ]*\\).*/\\1/p'`
  port=`"$LSOF_BIN" -p $pid 2>/dev/null | grep LISTEN | awk '{{print $9}}' | sed 's/.*://' | sort -u | paste -sd, -`
  printf "KEY=%s\\tPID=%s\\tPORT=%s\\n" "$key" "$pid" "${{port:-none}}"
done | sort
""".strip()


def _build_dg_runtime_port_command(profile: OSProfile) -> str:
    port_lookup = _build_port_lookup_shell()
    return f"""
{profile.ps_command} | grep '[D]GServer.jar' | while read -r l; do
  pid=$(printf "%s\\n" "$l" | awk '{{print $2}}')
  key=$(printf "%s\\n" "$l" | sed -n 's/.*-DG_\\([^[:space:]]*\\).*/\\1/p')
  base=$(printf "%s\\n" "$l" | sed -n 's/.*-f[[:space:]]\\([^[:space:]]*\\/DGServer_[^[:space:]]*\\)\\/conf\\/DG\\/common_linux\\.conf.*/\\1/p' | sed 's:/*$::')
  if [ -z "$base" ]; then
    base=$(pwdx "$pid" 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//' | sed 's:/*$::')
  fi
  if [ -z "$base" ]; then
    base=$(procwdx "$pid" 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//' | sed 's:/*$::')
  fi
  {port_lookup}
  printf "KEY=%s\\tPID=%s\\tPORT=%s\\tBASE=%s\\n" "$key" "$pid" "${{ports:-}}" "$base"
done | sort
""".strip()


def _build_pjs_runtime_port_command(profile: OSProfile) -> str:
    return f"""
{profile.ps_command} | grep '[j]etty/start.jar' | while read -r l; do
  pid=$(echo "$l" | awk '{{print $2}}')
  base=$(echo "$l" | grep -oP 'jetty\\.base=\\K\\S+')
  key=$(echo "$l" | grep -oP 'jetty\\.base=\\K\\S+' | sed -E 's#.*/QS1/##; s#/svc$##')
  port=$(echo "$l" | grep -oP 'jetty\\.(http\\.)?port=\\K[0-9]+')
  printf "KEY=%s\\tPID=%s\\tPORT=%s\\tBASE=%s\\n" "$key" "$pid" "${{port:-}}" "$base"
done | sort
""".strip()


def _build_oracle_runtime_info_command(profile: OSProfile) -> str:
    return f"""
if [ -f ~/.profile ]; then . ~/.profile 2>/dev/null || true; fi
if [ -f ~/.bash_profile ]; then . ~/.bash_profile 2>/dev/null || true; fi
if [ -f ~/.bashrc ]; then . ~/.bashrc 2>/dev/null || true; fi
if [ -f ~/.kshrc ]; then . ~/.kshrc 2>/dev/null || true; fi
lport=$(lsnrctl status 2>/dev/null | grep -oP 'PORT=\\K[0-9]+' | sort -un | paste -sd, -)
{profile.ps_command} | grep '[o]ra_pmon' | while read -r l; do
  pid=$(echo "$l" | awk '{{print $2}}')
  sid=$(echo "$l" | grep -oP 'ora_pmon_\\K\\S+')
  ver=$(ORACLE_SID=$sid sqlplus -s -L / as sysdba 2>/dev/null <<'EOF'
set heading off feedback off pagesize 0
select version from v$instance;
exit
EOF
)
  ver=$(printf "%s\\n" "$ver" | grep -Eo '[0-9]+(\\.[0-9]+)+' | head -n 1)
  printf "KEY=%s\\tPID=%s\\tPORT=%s\\tVER=%s\\n" "$sid" "$pid" "${{lport:-}}" "${{ver:-}}"
done | sort
""".strip()


def _build_postgres_runtime_info_command(profile: OSProfile) -> str:
    return f"""
{profile.ps_command} | grep '[p]ostgres' | grep -- '-D' | while read -r l; do
  pid=$(echo "$l" | awk '{{print $2}}')
  dgdir=$(echo "$l" | grep -oP '\\-D \\K\\S+')
  port=$(sed -n '4p' "$dgdir/postmaster.pid" 2>/dev/null)
  [ -z "$port" ] && port=$(grep -E '^\\s*port\\s*=' "$dgdir/postgresql.conf" 2>/dev/null | grep -oP '[0-9]+' | head -1)
  ver=$(cat "$dgdir/PG_VERSION" 2>/dev/null)
  [ -z "$ver" ] && ver=$(echo "$dgdir" | grep -oP 'PG_\\K[0-9]+' | sed -E 's/^([0-9])([0-9])$/\\1.\\2/')
  printf "DATA=%s\\tPID=%s\\tPORT=%s\\tVER=%s\\tBASE=%s\\n" "$dgdir" "$pid" "${{port:-5432}}" "${{ver:-?}}" "$dgdir"
done | sort
""".strip()


def group_runtime_process_ports(
    items: Iterable[RuntimeProcessPortInfo],
) -> dict[str, list[RuntimeProcessPortInfo]]:
    grouped: dict[str, list[RuntimeProcessPortInfo]] = {}
    for item in items:
        group_key = item.key or item.base_path or item.pid
        grouped.setdefault(group_key, []).append(item)
    return grouped
