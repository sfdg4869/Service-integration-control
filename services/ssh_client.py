import paramiko

class SSHClientWrapper:
    def __init__(self, host, port, username, password):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def __enter__(self):
        self.client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=10
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.client.close()

    def execute_command(self, process_user=None, command=None):
        """
        주어진 명령어를 특정 시스템 계정(process_user)으로 실행합니다.
        로그인한 계정과 대상 계정이 같다면 권한 전환을 생략합니다.
        """
        if process_user and process_user != self.username:
            # 타겟 계정이 다르면 sudo/su 를 시도 (비밀번호 입력창에 막히는 것을 방지하기 위해 간단한 timeout 처리 혹은 echo 필요)
            full_command = f'sudo su - {process_user} -c "{command}"'
        else:
            # 로그인한 계정과 같거나, 대상 계정이 없으면 그대로 실행
            if process_user == self.username:
                # 환경변수 로딩을 위해 bash 연동
                full_command = f'bash -lc "{command}"'
            else:
                full_command = command

        stdin, stdout, stderr = self.client.exec_command(full_command, get_pty=True)
        
        # sudo 암호가 필요한 환경을 대비해 자동 입력 (설정에 따라 주석처리/변경 가능)
        # stdin.write(self.password + '\n')
        # stdin.flush()
        
        out = stdout.read().decode('utf-8').strip()
        err = stderr.read().decode('utf-8').strip()
        status = stdout.channel.recv_exit_status()
        
        return {
            "exit_status": status,
            "stdout": out,
            "stderr": err,
            "command_executed": full_command
        }
