// ── Auth guard ──
if (!BioShieldAPI.getToken()) location.href = '/login';

const PHRASES = [
  "the quick brown fox jumps over the lazy dog",
  "secure payment using biometric identity",
  "keystroke dynamics reveal unique patterns",
  "my upi transfer is protected by bioshield",
  "type naturally to train your profile",
  "neural networks guard each transaction",
  "behavioral authentication never sleeps",
  "every key press tells a unique story",
  "identity verified through typing rhythm",
  "biometrics are the new password system"
];

const phraseBox   = document.getElementById('phrase-box');
const phraseInput = document.getElementById('phraseInput');
const captureBtn  = document.getElementById('captureBtn');
const enrollBtn   = document.getElementById('enrollBtn');
const hudEl       = document.getElementById('biometric-hud');
const sampleCount = document.getElementById('sampleCount');
const progressBar = document.getElementById('progressBar');
const sampleNum   = document.getElementById('sampleNum');
let phraseIndex   = 0;

let collector = new EnrollmentCollector(10);

collector.init(phraseInput, (done, total) => {
  const percent = Math.round((done / total) * 100);
  sampleCount.textContent = `${percent}%`;
  progressBar.style.width = `${percent}%`;
  sampleNum.textContent = done < total ? done + 1 : total;
  
  if (collector.isComplete()) {
    enrollBtn.style.display = 'flex';
    captureBtn.style.display = 'none';
    phraseInput.disabled = true;
    phraseInput.value = 'Phase 1 complete. Ready to proceed.';
    phraseInput.style.borderBottomColor = 'var(--aurora-mint)';
    phraseInput.style.color = 'var(--aurora-mint)';
    hudEl.style.display = 'none';
    clearAlert();
  } else {
    loadPhrase();
  }
});

function loadPhrase() {
  phraseIndex = collector.samples.length % PHRASES.length;
  phraseBox.textContent = PHRASES[phraseIndex];
  phraseInput.value = '';
  phraseInput.disabled = false;
  phraseInput.style.borderBottomColor = 'var(--border-bright)';
  phraseInput.style.color = 'var(--aurora-cyan)';
  phraseInput.focus();
  captureBtn.disabled = true;
  collector.capture.reset();
  
  new BiometricHUD(hudEl, collector.capture);
  clearAlert();
}

phraseInput.addEventListener('input', () => {
    captureBtn.disabled = !collector.capture.isReady();

    const currentText = phraseInput.value.trim();
    const targetText = PHRASES[phraseIndex];
    
    if (currentText === targetText) {
        phraseInput.style.borderBottomColor = 'var(--aurora-mint)';
        phraseInput.style.color = 'var(--aurora-mint)';
        
        setTimeout(() => {
            if (!captureBtn.disabled) captureBtn.click();
        }, 250);
    } else {
        phraseInput.style.borderBottomColor = 'var(--aurora-cyan)';
        phraseInput.style.color = 'var(--aurora-cyan)';
    }
});

phraseInput.addEventListener('keyup', (e) => {
  if (e.key === 'Enter' && !captureBtn.disabled) {
      captureBtn.click();
  }
});

captureBtn.addEventListener('click', () => {
  const recorded = collector.recordSample();
  if (!recorded) {
    showAlert('Pattern too short. Please type the full phrase.', 'warn');
  }
});

// ── Step 1: Keystroke Enrollment ──
enrollBtn.addEventListener('click', async () => {
  document.getElementById('enrollBtnText').textContent = 'Encrypting...';
  document.getElementById('enrollSpinner').style.display = 'inline-block';
  enrollBtn.disabled = true;

  try {
    // Save keystrokes first
    await BioShieldAPI.enroll({ samples: collector.getSamples() });
    showAlert('Keystroke DNA established.', 'success');
    
    // Smooth transition to Face Modal
    setTimeout(async () => {
        document.getElementById('enrollFaceModal').style.display = 'flex';
        
        // Boot up Camera
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ video: true });
            document.getElementById('enroll-face-video').srcObject = stream;
        } catch (camErr) {
            showAlert('Camera access is required to complete Step 2.', 'error');
        }
    }, 800);

  } catch (err) {
    showAlert(err.error || 'Connection failed. Please retry.', 'error');
    enrollBtn.disabled = false;
    document.getElementById('enrollBtnText').textContent = 'Encrypt & Secure Profile';
    document.getElementById('enrollSpinner').style.display = 'none';
  }
});

// ── Step 2: Facial DNA Enrollment ──
document.getElementById('saveFaceBtn').addEventListener('click', async () => {
    const btn = document.getElementById('saveFaceBtn');
    btn.textContent = 'Analyzing Facial Data...';
    btn.disabled = true;

    const video  = document.getElementById('enroll-face-video');
    const canvas = document.getElementById('enroll-face-canvas');
    canvas.width  = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    const imageData = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];

    // Shutdown camera stream
    const stream = video.srcObject;
    if (stream) stream.getTracks().forEach(t => t.stop());

    try {
        const res = await fetch('/api/enroll-face', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${BioShieldAPI.getToken()}`
            },
            body: JSON.stringify({ face_image: imageData })
        });

        if (!res.ok) throw new Error("Failed to save facial data");

        // Complete & Redirect
        btn.style.background = 'var(--aurora-mint)';
        btn.style.color = '#000';
        btn.textContent = '✓ Profile Fully Secured';
        setTimeout(() => location.href = '/dashboard', 1200);

    } catch (err) {
        const faceAlert = document.getElementById('faceEnrollAlert');
        faceAlert.textContent = "Camera error or server failed to save data.";
        faceAlert.className = "alert alert-error show";
        btn.textContent = 'Capture & Complete Enrollment';
        btn.disabled = false;
    }
});

function showAlert(msg, type) {
  const el = document.getElementById('alertBox');
  el.textContent = msg;
  el.className = `alert alert-${type} show`;
}

function clearAlert() {
  const el = document.getElementById('alertBox');
  el.className = 'alert';
  el.textContent = '';
}

loadPhrase();
