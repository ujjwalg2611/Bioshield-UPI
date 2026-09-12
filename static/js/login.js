// ── Redirect if already logged in ──
if (BioShieldAPI.getToken() && BioShieldAPI.getUser()) {
    location.href = '/dashboard';
}

const capture = new KeystrokeCapture({ minKeys: 5 });
const pwdInput = document.getElementById('password');
const hudContainer = document.getElementById('biometric-hud');

capture.attach(pwdInput);

capture.onUpdate = () => {
    if(hudContainer.style.display !== 'block') {
         hudContainer.style.display = 'block';
    }
    new BiometricHUD(hudContainer, capture);
};

function showAlert(msg, type) {
    const el = document.getElementById('alertBox');
    el.textContent = msg;
    el.className = `alert alert-${type} show`;
}

document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    const btn = document.getElementById('loginBtn');
    const btnText = document.getElementById('btnText');
    const btnSpinner = document.getElementById('btnSpinner');

    btnText.textContent = 'Analyzing Telemetry...';
    btnSpinner.style.display = 'inline-block';
    btn.disabled = true;

    try {
        const data = await BioShieldAPI.login({
            email: document.getElementById('email').value.trim(),
            password: document.getElementById('password').value,
            keystroke_data: capture.getData()
        });

        BioShieldAPI.setToken(data.token);
        BioShieldAPI.setUser(data.user);

        const risk = data.risk || {};

        if (risk.decision === 'BLOCK') {
            showAlert(`Access Denied: Anomalous telemetry detected (${Math.round((risk.score||0)*100)}% risk variance).`, 'error');
            btn.disabled = false;
            btnText.textContent = 'Authenticate Session';
            btnSpinner.style.display = 'none';
            return;
        }

        if (risk.decision === 'OTP_REQUIRED') {
            showAlert('High variance detected. Initiating secondary protocol (OTP).', 'warn');
            setTimeout(() => location.href = '/otp', 1600); // Assuming you build an OTP page later
            return;
        }

        showAlert('DNA verified. Establishing secure connection...', 'success');
        setTimeout(() => location.href = '/dashboard', 1200);

    } catch (err) {
        showAlert(err.error || 'Connection failed. Verify credentials.', 'error');
        btn.disabled = false;
        btnText.textContent = 'Authenticate Session';
        btnSpinner.style.display = 'none';
    }
});
