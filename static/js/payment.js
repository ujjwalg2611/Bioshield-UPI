// ── Auth guard ──
if (!BioShieldAPI.getToken()) location.href = '/login';

// Flow: Face ID first -> (face fails) Authorization PIN -> (typing rhythm fails) OTP.
// The server owns the state machine; this file only reacts to the `status` it returns.

let pendingEventId = null;
let pendingAmount  = 0;
let pendingRecipient = '';
let cameraStream   = null;
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
  setTimeout(() => { m.style.display = 'none'; }, 300);
}
function stopCamera() {
  if (cameraStream) cameraStream.getTracks().forEach(t => t.stop());
  cameraStream = null;
}
function resetFlow() {
  pendingEventId = null;
  capture.reset();
  pinInput.value = '';
  hudEl.style.display = 'none';
  document.querySelectorAll('.otp-digit').forEach(i => { i.value = ''; });
  $('paymentForm').reset();
}
function closeAllModals() {
  stopCamera();
  ['faceModal', 'pinModal', 'otpModal'].forEach(hideModal);
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

// ── Keystroke capture lives on the PIN field (only used in the PIN step) ──
capture.attach(pinInput);
capture.onUpdate = () => {
  if (hudEl.style.display !== 'block') hudEl.style.display = 'block';
  new BiometricHUD(hudEl, capture);
};

// ── Step router: react to whatever the server says comes next ──
function handleStep(data) {
  if (data.event_id) pendingEventId = data.event_id;
  $('alertBox').classList.remove('show');

  if (data.status === 'FACE_REQUIRED')      openFaceStep();
  else if (data.status === 'PIN_REQUIRED')  { stopCamera(); hideModal('faceModal'); openPinStep(data.message); }
  else if (data.status === 'OTP_REQUIRED')  { hideModal('pinModal'); openOtpStep(data); }
  else if (data.status === 'SUCCESS') {
    const how = data.auth_method === 'FACE_ID'
      ? `Identity confirmed (${Math.round((data.confidence || 0) * 100)}% face match). `
      : data.auth_method === 'OTP' ? 'Code accepted. ' : 'PIN and typing pattern accepted. ';
    succeed(`${how}₹${pendingAmount.toFixed(2)} sent to ${pendingRecipient}. TXN: ${data.txn_id}`);
  }
}

// ── Step 0: start payment ──
$('paymentForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('payBtn');
  $('payBtnText').textContent = 'Starting...';
  $('paySpinner').style.display = 'inline-block';
  btn.disabled = true;

  pendingAmount = parseFloat($('amount').value);
  pendingRecipient = $('recipient_upi').value.trim();

  try {
    handleStep(await BioShieldAPI.paymentInitiate({
      recipient_upi: pendingRecipient,
      amount: pendingAmount
    }));
  } catch (err) {
    showAlert('alertBox', err.error || 'Transaction failed.', 'error');
  } finally {
    btn.disabled = false;
    $('payBtnText').textContent = 'Authorize Transfer';
    $('paySpinner').style.display = 'none';
  }
});

// ── Step 1: face ──
async function openFaceStep() {
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
    showAlert('faceAlert', 'Camera unavailable. Cancel and try again, or check browser permissions.', 'error');
  }
}

$('captureBtn').addEventListener('click', async () => {
  const btn = $('captureBtn');
  btn.textContent = 'Matching...';
  btn.disabled = true;

  const video = $('face-video');
  const canvas = $('face-canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  const imageData = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];

  try {
    handleStep(await BioShieldAPI.paymentFace({ event_id: pendingEventId, face_image: imageData }));
  } catch (err) {
    // e.g. "No face detected" - camera stays on so they can retry
    showAlert('faceAlert', err.error || 'Face verification failed.', 'error');
    btn.textContent = 'Capture & Authorize';
    btn.disabled = false;
  }
});

$('cancelFaceBtn').addEventListener('click', () => cancelFlow('Transfer cancelled.'));

// ── Step 2: PIN fallback (+ typing rhythm check) ──
function openPinStep(msg) {
  capture.reset();
  pinInput.value = '';
  hudEl.style.display = 'none';
  $('pinAlert').classList.remove('show');
  $('pinMsg').textContent = msg || 'Enter your Authorization PIN.';
  showModal('pinModal');
  setTimeout(() => pinInput.focus(), 350);
}

async function submitPin() {
  if (pinInput.value.length !== 6) { showAlert('pinAlert', 'PIN must be 6 digits.', 'error'); return; }
  $('pinBtnText').textContent = 'Verifying...';
  $('pinSpinner').style.display = 'inline-block';
  $('pinSubmitBtn').disabled = true;

  try {
    handleStep(await BioShieldAPI.paymentPin({
      event_id: pendingEventId,
      pin: pinInput.value,
      keystroke_data: capture.getData()
    }));
  } catch (err) {
    if (err.terminated) { cancelFlow(err.error); }
    else {
      showAlert('pinAlert', err.error || 'PIN verification failed.', 'error');
      capture.reset();
      pinInput.value = '';
      hudEl.style.display = 'none';
      pinInput.focus();
    }
  } finally {
    $('pinBtnText').textContent = 'Verify PIN';
    $('pinSpinner').style.display = 'none';
    $('pinSubmitBtn').disabled = false;
  }
}
$('pinSubmitBtn').addEventListener('click', submitPin);
pinInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submitPin(); } });
$('cancelPinBtn').addEventListener('click', () => cancelFlow('Transfer cancelled.'));

// ── Step 3: OTP to registered mobile (typing rhythm didn't match) ──
function openOtpStep(data) {
  document.querySelectorAll('.otp-digit').forEach(i => { i.value = ''; });
  $('otpAlert').classList.remove('show');

  const where = data.channel === 'SMS' && data.sent_to
    ? `Enter the code sent by SMS to ${data.sent_to}.`
    : data.channel === 'EMAIL'
      ? 'SMS is not configured on this server, so the code was emailed to you.'
      : 'SMS is not configured on this server; the code was written to the server log.';
  $('otpMsg').textContent = `Your typing pattern didn't match your profile. ${where}`;

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
    if (err.terminated) { cancelFlow(err.error); }
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
$('cancelOtpBtn').addEventListener('click', () => cancelFlow('Transfer cancelled.'));
