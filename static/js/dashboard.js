if (!BioShieldAPI.getToken()) location.href = '/login';

mermaid.initialize({ startOnLoad: false, theme: 'dark', background: 'transparent' });

document.getElementById('logoutBtn').addEventListener('click', (e) => {
    e.preventDefault();
    BioShieldAPI.clearToken();
    location.href = '/login';
});

function formatTs(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', {month:'short', day:'numeric'}) + ' ' + 
           d.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'});
}

function getBadgeProps(status) {
    if (['ALLOW', 'SUCCESS', 'PASSED_BIOMETRIC', 'BIOMETRIC'].includes(status)) return { class: 'badge-success', text: status };
    if (['OTP_REQUIRED', 'OTP', 'OTP_VERIFIED'].includes(status)) return { class: 'badge-warn', text: status };
    return { class: 'badge-danger', text: status || 'FAILED' };
}

function updateIntegrityUI(isEnrolled, score) {
    const bar = document.getElementById('healthBar');
    const scoreText = document.getElementById('healthScore');
    const badgeContainer = document.getElementById('statusBadgeContainer');
    
    const color = isEnrolled ? 'var(--accent-green)' : 'var(--accent-red)';
    const shadow = isEnrolled ? 'var(--accent-green-glow)' : 'var(--accent-red-glow)';
    
    bar.style.width = score + '%';
    bar.style.background = color;
    bar.style.boxShadow = `0 0 15px ${shadow}`;
    
    scoreText.style.color = color;
    scoreText.textContent = score + '%';

    if (isEnrolled) {
        badgeContainer.innerHTML = '<span class="badge badge-success">● PROTECTED</span>';
    } else {
        badgeContainer.innerHTML = '<span class="badge badge-danger">● VULNERABLE</span> <a href="/enroll" style="color:var(--text-primary); font-size:0.8rem; margin-left:10px;">Fix Now →</a>';
    }
}

async function renderDynamicFlowchart(profileData, recentEvents) {
    const container = document.getElementById('mermaidContainer');
    let baseRisk = profileData ? 'Active Baseline' : 'No Baseline';
    let path = 'Pending';
    
    if (recentEvents && recentEvents.length > 0) {
        const lastRisk = recentEvents[0].risk_level;
        if (lastRisk === 'ALLOW') path = 'Approved';
        else if (lastRisk === 'OTP_REQUIRED') path = 'OTP Requested';
        else path = 'Blocked';
    }

    const graphDefinition = `
        graph TD
            A[Input Keystrokes] --> B{${baseRisk}}
            B -->|Z-Score < 0.35| C[Approve TXN]
            B -->|Z-Score 0.35 - 0.65| D[Trigger OTP]
            B -->|Z-Score > 0.65| E[Block TXN]
            
            classDef default fill:#1e293b,stroke:#334155,color:#f8fafc
            classDef activePath fill:#06b6d420,stroke:#06b6d4,color:#06b6d4,stroke-width:2px
            
            ${path === 'Approved' ? 'class C activePath' : ''}
            ${path === 'OTP Requested' ? 'class D activePath' : ''}
            ${path === 'Blocked' ? 'class E activePath' : ''}
    `;

    try {
        const { svg } = await mermaid.render('dynamicFlow', graphDefinition);
        container.innerHTML = svg;
    } catch (e) {
        container.innerHTML = `<span style="color:var(--text-tertiary)">Graph render failed</span>`;
    }
}

async function loadDashboard() {
    try {
        const data = await BioShieldAPI.riskHistory();
        const { user, profile, risk_events, transactions } = data;

        document.getElementById('welcomeName').textContent = user.full_name;
        document.getElementById('upiDisplay').textContent = user.upi_id;
        
        const healthScoreValue = user.is_enrolled ? 100 : 30;
        updateIntegrityUI(user.is_enrolled, healthScoreValue);

        const allow = risk_events.filter(e => e.risk_level === 'ALLOW').length;
        const flagged = risk_events.filter(e => e.risk_level === 'OTP_REQUIRED').length;
        const blocked = risk_events.filter(e => e.risk_level === 'BLOCK' || e.resolution === 'BLOCKED').length;

        document.getElementById('statBalance').textContent = `₹${user.balance.toLocaleString('en-IN', {minimumFractionDigits:2})}`;
        document.getElementById('statCleared').textContent = allow;
        document.getElementById('statFlagged').textContent = flagged;
        document.getElementById('statBlocked').textContent = blocked;

        if (profile) {
            const rows = [
                ['Avg Dwell Time', `${profile.avg_dwell_time.toFixed(1)} ms`],
                ['Avg Flight Time', `${profile.avg_flight_time.toFixed(1)} ms`],
                ['Typing Speed', `${profile.avg_typing_speed.toFixed(2)} chars/sec`],
                ['Rhythm Jitter', `${profile.avg_jitter.toFixed(1)} ms`],
                ['Error Correction Rate', `${(profile.avg_backspace_rate * 100).toFixed(1)}%`],
                ['Total Samples Trained', profile.sample_count],
            ];
            document.getElementById('profileSection').innerHTML = rows.map(([lbl, val]) => `
                <div class="dna-row">
                    <span class="dna-label">${lbl}</span>
                    <span class="dna-value">${val}</span>
                </div>
            `).join('');
        } else {
            document.getElementById('profileSection').innerHTML = `<p style="color:var(--text-tertiary); font-size:0.95rem;">Baseline not established. Please enroll biometrics.</p>`;
        }

        const tbody = document.getElementById('txnTableBody');
        if (transactions.length === 0 && risk_events.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-tertiary); padding: 2rem;">No recorded activity.</td></tr>`;
        } else {
            const allActivity = [...transactions, ...risk_events.filter(e => !e.amount)]
                .sort((a,b) => new Date(b.created_at) - new Date(a.created_at))
                .slice(0, 8);
            
            tbody.innerHTML = allActivity.map(item => {
                const isTxn = !!item.txn_id;
                const status = isTxn ? item.status : item.risk_level;
                const badgeProps = getBadgeProps(isTxn ? (item.auth_method || item.status) : item.risk_level);
                const desc = isTxn ? `TXN to ${item.recipient_upi}` : `EVENT: ${item.event_type}`;
                const amt = item.amount ? `₹${item.amount.toFixed(2)}` : '—';
                const auth = isTxn ? item.auth_method || 'PENDING' : 'ANALYSIS';

                return `
                    <tr>
                        <td><span class="badge ${getBadgeProps(status).class}">${status}</span></td>
                        <td style="font-family:var(--font-mono); font-size:0.85rem; color:var(--text-secondary);">${desc}</td>
                        <td class="tx-amount">${amt}</td>
                        <td><span class="badge ${badgeProps.class}">${auth}</span></td>
                        <td class="tx-date">${formatTs(item.created_at)}</td>
                    </tr>
                `;
            }).join('');
        }

        await renderDynamicFlowchart(profile, risk_events);

    } catch (err) {
        console.error(err);
        if (err.status === 401) {
            BioShieldAPI.clearToken();
            location.href = '/login';
        }
        document.getElementById('welcomeName').textContent = "Connection Error";
        document.getElementById('upiDisplay').textContent = "Please refresh the page.";
    }
}

loadDashboard();
