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

    const types = ['oracle', 'postgres', 'rts', 'dg', 'pjs'];
    const typeNames = {
        'oracle': 'Oracle DB',
        'postgres': 'PostgreSQL',
        'rts': 'RTS Process',
        'dg': 'DataGather',
        'pjs': 'PlatformJS'
    };

    const typeIcons = {
        'oracle': `<div style="width: 1.6rem; height: 1.6rem; background-color: #F80000; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-right: 0.5rem;"><svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 65%; height: 65%;"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg></div>`,
        'postgres': `<img src="https://upload.wikimedia.org/wikipedia/commons/2/29/Postgresql_elephant.svg" alt="PostgreSQL" style="height: 1.6rem; margin-right: 0.5rem; filter: drop-shadow(0 0 2px rgba(255,255,255,0.2));">`,
        'rts': `<img src="/static/maxgauge.png" alt="MaxGauge RTS" style="height: 1.6rem; object-fit: contain; margin-right: 0.5rem;" onerror="this.style.display='none'">`,
        'dg': `<img src="/static/maxgauge.png" alt="MaxGauge DG" style="height: 1.6rem; object-fit: contain; margin-right: 0.5rem; filter: hue-rotate(180deg);" onerror="this.style.display='none'">`,
        'pjs': `<img src="/static/maxgauge.png" alt="MaxGauge PJS" style="height: 1.6rem; object-fit: contain; margin-right: 0.5rem; filter: hue-rotate(90deg);" onerror="this.style.display='none'">`
    };

    types.forEach(type => {
        const typeServices = services.filter(s => s.type === type);
        if (typeServices.length === 0) return;

        // 각 타입별 첫 번째 서비스의 run_as를 기본 User로 사용
        const runAs = typeServices[0].run_as;
        const cardId = `card-${type}`;

        let instancesHtml = '';
        typeServices.forEach(srv => {
            const displayId = srv.display_id || srv.instance_id;
            const pathInfo = srv.path ? `<div style="font-size: 0.75rem; color: #64748b; margin-top: 0.2rem; margin-left: 1.5rem; word-break: break-all;">${srv.path}</div>` : '';
            const childProcesses = normalizeChildProcesses(type, srv.child_processes);
            const companionInfo = childProcesses.length > 0
                ? `<div style="margin-top: 0.35rem; margin-left: 1.5rem;">
                    <div style="font-size: 0.72rem; color: #64748b; margin-bottom: 0.3rem;">Child Processes</div>
                    <div style="display:flex; flex-wrap:wrap; gap:0.6rem;">${childProcesses.map(proc => `
                    <div class="child-proc-item" data-parent-id="${srv.instance_id}" data-proc="${proc.name}" data-available="${proc.available}" style="font-size: 0.78rem; color: #94a3b8; display:flex; align-items:center; gap:0.35rem;">
                        <span id="child-dot-${cardId}-${srv.instance_id}-${proc.name}" class="dot ${proc.available ? (proc.running ? 'running' : 'stopped') : 'unknown'}" style="width: 8px; height: 8px; display: inline-block;"></span>
                        <span id="child-label-${cardId}-${srv.instance_id}-${proc.name}">${proc.label}${proc.available ? '' : ' (N/A)'}</span>
                    </div>
                `).join('')}</div>
                </div>`
                : '';

            instancesHtml += `
                <div style="padding: 0.6rem 0; border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="font-weight: 500; color: #38bdf8; font-size: 0.95rem; display: flex; align-items: center; gap: 0.5rem;" class="instance-item" data-id="${srv.instance_id}">
                            <span id="dot-${cardId}-${srv.instance_id}" class="dot unknown" style="width: 10px; height: 10px; display: inline-block;"></span>
                            ${displayId}
                        </div>
                        <div style="display: flex; flex-direction: row; gap: 0.5rem; flex-shrink: 0;">
                            <button class="btn start-btn" style="padding: 0.3rem 0.6rem; font-size: 0.8rem; white-space: nowrap; width: 60px;" onclick="controlService('${type}', 'start', '${srv.instance_id}', '${cardId}')">시작</button>
                            <button class="btn stop-btn" style="padding: 0.3rem 0.6rem; font-size: 0.8rem; white-space: nowrap; width: 60px;" onclick="controlService('${type}', 'stop', '${srv.instance_id}', '${cardId}')">정지</button>
                        </div>
                    </div>
                    ${pathInfo}
                    ${companionInfo}
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

function normalizeChildProcesses(type, childProcesses) {
    const source = childProcesses || {};
    const orderByType = {
        rts: ['obsd', 'sndf', 'updater'],
        dg: ['obsd'],
        pjs: ['obsd']
    };
    const order = orderByType[type] || [];
    return order.map(name => {
        const meta = source[name] || {};
        return {
            name,
            label: name.toUpperCase(),
            running: Boolean(meta.running),
            available: Boolean(meta.available)
        };
    });
}

function parsePwdxMap(statusDetails) {
    if (!statusDetails || !statusDetails.includes('---PWDX_INFO---')) {
        return {};
    }

    const parts = statusDetails.split('---PWDX_INFO---');
    if (parts.length < 2) {
        return {};
    }

    const pathMap = {};
    parts[1].split('\n').forEach(line => {
        if (!line.includes(':')) return;
        const idx = line.indexOf(':');
        const pid = line.slice(0, idx).trim();
        const path = line.slice(idx + 1).trim().replace(/\/+$/, '');
        if (pid && path) {
            pathMap[pid] = path;
        }
    });
    return pathMap;
}

function normalizeRuntimePath(path, instanceName) {
    if (!path) return '';

    const confSuffix = instanceName ? `/conf/${instanceName}` : '';
    if (confSuffix && path.endsWith(confSuffix)) {
        return path.slice(0, -confSuffix.length);
    }

    const confIdx = path.indexOf('/conf/');
    if (confIdx >= 0) {
        return path.slice(0, confIdx);
    }

    return path;
}

function getProcessStatusMap(statusDetails, processNames) {
    const processMap = {};
    if (!statusDetails) {
        return processMap;
    }

    const psSection = statusDetails.split('---PWDX_INFO---')[0] || '';
    const pathMap = parsePwdxMap(statusDetails);

    psSection.split('\n').forEach(line => {
        const trimmed = line.trim();
        if (!trimmed) return;

        const lower = trimmed.toLowerCase();
        const match = lower.match(/mxg_(rts|obsd|updater|sndf)/);
        if (!match) return;

        const procName = match[1];
        if (!processNames.includes(procName)) return;
        const fields = trimmed.split(/\s+/);
        const pid = fields.length > 1 ? fields[1] : '';
        const instMatch = trimmed.match(/(?:^|\s)-c\s+(\S+)|(?:^|\s)c\s+(\S+)/);
        const instanceName = instMatch ? (instMatch[1] || instMatch[2] || '') : '';
        const runtimePath = normalizeRuntimePath(pathMap[pid] || '', instanceName);
        const keys = [runtimePath, instanceName].filter(Boolean);

        keys.forEach(key => {
            if (!processMap[key]) {
                processMap[key] = {};
            }
            processMap[key][procName] = true;
        });
    });

    return processMap;
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
    const processNamesByType = {
        rts: ['rts', 'obsd', 'sndf', 'updater'],
        dg: ['obsd'],
        pjs: ['obsd']
    };
    const processStateMap = processNamesByType[type]
        ? getProcessStatusMap(statusResult.details, processNamesByType[type])
        : {};

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
                const shortInstId = instId.includes('/') ? instId.split('/').pop() : instId;
                isInstRunning = Boolean(
                    processStateMap[instId]?.rts ||
                    processStateMap[shortInstId]?.rts
                );
            } else if (type === 'dg') {
                isInstRunning = statusResult.status === 'running';
            } else if (type === 'pjs') {
                if (instId.includes('/')) {
                    // 동일한 이름(SID)을 가진 가짜가 먼저 불 켜지는 착시 방지
                    let pwdxValid = false;
                    if (statusResult.details.includes('---PWDX_INFO---')) {
                        const chunks = statusResult.details.split('---PWDX_INFO---');
                        if (chunks.length > 1 && chunks[1].trim().length > 0) {
                            pwdxValid = true;
                        }
                    }
                    if (pwdxValid) {
                        isInstRunning = statusResult.details.includes(instId);
                    } else {
                        // AIX 등에서 pwdx/procwdx 출력조차 실패한 최후의 보루 시, 기존처럼 짧은 SID 명칭으로라도 검사
                        const shortInstId = instId.split('/').pop();
                        isInstRunning = statusResult.details.includes(shortInstId);
                    }
                } else {
                    isInstRunning = statusResult.details.includes(instId);
                }
            } else {
                isInstRunning = statusResult.details.includes(instId) || statusResult.details.includes('postgres');
            }
        }

        if (dotEl) {
            dotEl.className = isInstRunning ? 'dot running' : 'dot stopped';
        }

        if (type === 'rts' || type === 'dg' || type === 'pjs') {
            const shortInstId = instId.includes('/') ? instId.split('/').pop() : instId;
            const procState = processStateMap[instId] || processStateMap[shortInstId] || {};
            card.querySelectorAll(`.child-proc-item[data-parent-id="${instId}"]`).forEach(child => {
                const procName = child.getAttribute('data-proc');
                const isAvailable = child.getAttribute('data-available') === 'true';
                const childDot = document.getElementById(`child-dot-${cardId}-${instId}-${procName}`);
                const childLabel = document.getElementById(`child-label-${cardId}-${instId}-${procName}`);
                if (childDot) {
                    if (!isAvailable) {
                        childDot.className = 'dot unknown';
                    } else {
                        childDot.className = procState[procName] ? 'dot running' : 'dot stopped';
                    }
                }
                if (childLabel) {
                    const baseLabel = procName.toUpperCase();
                    childLabel.innerText = isAvailable ? baseLabel : `${baseLabel} (N/A)`;
                }
            });
        }
    });
}

function setButtonsState(cardId, disabled) {
    const card = document.getElementById(cardId);
    if (!card) return;
    const btns = card.querySelectorAll('.btn');
    btns.forEach(b => b.disabled = disabled);
}

function updateCardHeader(cardId, status, details) {
    const card = document.getElementById(cardId);
    if (!card) return;

    const dot = card.querySelector('.dot');
    const text = card.querySelector('.status-text');
    const logs = card.querySelector('.log-output');

    dot.className = 'dot';
    text.className = 'status-text target-status';

    if (status === 'running') {
        dot.classList.add('running');
        text.classList.add('running');
        text.innerText = 'RUNNING';
    } else if (status === 'stopped') {
        dot.classList.add('stopped');
        text.classList.add('stopped');
        text.innerText = 'STOPPED';
    } else {
        dot.classList.add('unknown');
        text.classList.add('unknown');
        text.innerText = 'ERROR';
    }

    logs.innerText = (details || '').trim() || 'No output.';
}

function updateSingleInstanceStatus(cardId, type, instanceId, statusResult) {
    const card = document.getElementById(cardId);
    if (!card) return;

    const dotEl = document.getElementById(`dot-${cardId}-${instanceId}`);
    if (dotEl) {
        dotEl.className = statusResult.status === 'running' ? 'dot running' : 'dot stopped';
    }

    const processNamesByType = {
        rts: ['rts', 'obsd', 'sndf', 'updater'],
        dg: ['obsd'],
        pjs: ['obsd']
    };
    const processStateMap = processNamesByType[type]
        ? getProcessStatusMap(statusResult.details, processNamesByType[type])
        : {};
    const shortInstId = instanceId.includes('/') ? instanceId.split('/').pop() : instanceId;
    const procState = processStateMap[instanceId] || processStateMap[shortInstId] || {};

    card.querySelectorAll(`.child-proc-item[data-parent-id="${instanceId}"]`).forEach(child => {
        const procName = child.getAttribute('data-proc');
        const isAvailable = child.getAttribute('data-available') === 'true';
        const childDot = document.getElementById(`child-dot-${cardId}-${instanceId}-${procName}`);
        const childLabel = document.getElementById(`child-label-${cardId}-${instanceId}-${procName}`);

        if (childDot) {
            if (!isAvailable) {
                childDot.className = 'dot unknown';
            } else {
                childDot.className = procState[procName] ? 'dot running' : 'dot stopped';
            }
        }

        if (childLabel) {
            const baseLabel = procName.toUpperCase();
            childLabel.innerText = isAvailable ? baseLabel : `${baseLabel} (N/A)`;
        }
    });
}

function updateMultiInstanceStatus(cardId, type, resultMap) {
    const instanceIds = Object.keys(resultMap);
    const results = instanceIds.map(instanceId => resultMap[instanceId]).filter(Boolean);
    const anyRunning = results.some(result => result.status === 'running');
    const anyError = results.some(result => result.status === 'error');
    const headerStatus = anyRunning ? 'running' : (anyError ? 'error' : 'stopped');
    const headerDetails = instanceIds.map(instanceId => {
        const result = resultMap[instanceId] || {};
        const body = (result.details || result.message || '').trim();
        return `[${instanceId}] ${body || result.status || 'unknown'}`;
    }).join('\n\n');

    updateCardHeader(cardId, headerStatus, headerDetails);

    instanceIds.forEach(instanceId => {
        updateSingleInstanceStatus(cardId, type, instanceId, resultMap[instanceId]);
    });
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
        if (type === 'dg' && !instanceId) {
            const instanceIds = Array.from(
                document.querySelectorAll(`#${cardId} .instance-item`)
            ).map(item => item.getAttribute('data-id')).filter(Boolean);

            const responses = await Promise.all(instanceIds.map(async currentInstanceId => {
                const response = await fetch(`/api/${type}/status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ...payload,
                        instance_id: currentInstanceId
                    })
                });
                const data = await response.json();
                return [currentInstanceId, data];
            }));

            updateMultiInstanceStatus(cardId, type, Object.fromEntries(responses));
            return;
        }

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

    const useCardCredentials = cardUser && cardPass;
    const runUser = useCardCredentials ? cardUser : creds.username;
    const runPass = useCardCredentials ? cardPass : creds.password;

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
            const reason = data.error || data.message || data.logs || 'No error text returned.';
            logText = `[FAILED] Error occurred.\n${reason}\n${data.logs || ''}`;
        }
        card.querySelector('.log-output').innerText = logText.trim();

        // 약간의 종료/기동 여유 시간(Delay)을 두어 확실히 상태가 안정된 직후에만 두 번 폴링
        setTimeout(() => checkStatus(type, '', cardId), 2000);
        setTimeout(() => checkStatus(type, '', cardId), 4500);
    } catch (err) {
        card.querySelector('.log-output').innerText = `[ERROR] Request failed.\n${err.message}`;
        setButtonsState(cardId, false);
    }
}
