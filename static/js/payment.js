// ── Auth guard ──
if (!BioShieldAPI.getToken()) location.href = '/login';

// ── State ──
let pendingEventId  = null;
let pendingAmount   = 0;
let pendingRecipient = '';
let cameraStream    = null;
const capture = new KeystrokeCapture({ minKeys: 4 });

// ── Load balance ──
async function loadBalance() {
  try {
    const d = await BioShieldAPI.riskHistory();
    document.getElementById('balanceDisplay').textContent = `₹${d.user.balance.toLocaleString('en-IN', {minimumFractionDigits:2})}`;
  } catch {}
}
loadBalance();

// ── Wire PIN keystroke capture ──
const pinInput = document.getElementById('pin_input');
const hudEl = document.getElementById('biometric-hud');

capture.attach(pinInput);
capture.onUpdate = () => {
    if(hudEl.style.display !== 'block') {
         hudEl.style.display = 'block';
    }
    new BiometricHUD(hudEl, capture);
};

function showAlert(id, msg, type) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = `alert alert-${type} show`;
}

// ── Payment form submit ──
document.getElementById('paymentForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('payBtn');
  document.getElementById('payBtnText').textContent = 'Analyzing Telemetry...';
  document.getElementById('paySpinner').style.display = 'inline-block';
  btn.disabled = true;

  pendingAmount   = parseFloat(document.getElementById('amount').value);
  pendingRecipient = document.getElementById('recipient_upi').value.trim();

  try {
    const data = await BioShieldAPI.payment({
      recipient_upi: pendingRecipient,
      amount: pendingAmount,
      pin: document.getElementById('pin_input').value,
      keystroke_data: capture.getData()
    });

    if (data.status === 'BLOCKED') {
      showAlert('alertBox', `Transfer Terminated — Severe risk variance detected (${Math.round((data.risk_score||0)*100)}%).`, 'error');

    } else if (data.status === 'OTP_REQUIRED') {
      pendingEventId = data.event_id;
      document.getElementById('demoOtp').textContent = data.otp || '(check server)';
      
      // Show Modal smoothly
      const modal = document.getElementById('otpModal');
      modal.style.display = 'flex';
      // Trigger reflow to apply animation
      modal.offsetHeight; 
      modal.classList.add('show');
      
      document.getElementById('alertBox').classList.remove('show');

    } else if (data.status === 'SUCCESS') {
      showAlert('alertBox',
        `Transfer Complete. ₹${pendingAmount.toFixed(2)} sent to ${pendingRecipient}. TXN: ${data.txn_id}`,
        'success'
      );
      loadBalance();
      capture.reset();
      document.getElementById('paymentForm').reset();
      hudEl.style.display = 'none';
    }

  } catch (err) {
    showAlert('alertBox', err.error || 'Transaction failed.', 'error');
  } finally {
    btn.disabled = false;
    document.getElementById('payBtnText').textContent = 'Authorize Transfer';
    document.getElementById('paySpinner').style.display = 'none';
  }
});

// ── OTP digit auto-advance ──
document.querySelectorAll('.otp-digit').forEach((el, i, all) => {
  el.addEventListener('input', () => {
    if (el.value && i < all.length - 1) all[i + 1].focus();
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Backspace' && !el.value && i > 0) all[i - 1].focus();
  });
});

// ── OTP submit ──
document.getElementById('otpSubmitBtn').addEventListener('click', async () => {
  const digits = [...document.querySelectorAll('.otp-digit')].map(i => i.value).join('');
  if (digits.length !== 6) { showAlert('otpAlert', 'Incomplete code.', 'error'); return; }

  document.getElementById('otpBtnText').textContent = 'Verifying...';
  document.getElementById('otpSpinner').style.display = 'inline-block';

  try {
    const data = await BioShieldAPI.otpVerify({
      otp: digits,
      event_id: pendingEventId,
      amount: pendingAmount,
      recipient_upi: pendingRecipient
    });
    
    document.getElementById('otpModal').classList.remove('show');
    setTimeout(() => document.getElementById('otpModal').style.display = 'none', 300);
    
    showAlert('alertBox',
      `Authorization Accepted. ₹${pendingAmount.toFixed(2)} sent. TXN: ${data.txn_id}`,
      'success'
    );
    loadBalance();
    capture.reset();
    document.getElementById('paymentForm').reset();
    hudEl.style.display = 'none';
  } catch (err) {
    showAlert('otpAlert', err.error || 'Invalid Code.', 'error');
  } finally {
    document.getElementById('otpBtnText').textContent = 'Verify & Transfer';
    document.getElementById('otpSpinner').style.display = 'none';
  }
});

// ── Open Face ID ──
document.getElementById('openFaceBtn').addEventListener('click', async () => {
  document.getElementById('otpModal').classList.remove('show');
  setTimeout(() => document.getElementById('otpModal').style.display = 'none', 300);
  
  const faceModal = document.getElementById('faceModal');
  faceModal.style.display = 'flex';
  faceModal.offsetHeight;
  faceModal.classList.add('show');

  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
    const video = document.getElementById('face-video');
    video.srcObject = cameraStream;
    document.getElementById('captureBtn').disabled = false;
  } catch {
    showAlert('faceAlert', 'Camera access denied. Please check permissions.', 'error');
  }
});

document.getElementById('backToOtpBtn').addEventListener('click', () => {
  if (cameraStream) cameraStream.getTracks().forEach(t => t.stop());
  
  document.getElementById('faceModal').classList.remove('show');
  setTimeout(() => {
      document.getElementById('faceModal').style.display = 'none';
      const otpModal = document.getElementById('otpModal');
      otpModal.style.display = 'flex';
      otpModal.offsetHeight;
      otpModal.classList.add('show');
  }, 300);
});

// ── Face capture & verify ──
document.getElementById('captureBtn').addEventListener('click', async () => {
  const btn = document.getElementById('captureBtn');
  btn.textContent = 'Processing...';
  btn.disabled = true;

  const video  = document.getElementById('face-video');
  const canvas = document.getElementById('face-canvas');
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  const imageData = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];

  if (cameraStream) cameraStream.getTracks().forEach(t => t.stop());

  try {
    const data = await BioShieldAPI.faceVerify({
      face_image: imageData,
      event_id: pendingEventId,
      amount: pendingAmount,
      recipient_upi: pendingRecipient
    });
    
    document.getElementById('faceModal').classList.remove('show');
    setTimeout(() => document.getElementById('faceModal').style.display = 'none', 300);

    showAlert('alertBox',
      `Identity Confirmed (${Math.round(data.confidence*100)}% match). ₹${pendingAmount.toFixed(2)} sent. TXN: ${data.txn_id}`,
      'success'
    );
    loadBalance();
    capture.reset();
    document.getElementById('paymentForm').reset();
    hudEl.style.display = 'none';
  } catch (err) {
    showAlert('faceAlert', err.error || 'Facial verification failed.', 'error');
    btn.textContent = 'Capture & Authorize';
    btn.disabled = false;
  }
});
