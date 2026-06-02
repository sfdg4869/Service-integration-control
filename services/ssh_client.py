import asyncssh
import asyncio
import time
import logging

logger = logging.getLogger(__name__)

# ============================================================
# SSH 커넥션 풀 (Connection Pool)
# 한 번 맺은 SSH 세션을 메모리에 캐싱하여 재사용합니다.
# 매 API 호출마다 새로 로그인하지 않으므로 1~3초 → 수십 ms 로 단축됩니다.
# ============================================================
_pool: dict = {}  # {(host, port, username): (conn, last_used_timestamp)}
_pool_lock = asyncio.Lock()
POOL_TTL = 60  # 60초 동안 미사용 시 세션 만료


async def _get_connection(host, port, username, password):
    """커넥션 풀에서 기존 세션을 재사용하거나, 없으면 새로 생성합니다."""
    key = (host, port, username)

    async with _pool_lock:
        if key in _pool:
            conn, last_used = _pool[key]
            # 세션이 아직 살아있는지 확인
            try:
                result = await asyncio.wait_for(conn.run("echo ok", check=True), timeout=5)
                if result.exit_status == 0:
                    _pool[key] = (conn, time.time())
                    return conn
            except Exception:
                # 세션이 끊어졌으면 풀에서 제거하고 새로 생성
                try:
                    conn.close()
                except Exception:
                    pass
                del _pool[key]

        # 새 연결 생성
        conn = await asyncssh.connect(
            host, port,
            username=username,
            password=password,
            known_hosts=None,           # 호스트 키 검증 생략 (내부망 전용)
            connect_timeout=10,
            # PTY 할당 없음 (get_pty=False 효과) → 속도 향상
            # look_for_keys=False, allow_agent=False 효과 내장
        )
        _pool[key] = (conn, time.time())
        return conn


async def cleanup_stale_connections():
    """오래된 세션을 주기적으로 정리합니다."""
    async with _pool_lock:
        now = time.time()
        stale_keys = [k for k, (_, t) in _pool.items() if now - t > POOL_TTL]
        for k in stale_keys:
            try:
                _pool[k][0].close()
            except Exception:
                pass
            del _pool[k]


class SSHClientWrapper:
    """
    AsyncSSH 기반 SSH 클라이언트 래퍼.
    - 비동기(async/await) 지원
    - 커넥션 풀링으로 재접속 오버헤드 제거
    - PTY 미할당으로 응답 속도 향상
    """
    def __init__(self, host, port, username, password):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.conn = None

    async def __aenter__(self):
        self.conn = await _get_connection(self.host, self.port, self.username, self.password)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 풀링 방식이므로 연결을 닫지 않음 (재사용)
        pass

    async def execute_command(self, process_user=None, command=None, timeout=30):
        """
        주어진 명령어를 특정 시스템 계정(process_user)으로 실행합니다.
        로그인한 계정과 대상 계정이 같다면 권한 전환을 생략합니다.
        """
        if process_user and process_user != self.username:
            escaped_command = command.replace("'", "'\\''")
            # Prefer sudo when present, but fall back to su for older Unix hosts
            # like HP-UX where sudo is often absent.
            full_command = (
                f"if command -v sudo >/dev/null 2>&1; then "
                f"sudo su - {process_user} -c '{escaped_command}'; "
                f"else "
                f"su - {process_user} -c '{escaped_command}'; "
                f"fi"
            )
        else:
            if process_user == self.username:
                # When we're already the target user, run the command directly.
                # Extra shell wrapping causes quoting/login-shell issues on HP-UX/AIX.
                full_command = command
            else:
                full_command = command

        try:
            result = await asyncio.wait_for(
                self.conn.run(full_command, check=False),
                timeout=timeout
            )
            return {
                "exit_status": result.exit_status if result.exit_status is not None else -1,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "command_executed": full_command
            }
        except asyncio.TimeoutError:
            return {
                "exit_status": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "command_executed": full_command
            }
        except Exception as e:
            # 연결이 끊겼을 수 있으므로 풀에서 제거 후 재시도
            key = (self.host, self.port, self.username)
            async with _pool_lock:
                if key in _pool:
                    del _pool[key]
            
            # 1회 재연결 시도
            try:
                self.conn = await _get_connection(self.host, self.port, self.username, self.password)
                result = await asyncio.wait_for(
                    self.conn.run(full_command, check=False),
                    timeout=timeout
                )
                return {
                    "exit_status": result.exit_status if result.exit_status is not None else -1,
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                    "command_executed": full_command
                }
            except Exception as retry_err:
                return {
                    "exit_status": -1,
                    "stdout": "",
                    "stderr": str(retry_err),
                    "command_executed": full_command
                }
