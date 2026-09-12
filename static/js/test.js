// ── Auth guard (this page hits a real, authenticated backend endpoint) ──
if (!BioShieldAPI.getToken()) location.href = '/login';

const capture = new KeystrokeCapture({ minKeys: 8 });
const testInput = document.getElementById('testInput');
const testBtn = document.getElementById('testBtn');
const hudDwell = document.getElementById('hudDwell');
const hudFlight = document.getElementById('hudFlight');
const hudSpeed = document.getElementById('hudSpeed');
const hudProgress = document.getElementById('hudProgress');
const targetPhrase = "the quick brown fox jumps over the lazy dog";

function updateDynamicHUD() {
    const stats = capture.getStats();

    hudDwell.textContent = stats.avg_dwell ? `${stats.avg_dwell} ms` : '—';
    hudFlight.textContent = stats.avg_flight ? `${stats.avg_flight} ms` : '—';
    hudSpeed.textContent = stats.typing_speed && stats.typing_speed !== '0.00' ? `${stats.typing_speed} k/s` : '—';

    let matchedLen = 0;
    if (testInput.value) {
        for (let i = 0; i < testInput.value.length; i++) {
            if (i < targetPhrase.length && testInput.value[i] === targetPhrase[i]) matchedLen++;
        }
    }
    hudProgress.textContent = `${matchedLen}/${targetPhrase.length}`;

    // Auto color input if correct
    if (testInput.value.trim() === targetPhrase) {
        testInput.style.borderColor = 'var(--success)';
        testInput.style.color = 'var(--success)';
    } else {
        testInput.style.borderColor = 'var(--border)';
        testInput.style.color = 'var(--text-main)';
    }

    testBtn.disabled = !capture.isReady() || testInput.value.length < targetPhrase.length * 0.7;
}

capture.attach(testInput);
capture.onUpdate = updateDynamicHUD;
testInput.addEventListener('input', updateDynamicHUD);

function showAlert(msg, type) {
    const el = document.getElementById('alertBox');
    el.textContent = msg;
    el.className = `alert alert-${type} show`;
}

testBtn.addEventListener('click', async () => {
    document.getElementById('btnText').textContent = 'Analyzing Neural Pattern...';
    document.getElementById('btnSpinner').style.display = 'inline-block';
    testBtn.disabled = true;

    try {
        // Sends the exact shape app.py's extract_features() expects:
        // dwell_times, flight_times, press_intervals, backspace_count, total_keys, duration_ms
        const keystroke_data = capture.getData();
        const data = await BioShieldAPI.test({ keystroke_data });

        if (data.reason === 'No baseline profile') {
            showAlert('No enrolled profile yet — finish enrollment first.', 'error');
            return;
        }

        const isRecognized = data.result === 'Recognized';
        const score = data.risk_score || 0;

        document.getElementById('resultIcon').innerHTML = isRecognized ? '✅' : '❌';
        document.getElementById('resultLabel').textContent = data.result;
        document.getElementById('resultLabel').style.color = isRecognized ? 'var(--success)' : 'var(--danger)';

        document.getElementById('riskInfo').innerHTML = renderRiskBadge(data.decision, score);

        const features = data.features || {};
        const details = data.details || {};
        document.getElementById('featureGrid').innerHTML = `
            <div class="stat-card"><div class="hud-label">Dwell Time</div><div class="hud-value">${features.avg_dwell_time ? features.avg_dwell_time.toFixed(1)+' ms' : '—'}</div></div>
            <div class="stat-card"><div class="hud-label">Flight Time</div><div class="hud-value">${features.avg_flight_time ? features.avg_flight_time.toFixed(1)+' ms' : '—'}</div></div>
            <div class="stat-card"><div class="hud-label">Typing Speed</div><div class="hud-value">${features.avg_typing_speed ? features.avg_typing_speed.toFixed(2)+' cps' : '—'}</div></div>
            <div class="stat-card"><div class="hud-label">Rhythm Jitter</div><div class="hud-value">${features.avg_jitter ? features.avg_jitter.toFixed(1)+' ms' : '—'}</div></div>
            <div class="stat-card"><div class="hud-label">Backspace Rate</div><div class="hud-value">${features.avg_backspace_rate !== undefined ? (features.avg_backspace_rate*100).toFixed(1)+'%' : '—'}</div></div>
            <div class="stat-card"><div class="hud-label">Confidence</div><div class="hud-value">${details.confidence !== undefined ? details.confidence : '—'}</div></div>
        `;

        document.getElementById('resultPanel').style.display = 'block';
        document.getElementById('alertBox').classList.remove('show');

    } catch (err) {
        showAlert(err.error || err.message || 'Authentication engine error', 'error');
    } finally {
        document.getElementById('btnText').textContent = 'Analyze Neural Pattern';
        document.getElementById('btnSpinner').style.display = 'none';
    }
});

document.getElementById('retestBtn').addEventListener('click', () => {
    capture.reset();
    testInput.value = '';
    testInput.style.borderColor = 'var(--border)';
    testInput.style.color = 'var(--text-main)';
    document.getElementById('resultPanel').style.display = 'none';
    document.getElementById('alertBox').classList.remove('show');
    testBtn.disabled = true;
    updateDynamicHUD();
    testInput.focus();
});

updateDynamicHUD();
