// ── Redirect if already logged in ──
if (BioShieldAPI.getToken() && BioShieldAPI.getUser()) {
    location.href = '/dashboard';
}

// ── Biometric Capture ──
const capture = new KeystrokeCapture({ minKeys: 4 });
const pwdInput = document.getElementById('password');
const hudContainer = document.getElementById('biometric-hud');

capture.attach(pwdInput);
capture.onUpdate = () => {
    if(hudContainer.style.display !== 'block') {
         hudContainer.style.display = 'block';
    }
    new BiometricHUD(hudContainer, capture);
};

// ── Password Strength Logic ──
const strBars = [
    document.getElementById('str1'),
    document.getElementById('str2'),
    document.getElementById('str3'),
    document.getElementById('str4')
];
const strText = document.getElementById('strengthText');

pwdInput.addEventListener('input', (e) => {
    const val = e.target.value;
    let strength = 0;
    
    if (val.length >= 8) strength++;
    if (val.match(/[A-Z]/) && val.match(/[a-z]/)) strength++;
    if (val.match(/[0-9]/)) strength++;
    if (val.match(/[^a-zA-Z0-9]/)) strength++;

    // Reset colors
    strBars.forEach(bar => bar.style.background = 'transparent');
    
    if (val.length === 0) {
        strText.textContent = '';
        return;
    }

    const colors = ['var(--danger)', 'var(--warn)', 'var(--primary)', 'var(--success)'];
    const labels = ['Weak', 'Fair', 'Good', 'Strong'];

    for (let i = 0; i < strength; i++) {
        strBars[i].style.background = colors[strength - 1] || 'var(--danger)';
    }
    strText.textContent = labels[strength - 1] || 'Weak';
    strText.style.color = colors[strength - 1] || 'var(--danger)';
    
    checkMatch(); // Re-verify match if main password changes
});

// ── Password Match Logic ──
const confirmInput = document.getElementById('confirm_password');
const matchIndicator = document.getElementById('matchIndicator');

function checkMatch() {
    const pwd1 = pwdInput.value;
    const pwd2 = confirmInput.value;
    
    if (pwd2.length === 0) {
        matchIndicator.style.display = 'none';
        confirmInput.style.borderColor = 'var(--border)';
        return;
    }

    matchIndicator.style.display = 'block';
    if (pwd1 === pwd2) {
        matchIndicator.innerHTML = '✅';
        confirmInput.style.borderColor = 'var(--success)';
    } else {
        matchIndicator.innerHTML = '❌';
        confirmInput.style.borderColor = 'var(--danger)';
    }
}

confirmInput.addEventListener('input', checkMatch);

// ── Form Submission ──
function showAlert(msg, type) {
    const el = document.getElementById('alertBox');
    el.textContent = msg;
    el.className = `alert alert-${type} show`;
}

document.getElementById('signupForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    const pw = pwdInput.value;
    const pw2 = confirmInput.value;

    if (pw !== pw2) {
        showAlert('Passphrases do not match. Please verify.', 'error');
        return;
    }
    if (pw.length < 8) {
        showAlert('Passphrase must be at least 8 characters.', 'error');
        return;
    }

    const btn = document.getElementById('signupBtn');
    const btnText = document.getElementById('btnText');
    const btnSpinner = document.getElementById('btnSpinner');

    btnText.textContent = 'Generating Keys...';
    btnSpinner.style.display = 'inline-block';
    btn.disabled = true;

    try {
        const data = await BioShieldAPI.signup({
            full_name: document.getElementById('full_name').value.trim(),
            email: document.getElementById('email').value.trim(),
            password: pw
        });

        BioShieldAPI.setToken(data.token);
        BioShieldAPI.setUser(data.user);

        showAlert(`Identity Secured. Assigned ID: ${data.user.upi_id}`, 'success');

        setTimeout(() => {
            location.href = '/enroll';
        }, 1800);

    } catch (err) {
        showAlert(err.error || 'Provisioning failed. Please retry.', 'error');
        btn.disabled = false;
        btnText.textContent = 'Provision Account';
        btnSpinner.style.display = 'none';
    }
});
