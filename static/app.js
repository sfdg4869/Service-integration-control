function getCredentials() {
    const host = document.getElementById('host').value;
    const port = parseInt(document.getElementById('port').value || 10022);
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;

    if (!host || !username || !password) {
        alert("Please enter Host IP, Username, and Password.");
        return null;
    }

    // 수정: 선택된 타겟 서비스 배열 구성
    const checkboxes = document.querySelectorAll('.chk-target:checked');
    const target_services = Array.from(checkboxes).map(chk => chk.value);

    if (target_services.length === 0) {
        alert("Please select at least one service to scan.");
        return null;
    }

    return { host, port, username, password, target_services };
}

async function discoverServices() {
    const creds = getCredentials();
    if (!creds) return;

    const btn = document.getElementById('discover-btn');
    const dashboard = document.getElementById('dynamic-dashboard');

    btn.disabled = true;
    btn.innerText = "서버 탐색 중... (Scanning)";
    dashboard.innerHTML = "<p style='text-align:center; width:100%; color:#94a3b8;'>서버의 프로세스를 찾고 있습니다. 잠시만 기다려주세요...</p>";

    try {
        const response = await fetch('/api/discovery/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(creds)
        });
        const data = await response.json();

        if (data.success && data.services) {
            renderDashboard(data.services);
        } else {
            dashboard.innerHTML = `<p style="color:#ef4444; width:100%; text-align:center;">탐색 실패: ${data.error || '알 수 없는 오류'}</p>`;
        }
    } catch (err) {
        dashboard.innerHTML = `<p style="color:#ef4444; width:100%; text-align:center;">네트워크 에러: ${err.message}</p>`;
    } finally {
        btn.disabled = false;
        btn.innerText = "서버 스캔 다시 시작 (Discover)";
    }
}

function renderDashboard(services) {
    const dashboard = document.getElementById('dynamic-dashboard');
    dashboard.innerHTML = '';

    if (services.length === 0) {
        dashboard.innerHTML = "<p style='color:#94a3b8; width:100%; text-align:center;'>발견된 데이터베이스나 RTS 프로세스가 없습니다.</p>";
        return;
    }

    const types = ['oracle', 'postgres', 'rts'];
    const typeNames = {
        'oracle': 'Oracle DB',
        'postgres': 'PostgreSQL',
        'rts': 'RTS Process'
    };

    const typeIcons = {
        'oracle': `<div style="width: 1.6rem; height: 1.6rem; background-color: #F80000; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-right: 0.5rem;"><svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 65%; height: 65%;"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg></div>`,
        'postgres': `<img src="https://upload.wikimedia.org/wikipedia/commons/2/29/Postgresql_elephant.svg" alt="PostgreSQL" style="height: 1.6rem; margin-right: 0.5rem; filter: drop-shadow(0 0 2px rgba(255,255,255,0.2));">`,
        'rts': `<img src="/static/maxgauge.png" alt="MaxGauge" style="height: 1.6rem; object-fit: contain; margin-right: 0.5rem;" onerror="this.style.display='none'">`
    };

    types.forEach(type => {
        const typeServices = services.filter(s => s.type === type);
        if (typeServices.length === 0) return;

        // 각 타입별 첫 번째 서비스의 run_as를 기본 User로 사용
        const runAs = typeServices[0].run_as;
        const cardId = `card-${type}`;

        let instancesHtml = '';
        typeServices.forEach(srv => {
            instancesHtml += `
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.6rem 0; border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <div style="font-weight: 500; color: #38bdf8; font-size: 0.95rem; display: flex; align-items: center; gap: 0.5rem;" class="instance-item" data-id="${srv.instance_id}">
                        <span id="dot-${cardId}-${srv.instance_id}" class="dot unknown" style="width: 10px; height: 10px; display: inline-block;"></span>
                        ${srv.instance_id}
                    </div>
                    <div style="display: flex; flex-direction: row; gap: 0.5rem; flex-shrink: 0;">
                        <button class="btn start-btn" style="padding: 0.3rem 0.6rem; font-size: 0.8rem; white-space: nowrap; width: 60px;" onclick="controlService('${type}', 'start', '${srv.instance_id}', '${cardId}')">시작</button>
                        <button class="btn stop-btn" style="padding: 0.3rem 0.6rem; font-size: 0.8rem; white-space: nowrap; width: 60px;" onclick="controlService('${type}', 'stop', '${srv.instance_id}', '${cardId}')">정지</button>
                    </div>
                </div>
            `;
        });

        const cardHTML = `
            <div class="service-card glass-panel" id="${cardId}">
                <div class="service-header">
                    <h3 style="display: flex; align-items: center;">${typeIcons[type] || ''}${typeNames[type]}</h3>
                    <div class="status-indicator">
                        <span class="dot unknown"></span>
                        <span class="status-text target-status">Unknown</span>
                    </div>
                </div>
                
                <p class="service-user" style="margin-bottom: 0.2rem;">Target User: <strong>${runAs}</strong></p>
                <!-- 카드 자체 인증 폼 -->
                <div style="display:flex; gap: 0.5rem; margin-bottom: 1rem;">
                    <input type="text" id="${cardId}-user" value="${runAs}" class="micro-input" placeholder="User">
                    <input type="password" id="${cardId}-pass" class="micro-input" placeholder="해당 User의 암호">
                </div>

                <div class="actions" style="margin-bottom: 1rem;">
                    <button class="btn check-btn" onclick="checkStatus('${type}', '', '${cardId}')">전체 상태 조회 (Check All)</button>
                </div>

                <div class="instances-list" style="margin-bottom: 1rem; background: rgba(15,23,42,0.6); padding: 0 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05);">
                    ${instancesHtml}
                </div>

                <div class="terminal-log">
                    <pre class="log-output">Ready...</pre>
                </div>
            </div>
        `;

        dashboard.insertAdjacentHTML('beforeend', cardHTML);

        // 카드 렌더링 후 초기 상태 조회
        checkStatus(type, '', cardId);
    });
}

function updateStatusUI(cardId, statusResult, type) {
    const card = document.getElementById(cardId);
    if (!card) return;
    const dot = card.querySelector('.dot');
    const text = card.querySelector('.status-text');
    const logs = card.querySelector('.log-output');

    dot.className = 'dot';
    text.className = 'status-text target-status';

    if (statusResult.status === 'running') {
        dot.classList.add('running');
        text.classList.add('running');
        text.innerText = 'RUNNING';
    } else if (statusResult.status === 'stopped') {
        dot.classList.add('stopped');
        text.classList.add('stopped');
        text.innerText = 'STOPPED';
    } else {
        dot.classList.add('unknown');
        text.classList.add('unknown');
        text.innerText = 'ERROR';
    }

    logs.innerText = (statusResult.details || statusResult.message || "").trim() || "No output.";

    // 개별 인스턴스 토글 업데이트
    const listItems = document.querySelectorAll(`#${cardId} .instance-item`);
    listItems.forEach(item => {
        const instId = item.getAttribute('data-id');
        const dotEl = document.getElementById(`dot-${cardId}-${instId}`);
        let isInstRunning = false;

        if (statusResult.details) {
            if (type === 'oracle') {
                isInstRunning = statusResult.details.includes(`ora_pmon_${instId}`);
            } else if (type === 'rts') {
                isInstRunning = statusResult.details.includes(`-c ${instId}`);
            } else {
                isInstRunning = statusResult.details.includes(instId) || statusResult.details.includes('postgres');
            }
        }

        if (dotEl) {
            dotEl.className = isInstRunning ? 'dot running' : 'dot stopped';
        }
    });
}

function setButtonsState(cardId, disabled) {
    const card = document.getElementById(cardId);
    if (!card) return;
    const btns = card.querySelectorAll('.btn');
    btns.forEach(b => b.disabled = disabled);
}

async function checkStatus(type, instanceId, cardId) {
    const creds = getCredentials();
    if (!creds) return;

    // 상태 조회의 핵심 수정 포인트:
    // ps -ef 프로세스 스캔은 꼭 해당 서비스 전용 데몬 계정으로 접속하지 않아도 조회가 됩니다.
    // 맨 위쪽 전역 Discovery Username/Password를 그대로 사용하여 조회만 가볍게 수행합니다.
    const payload = {
        host: creds.host,
        port: creds.port,
        username: creds.username,
        password: creds.password,
        instance_id: instanceId,
        target_services: creds.target_services
    };

    setButtonsState(cardId, true);
    document.getElementById(cardId).querySelector('.log-output').innerText = "Checking status...";
    document.getElementById(cardId).querySelector('.status-text').innerText = "...";

    try {
        const response = await fetch(`/api/${type}/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        updateStatusUI(cardId, data, type);
    } catch (err) {
        updateStatusUI(cardId, { status: 'error', message: err.message });
    } finally {
        setButtonsState(cardId, false);
    }
}

async function controlService(type, action, instanceId, cardId) {
    const creds = getCredentials();
    if (!creds) return;

    const cardUser = document.getElementById(`${cardId}-user`).value;
    const cardPass = document.getElementById(`${cardId}-pass`).value;

    const runUser = cardUser || creds.username;
    const runPass = cardPass || creds.password;

    const payload = {
        host: creds.host,
        port: creds.port,
        username: runUser,
        password: runPass,
        instance_id: instanceId,
        target_services: creds.target_services
    };

    setButtonsState(cardId, true);
    const card = document.getElementById(cardId);
    card.querySelector('.log-output').innerText = `Sending ${action} command...`;

    try {
        const response = await fetch(`/api/${type}/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();

        let logText = "";
        if (data.success) {
            logText = `[SUCCESS] Action completed.\n${data.logs}\n${data.error}`;
        } else {
            logText = `[FAILED] Error occurred.\n${data.error || data.message}\n${data.logs || ''}`;
        }
        card.querySelector('.log-output').innerText = logText.trim();

        setTimeout(() => checkStatus(type, instanceId, cardId), 2500);
    } catch (err) {
        card.querySelector('.log-output').innerText = `[ERROR] Request failed.\n${err.message}`;
        setButtonsState(cardId, false);
    }
}
