// ── Auth guard ──
if (!BioShieldAPI.getToken()) location.href = '/login';

// Flow: PIN (typed naturally) -> if typing rhythm matches, paid.
// If it doesn't: choose Face match OR an OTP sent to the registered mobile.
// The server owns the state machine; this file only reacts to the `status` it returns.

let pendingEventId = null;
let pendingAmount = 0;
let pendingRecipient = '';
let cameraStream = null;
let lastOptions = ['OTP'];
const capture = new KeystrokeCapture({ minKeys: 4 });

const $ = (id) => document.getElementById(id);
const pinInput = $('pin_input');
const hudEl = $('biometric-hud');

// ── Balance ──
async function loadBalance() {
  try {
    const d = await BioShieldAPI.riskHistory();
    $('balanceDisplay').textContent = `₹${d.user.balance.toLocaleString('en-IN', {minimumFractionDigits:2})}`;
  } catch {}
}
loadBalance();

// ── Helpers ──
function showAlert(id, msg, type) {
  const el = $(id);
  el.textContent = msg;
  el.className = `alert alert-${type} show`;
}
function showModal(id) {
  const m = $(id);
  m.style.display = 'flex';
  m.offsetHeight;
  m.classList.add('show');
}
function hideModal(id) {
  const m = $(id);
  m.classList.remove('show');
  setTimeout(() => { if (!m.classList.contains('show')) m.style.display = 'none'; }, 300);
}
function stopCamera() {
  if (cameraStream) cameraStream.getTracks().forEach(t => t.stop());
  cameraStream = null;
}
function clearPin() {
  capture.reset();
  pinInput.value = '';
  hudEl.style.display = 'none';
}
function resetFlow() {
  pendingEventId = null;
  clearPin();
  document.querySelectorAll('.otp-digit').forEach(i => { i.value = ''; });
  $('paymentForm').reset();
}
function closeAllModals() {
  stopCamera();
  ['stepupModal', 'faceModal', 'otpModal'].forEach(hideModal);
}
function cancelFlow(msg) {
  closeAllModals();
  resetFlow();
  showAlert('alertBox', msg, 'error');
}
function succeed(msg) {
  closeAllModals();
  showAlert('alertBox', msg, 'success');
  loadBalance();
  resetFlow();
}

// ── Keystroke capture on the PIN field ──
capture.attach(pinInput);
capture.onUpdate = () => {
  if (hudEl.style.display !== 'block') hudEl.style.display = 'block';
  new BiometricHUD(hudEl, capture);
};

// ── Step router: react to whatever the server says comes next ──
function handleStep(data) {
  if (data.event_id) pendingEventId = data.event_id;
  if (data.options) lastOptions = data.options;
  $('alertBox').classList.remove('show');

  if (data.status === 'STEPUP_REQUIRED') { hideModal('faceModal'); hideModal('otpModal'); stopCamera(); openStepup(data.message); }
  else if (data.status === 'OTP_REQUIRED') { hideModal('stepupModal'); openOtpStep(data); }
  else if (data.status === 'SUCCESS') {
    const how = data.auth_method === 'FACE_ID'
      ? `Face confirmed (${Math.round((data.confidence || 0) * 100)}% match). `
      : data.auth_method === 'OTP' ? 'Code accepted. ' : 'PIN and typing pattern accepted. ';
    succeed(`${how}₹${pendingAmount.toFixed(2)} sent to ${pendingRecipient}. TXN: ${data.txn_id}`);
  }
}

// ── Submit: open payment, then check the PIN straight away ──
$('paymentForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (pinInput.value.length !== 6) { showAlert('alertBox', 'PIN must be 6 digits.', 'error'); return; }

  const btn = $('payBtn');
  $('payBtnText').textContent = 'Verifying...';
  $('paySpinner').style.display = 'inline-block';
  btn.disabled = true;

  pendingAmount = parseFloat($('amount').value);
  pendingRecipient = $('recipient_upi').value.trim();
  const pin = pinInput.value;
  const keystrokes = capture.getData();

  try {
    const started = await BioShieldAPI.paymentInitiate({ recipient_upi: pendingRecipient, amount: pendingAmount });
    pendingEventId = started.event_id;
    handleStep(await BioShieldAPI.paymentPin({ event_id: pendingEventId, pin, keystroke_data: keystrokes }));
  } catch (err) {
    if (err.terminated) cancelFlow(err.error);
    else showAlert('alertBox', err.error || 'Transaction failed.', 'error');
    clearPin();
  } finally {
    btn.disabled = false;
    $('payBtnText').textContent = 'Authorize Transfer';
    $('paySpinner').style.display = 'none';
  }
});

// ── Step-up chooser (only after a typing-rhythm mismatch) ──
function openStepup(msg) {
  clearPin();
  $('stepupAlert').classList.remove('show');
  $('stepupMsg').textContent = msg || "Typing pattern didn't match your profile.";
  $('chooseFaceBtn').style.display = lastOptions.includes('FACE') ? 'block' : 'none';
  showModal('stepupModal');
}

async function sendOtp(fromStepup) {
  const text = $('chooseOtpText'), spin = $('chooseOtpSpinner');
  text.textContent = 'Sending...'; spin.style.display = 'inline-block';
  $('chooseOtpBtn').disabled = true; $('resendOtpBtn').disabled = true;
  try {
    handleStep(await BioShieldAPI.paymentOtpSend({ event_id: pendingEventId }));
  } catch (err) {
    showAlert(fromStepup ? 'stepupAlert' : 'otpAlert', err.error || 'Could not send the code.', 'error');
  } finally {
    text.textContent = 'Send code to my mobile'; spin.style.display = 'none';
    $('chooseOtpBtn').disabled = false; $('resendOtpBtn').disabled = false;
  }
}
$('chooseOtpBtn').addEventListener('click', () => sendOtp(true));
$('resendOtpBtn').addEventListener('click', () => sendOtp(false));
$('cancelStepupBtn').addEventListener('click', () => cancelFlow('Transfer cancelled.'));

// ── Option A: face ──
$('chooseFaceBtn').addEventListener('click', async () => {
  hideModal('stepupModal');
  showModal('faceModal');
  $('faceAlert').classList.remove('show');
  const capBtn = $('captureBtn');
  capBtn.textContent = 'Capture & Authorize';
  capBtn.disabled = true;
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
    $('face-video').srcObject = cameraStream;
    capBtn.disabled = false;
  } catch {
    showAlert('faceAlert', 'Camera unavailable. Go back and use the mobile code instead.', 'error');
  }
});

$('captureBtn').addEventListener('click', async () => {
  const btn = $('captureBtn');
  btn.textContent = 'Matching (can take a few seconds)...';
  btn.disabled = true;

  const video = $('face-video'), canvas = $('face-canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  const imageData = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];

  try {
    handleStep(await BioShieldAPI.paymentFace({ event_id: pendingEventId, face_image: imageData }));
  } catch (err) {
    // e.g. "No face detected" - camera stays on so they can retry
    showAlert('faceAlert', err.error || 'Face verification failed.', 'error');
  } finally {
    btn.textContent = 'Capture & Authorize';
    btn.disabled = false;
  }
});
$('backFromFaceBtn').addEventListener('click', () => { stopCamera(); hideModal('faceModal'); openStepup(); });

// ── Option B: OTP to registered mobile ──
function openOtpStep(data) {
  document.querySelectorAll('.otp-digit').forEach(i => { i.value = ''; });
  $('otpAlert').classList.remove('show');

  $('otpMsg').textContent = data.channel === 'SMS' && data.sent_to
    ? `Enter the code sent by SMS to ${data.sent_to}.`
    : data.channel === 'EMAIL'
      ? 'SMS is not configured on this server, so the code was emailed to you.'
      : 'SMS is not configured on this server; the code was written to the server log.';

  $('demoOtpBox').style.display = data.otp ? 'block' : 'none';
  if (data.otp) $('demoOtp').textContent = data.otp;

  showModal('otpModal');
  document.querySelector('.otp-digit').focus();
}

document.querySelectorAll('.otp-digit').forEach((el, i, all) => {
  el.addEventListener('input', () => { if (el.value && i < all.length - 1) all[i + 1].focus(); });
  el.addEventListener('keydown', (e) => { if (e.key === 'Backspace' && !el.value && i > 0) all[i - 1].focus(); });
});

$('otpSubmitBtn').addEventListener('click', async () => {
  const digits = [...document.querySelectorAll('.otp-digit')].map(i => i.value).join('');
  if (digits.length !== 6) { showAlert('otpAlert', 'Incomplete code.', 'error'); return; }

  $('otpBtnText').textContent = 'Verifying...';
  $('otpSpinner').style.display = 'inline-block';
  $('otpSubmitBtn').disabled = true;

  try {
    handleStep(await BioShieldAPI.paymentOtp({ event_id: pendingEventId, otp: digits }));
  } catch (err) {
    if (err.terminated) cancelFlow(err.error);
    else {
      showAlert('otpAlert', err.error || 'Invalid code.', 'error');
      document.querySelectorAll('.otp-digit').forEach(i => { i.value = ''; });
      document.querySelector('.otp-digit').focus();
    }
  } finally {
    $('otpBtnText').textContent = 'Verify & Transfer';
    $('otpSpinner').style.display = 'none';
    $('otpSubmitBtn').disabled = false;
  }
});
$('backFromOtpBtn').addEventListener('click', () => { hideModal('otpModal'); openStepup(); });
