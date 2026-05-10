/* Chat UI — uses fetch() + ReadableStream for SSE (EventSource can't POST). */

const messagesEl = document.getElementById('messages');
const traceLog = document.getElementById('trace-log');
const form = document.getElementById('chat-form');
const input = document.getElementById('message-input');
const sendBtn = document.getElementById('send-btn');
const scopeDemoBtn = document.getElementById('scope-demo-btn');
const newChatBtn = document.getElementById('new-chat-btn');

// Pending flow card state machine — batches tool_call + token_trace + token_anatomy
let pendingCard = null;

// New Chat button — reset conversation history
newChatBtn.addEventListener('click', async () => {
    newChatBtn.disabled = true;
    try {
        await fetch('/api/chat/reset', { method: 'POST' });
        messagesEl.innerHTML = messagesEl.querySelector('.system').outerHTML;
        clearTrace();
        addTrace('info', 'Started a new conversation');
    } catch (err) {
        addTrace('error', `Reset failed: ${err.message}`);
    } finally {
        newChatBtn.disabled = false;
        input.focus();
    }
});

// Permission Boundary Demo button
scopeDemoBtn.addEventListener('click', async () => {
    scopeDemoBtn.disabled = true;
    clearTrace();
    addTrace('info', '🔒 Running Scope Isolation Demo…');

    try {
        const resp = await fetch('/api/demo/scope-violation', { method: 'POST' });
        const data = await resp.json();

        if (data.outcome === 'denied') {
            addScopeViolationCard(data);
        } else {
            addTrace('info', '⚠️ ' + data.message);
        }
    } catch (err) {
        addTrace('error', `Demo failed: ${err.message}`);
    } finally {
        scopeDemoBtn.disabled = false;
    }
});

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message) return;

    appendMessage('user', message);
    input.value = '';
    sendBtn.disabled = true;

    clearTrace();
    addTrace('info', 'Sending request…');

    const assistantEl = appendMessage('assistant', '');
    const thinkingEl = document.createElement('div');
    thinkingEl.className = 'thinking';
    thinkingEl.textContent = 'Thinking…';
    assistantEl.appendChild(thinkingEl);

    try {
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
        });

        if (resp.status === 302 || resp.redirected) {
            window.location.href = '/auth/login';
            return;
        }

        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let contentParts = [];

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            const frames = buffer.split('\n\n');
            buffer = frames.pop() || '';

            for (const raw of frames) {
                if (!raw.trim()) continue;
                const lines = raw.split('\n');
                const eventLine = lines.find(l => l.startsWith('event: '));
                const dataLine = lines.find(l => l.startsWith('data: '));
                if (!eventLine || !dataLine) continue;

                try {
                    const data = JSON.parse(dataLine.slice(6));
                    handleEvent(eventLine.slice(7).trim(), data, thinkingEl, contentParts);
                } catch (err) {
                    // skip malformed JSON
                }
            }
        }

        flushPendingCard();

        if (contentParts.length > 0) {
            thinkingEl.remove();
            assistantEl.innerHTML = formatContent(contentParts.join(''));
        }

    } catch (err) {
        thinkingEl.remove();
        assistantEl.textContent = `Error: ${err.message}`;
        addTrace('error', err.message);
    } finally {
        sendBtn.disabled = false;
        input.focus();
    }
});

/* ---- Event handling ---- */

function handleEvent(type, data, thinkingEl, contentParts) {
    switch (type) {
        case 'thinking':
            thinkingEl.textContent = data.content || '…';
            thinkingEl.style.display = '';
            addTrace('thinking', data.content);
            break;

        case 'agent_call':
            flushPendingCard();
            addTrace('agent-call', `${data.agent} — ${data.task}`);
            break;

        case 'tool_call':
            flushPendingCard();
            pendingCard = {
                service: data.service,
                tool: data.tool,
                path: data.path || '',
                el: createFlowCard(data),
            };
            traceLog.appendChild(pendingCard.el);
            scrollTrace();
            break;

        case 'token_trace':
            if (pendingCard && pendingCard.service === data.service) {
                fillFlowCardTrace(pendingCard.el, data);
            } else {
                flushPendingCard();
                addTrace('token-trace',
                    `OBO → ${data.service} [${(data.scopes || []).join(', ')}]`
                );
            }
            scrollTrace();
            break;

        case 'token_anatomy':
            if (pendingCard && pendingCard.service === data.service) {
                fillFlowCardAnatomy(pendingCard.el, data);
                flushPendingCard();
            } else {
                flushPendingCard();
                addLegacyTokenAnatomy(data);
            }
            scrollTrace();
            break;

        case 'content':
            thinkingEl.style.display = 'none';
            contentParts.push(data.content || '');
            break;

        case 'error':
            flushPendingCard();
            addTrace('error', data.message);
            break;

        case 'done':
            flushPendingCard();
            addTrace('info', '✓ Complete');
            break;
    }
}

function flushPendingCard() {
    pendingCard = null;
}

/* ---- Flow Card (progressive rendering) ---- */

function createFlowCard(data) {
    const card = document.createElement('div');
    card.className = 'flow-card';

    // Header: tool → service
    const header = document.createElement('div');
    header.className = 'flow-header';
    header.innerHTML = `<span class="flow-tool">${esc(data.tool)}</span>` +
        `<span class="flow-arrow">→</span>` +
        `<span class="flow-service">${esc(data.service)}</span>`;
    card.appendChild(header);

    // API path
    if (data.path) {
        const pathEl = document.createElement('div');
        pathEl.className = 'flow-path';
        pathEl.textContent = data.path;
        card.appendChild(pathEl);
    }

    // Token flow placeholder
    const flowEl = document.createElement('div');
    flowEl.className = 'flow-chain';
    flowEl.setAttribute('data-section', 'chain');
    card.appendChild(flowEl);

    // Claims placeholder (will be filled by token_anatomy)
    const claimsEl = document.createElement('div');
    claimsEl.className = 'flow-claims';
    claimsEl.setAttribute('data-section', 'claims');
    card.appendChild(claimsEl);

    return card;
}

function fillFlowCardTrace(cardEl, data) {
    const chainEl = cardEl.querySelector('[data-section="chain"]');
    if (!chainEl) return;

    const scopes = (data.scopes || []).join(', ');
    const agentShort = data.agent_identity ? data.agent_identity.slice(0, 8) + '…' : '';

    chainEl.innerHTML = `
        <div class="chain-title">Token Exchange (OBO Flow)</div>
        <div class="chain-steps">
            <div class="chain-step">
                <div class="step-label">User Token</div>
                <div class="step-detail">aud: Blueprint App</div>
            </div>
            <div class="chain-arrow">
                <div class="arrow-line"></div>
                <div class="arrow-label">Agent ID Sidecar</div>
                <div class="arrow-sublabel">OBO + Agent Identity</div>
            </div>
            <div class="chain-step">
                <div class="step-label">Downstream Token</div>
                <div class="step-detail">aud: Microsoft Graph</div>
            </div>
        </div>
        <div class="chain-meta">
            <div><span class="meta-key">Downstream API</span> ${esc(data.service)}</div>
            <div><span class="meta-key">Agent Identity</span> ${esc(agentShort)}</div>
            <div><span class="meta-key">Scopes</span> ${esc(scopes)}</div>
        </div>
    `;
}

function fillFlowCardAnatomy(cardEl, data) {
    const claimsEl = cardEl.querySelector('[data-section="claims"]');
    if (!claimsEl) return;

    const claims = data.claims || {};
    const claimLabels = {
        azp: ['Authorized Party', 'Agent Identity acting on behalf of user'],
        xms_par_app_azp: ['Parent App (Blueprint)', 'The app that requested OBO'],
        scp: ['Delegated Scopes', 'Permissions granted to this agent'],
        aud: ['Audience', 'API this token is valid for'],
        iss: ['Issuer', 'Token authority'],
        sub: ['User Subject', 'Unique user identifier in this app'],
        oid: ['User Object ID', 'Entra directory object ID'],
        tid: ['Tenant ID', 'Entra tenant'],
    };

    const toggle = document.createElement('button');
    toggle.className = 'claims-toggle';
    toggle.textContent = '▸ Downstream Token Claims';
    claimsEl.appendChild(toggle);

    const table = document.createElement('table');
    table.className = 'claims-table claims-hidden';

    for (const [key, val] of Object.entries(claims)) {
        const [label, hint] = claimLabels[key] || [key, ''];
        const row = table.insertRow();

        const labelCell = row.insertCell();
        labelCell.className = 'claim-label';
        labelCell.textContent = label;
        if (hint) labelCell.title = hint;

        const valCell = row.insertCell();
        valCell.className = 'claim-value';
        valCell.textContent = typeof val === 'string' ? val : JSON.stringify(val);
    }

    claimsEl.appendChild(table);

    toggle.addEventListener('click', () => {
        const hidden = table.classList.toggle('claims-hidden');
        toggle.textContent = (hidden ? '▸' : '▾') + ' Downstream Token Claims';
    });
}

/* ---- Legacy fallback for unmatched token_anatomy ---- */

function addLegacyTokenAnatomy(data) {
    const card = document.createElement('div');
    card.className = 'trace-item token-anatomy';
    card.innerHTML = `<div class="anatomy-header">🔑 Token — ${esc(data.service)}</div>`;

    const claims = data.claims || {};
    const table = document.createElement('table');
    table.className = 'claims-table';
    for (const [key, val] of Object.entries(claims)) {
        const row = table.insertRow();
        row.insertCell().textContent = key;
        const vc = row.insertCell();
        vc.className = 'claim-value';
        vc.textContent = typeof val === 'string' ? val : JSON.stringify(val);
    }
    card.appendChild(table);
    traceLog.appendChild(card);
    scrollTrace();
}

/* ---- Scope Violation Card ---- */

function addScopeViolationCard(data) {
    const card = document.createElement('div');
    card.className = 'flow-card flow-card-denied';

    const header = document.createElement('div');
    header.className = 'flow-header flow-header-denied';
    header.innerHTML = `<span class="flow-tool">🛡️ Scope Isolation</span>` +
        `<span class="flow-arrow">✗</span>` +
        `<span class="flow-service">${esc(data.downstream_api_entry || '')}</span>`;
    card.appendChild(header);

    const chainEl = document.createElement('div');
    chainEl.className = 'flow-chain';
    chainEl.innerHTML = `
        <div class="chain-title chain-title-denied">Token Exchange DENIED</div>
        <div class="chain-steps">
            <div class="chain-step">
                <div class="step-label">User Token</div>
                <div class="step-detail">via ${esc(data.agent_identity || 'Agent')}</div>
            </div>
            <div class="chain-arrow chain-arrow-denied">
                <div class="arrow-line"></div>
                <div class="arrow-label">Sidecar OBO</div>
                <div class="arrow-sublabel">Scopes: ${esc((data.token_scopes || data.granted_scopes || []).join(', '))}</div>
            </div>
            <div class="chain-step chain-step-denied">
                <div class="step-label">❌ ${esc(data.attempted_resource || '')}</div>
                <div class="step-detail">Requires: ${esc(data.required_scope || 'User.Read')}</div>
            </div>
        </div>
        <div class="chain-meta">
            <div><span class="meta-key">Agent</span> ${esc(data.agent_identity || '')}</div>
            <div><span class="meta-key">Entry</span> ${esc(data.downstream_api_entry || '')}</div>
            <div><span class="meta-key">Result</span> <span class="denied-badge">DENIED</span></div>
        </div>
    `;
    card.appendChild(chainEl);

    if (data.error_detail) {
        const errDiv = document.createElement('div');
        errDiv.className = 'violation-error';
        errDiv.textContent = data.error_detail;
        card.appendChild(errDiv);
    }

    traceLog.appendChild(card);
    scrollTrace();
}

/* ---- Helpers ---- */

function appendMessage(role, content) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if (content) div.textContent = content;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
}

function formatContent(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}

function clearTrace() {
    traceLog.innerHTML = '';
    pendingCard = null;
}

function addTrace(type, text) {
    const div = document.createElement('div');
    div.className = `trace-item ${type}`;

    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = type.replace('-', ' ');

    div.appendChild(label);
    div.appendChild(document.createTextNode(text));
    traceLog.appendChild(div);
    scrollTrace();
}

function scrollTrace() {
    traceLog.scrollTop = traceLog.scrollHeight;
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
