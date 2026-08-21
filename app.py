
import os
import re
import json
import math
import random
import string
import uuid
import base64
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
import jwt
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from cryptography.fernet import Fernet
from deepface import DeepFace

import blockchain
from models import db, User, KeystrokeProfile, RiskEvent, Transaction


app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app, supports_credentials=True)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'bioshield-secret-2024-change-in-prod')

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///bioshield.db')
# Render/Heroku-style providers hand out "postgres://" URLs, but SQLAlchemy
# 1.4+ requires the "postgresql://" scheme - rewrite it if needed.
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# --- SpikeGuard: merchant fraud-spike detector (AI Risk Manager track) ---
from spikeguard.routes import spikeguard_bp
app.register_blueprint(spikeguard_bp)

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"])

# DEMO_MODE=true is only for local testing without a real SMS/email provider
# wired up. In production this must be unset/false so OTPs are never returned
# in the API response - only sent through a real out-of-band channel.
DEMO_MODE = os.environ.get('DEMO_MODE', 'false').lower() == 'true'

otp_store = {}

FACE_STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__name__)), 'face_data')
os.makedirs(FACE_STORAGE_DIR, exist_ok=True)

# Face images are biometric data - encrypt at rest. FACE_ENCRYPTION_KEY must be
# a Fernet key (Fernet.generate_key()); generate one and store it in .env.
_enc_key = os.environ.get('FACE_ENCRYPTION_KEY')
if not _enc_key:
    # Dev fallback only - a key generated fresh each restart means old
    # enrolled faces become unreadable, so set FACE_ENCRYPTION_KEY in .env
    # for anything beyond a quick local test.
    _enc_key = Fernet.generate_key().decode()
    print("[WARN] FACE_ENCRYPTION_KEY not set - using an ephemeral key. "
          "Set FACE_ENCRYPTION_KEY in your .env for persistent enrollment.")
fernet = Fernet(_enc_key.encode() if isinstance(_enc_key, str) else _enc_key)


def save_base64_image(b64_string, file_path):
    """Decode base64 from frontend and write it to disk ENCRYPTED at rest."""
    if ',' in b64_string:
        b64_string = b64_string.split(',')[1]
    image_data = base64.b64decode(b64_string)
    encrypted = fernet.encrypt(image_data)
    with open(file_path, "wb") as fh:
        fh.write(encrypted)


def read_encrypted_image_to_temp(encrypted_path, temp_path):
    """Decrypt a stored face image to a temp plaintext file for DeepFace to
    read, since DeepFace needs a real image file/path. Caller must delete
    temp_path immediately after use."""
    with open(encrypted_path, "rb") as fh:
        encrypted = fh.read()
    plaintext = fernet.decrypt(encrypted)
    with open(temp_path, "wb") as fh:
        fh.write(plaintext)



def generate_token(user_id: int) -> str:
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'No token provided'}), 401
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            request.user_id = payload['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated



def extract_features(keystroke_data: dict) -> dict:
    dwell = keystroke_data.get('dwell_times', [])
    flight = keystroke_data.get('flight_times', [])
    press = keystroke_data.get('press_intervals', [])
    backspace_count = keystroke_data.get('backspace_count', 0)
    total_keys = max(keystroke_data.get('total_keys', 1), 1)
    duration_ms = max(keystroke_data.get('duration_ms', 1), 1)

    def safe_mean(lst):
        return sum(lst) / len(lst) if lst else 0.0

    def safe_std(lst):
        if len(lst) < 2:
            return 0.0
        m = safe_mean(lst)
        variance = sum((x - m) ** 2 for x in lst) / len(lst)
        return math.sqrt(variance)

    avg_dwell = safe_mean(dwell)
    avg_flight = safe_mean(flight)
    avg_press = safe_mean(press)
    typing_speed = (total_keys / duration_ms) * 1000   # chars/sec
    jitter = safe_std(flight)
    backspace_rate = backspace_count / total_keys

    return {
        'avg_dwell_time': avg_dwell,
        'avg_flight_time': avg_flight,
        'avg_press_interval': avg_press,
        'avg_typing_speed': typing_speed,
        'avg_jitter': jitter,
        'avg_backspace_rate': backspace_rate
    }


def predict_risk(features: dict, profile: KeystrokeProfile) -> dict:
    if profile is None or profile.sample_count < 3:
        return {
            'decision': 'ALLOW',
            'score': 0.1,
            'reason': 'Insufficient baseline – defaulting to ALLOW',
            'details': {}
        }

    def z_score(val, mean, std):
        if std < 1e-6:
            return 0.0
        return abs(val - mean) / std

    z_dwell  = z_score(features['avg_dwell_time'],  profile.avg_dwell_time,  max(profile.std_dwell, 10))
    z_flight = z_score(features['avg_flight_time'], profile.avg_flight_time, max(profile.std_flight, 10))
    z_speed  = z_score(features['avg_typing_speed'],profile.avg_typing_speed, max(profile.std_speed, 0.1))
    backspace_delta = abs(features['avg_backspace_rate'] - profile.avg_backspace_rate)
    
    jitter_ratio = (features['avg_jitter'] / max(profile.avg_jitter, 1)) if profile.avg_jitter > 0 else 1.0
    jitter_score = max(0, jitter_ratio - 1.5)

    raw_score = (
        z_dwell  * 0.25 +
        z_flight * 0.30 +
        z_speed  * 0.25 +
        backspace_delta * 2.0 * 0.10 +
        jitter_score * 0.10
    )
    score = min(raw_score / 5.0, 1.0) 

    details = {
        'z_dwell': round(z_dwell, 3),
        'z_flight': round(z_flight, 3),
        'z_speed': round(z_speed, 3),
        'backspace_delta': round(backspace_delta, 3),
        'jitter_score': round(jitter_score, 3)
    }

    if score < 0.35:
        decision = 'ALLOW'
    elif score < 0.65:
        decision = 'OTP_REQUIRED'
    else:
        decision = 'BLOCK'

    return {'decision': decision, 'score': score, 'details': details}


def update_profile_moving_average(profile: KeystrokeProfile, features: dict, alpha: float = 0.15):
    a = alpha
    profile.avg_dwell_time    = (1 - a) * profile.avg_dwell_time    + a * features['avg_dwell_time']
    profile.avg_flight_time   = (1 - a) * profile.avg_flight_time   + a * features['avg_flight_time']
    profile.avg_press_interval = (1-a) * profile.avg_press_interval  + a * features['avg_press_interval']
    profile.avg_typing_speed  = (1 - a) * profile.avg_typing_speed  + a * features['avg_typing_speed']
    profile.avg_jitter        = (1 - a) * profile.avg_jitter        + a * features['avg_jitter']
    profile.avg_backspace_rate = (1-a) * profile.avg_backspace_rate  + a * features['avg_backspace_rate']
    
    samples = profile.get_samples()
    samples.append(features)
    profile.set_samples(samples)
    
    if len(samples) >= 2:
        profile.std_dwell  = _std_from_samples(samples, 'avg_dwell_time')
        profile.std_flight = _std_from_samples(samples, 'avg_flight_time')
        profile.std_speed  = _std_from_samples(samples, 'avg_typing_speed')
    
    profile.updated_at = datetime.utcnow()


def _std_from_samples(samples, key):
    vals = [s.get(key, 0) for s in samples]
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def build_baseline_from_samples(samples: list) -> dict:
    def mean(key):
        vals = [s.get(key, 0) for s in samples]
        return sum(vals) / max(len(vals), 1)

    return {
        'avg_dwell_time':    mean('avg_dwell_time'),
        'avg_flight_time':   mean('avg_flight_time'),
        'avg_press_interval': mean('avg_press_interval'),
        'avg_typing_speed':  mean('avg_typing_speed'),
        'avg_jitter':        mean('avg_jitter'),
        'avg_backspace_rate': mean('avg_backspace_rate'),
        'std_dwell':   _std_from_samples(samples, 'avg_dwell_time'),
        'std_flight':  _std_from_samples(samples, 'avg_flight_time'),
        'std_speed':   _std_from_samples(samples, 'avg_typing_speed'),
    }


def generate_otp(user_id: int) -> str:
    otp = ''.join(random.choices(string.digits, k=6))
    otp_store[user_id] = {
        'otp': otp,
        'expires_at': datetime.utcnow() + timedelta(minutes=5)
    }
    return otp


def send_otp_email(user, otp):
    """Send the OTP via real email if SMTP is configured; otherwise just log
    it server-side (simulated send) so local dev keeps working without any
    email provider set up."""
    smtp_host = os.environ.get('SMTP_HOST')
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')

    if not (smtp_host and smtp_user and smtp_password):
        print(f"[OTP] (simulated send - SMTP not configured) OTP for {user.email}: {otp}")
        return

    try:
        import smtplib
        from email.mime.text import MIMEText

        smtp_port = int(os.environ.get('SMTP_PORT', '587'))
        msg = MIMEText(
            f"Your BioShield-UPI verification code is: {otp}\n\n"
            f"This code expires in 5 minutes. If you didn't request this, "
            f"someone may be trying to access your account."
        )
        msg['Subject'] = 'Your BioShield-UPI verification code'
        msg['From'] = smtp_user
        msg['To'] = user.email

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [user.email], msg.as_string())
        print(f"[OTP] Email sent to {user.email}")
    except Exception as e:
        # Never let a flaky email provider break the payment flow - fall back
        # to the console log so the user (in dev) can still see/use the OTP.
        print(f"[OTP] Email send failed ({e}) - falling back to console log. OTP: {otp}")


def verify_otp(user_id: int, otp: str) -> bool:
    record = otp_store.get(user_id)
    if not record:
        return False
    if datetime.utcnow() > record['expires_at']:
        del otp_store[user_id]
        return False
    if record['otp'] != otp:
        return False
    del otp_store[user_id]
    return True



def notarize_transaction(txn: Transaction):
    """Kick off async on-chain notarization for a just-completed transaction.
    Safe no-op if blockchain isn't configured (RPC_URL/PRIVATE_KEY/CONTRACT_ADDRESS
    unset) - the payment itself already succeeded and isn't affected either way."""
    if not blockchain.is_enabled():
        txn.chain_status = 'SKIPPED'
        db.session.commit()
        return

    timestamp = txn.created_at.isoformat()

    def on_done(success, tx_hash, block_number, error):
        with app.app_context():
            row = db.session.get(Transaction, txn.id)
            if not row:
                return
            if success:
                row.chain_status = 'CONFIRMED'
                row.chain_tx_hash = tx_hash
                row.chain_block_number = block_number
            else:
                row.chain_status = 'FAILED'
            db.session.commit()

    blockchain.notarize_async(
        txn_id=txn.txn_id,
        user_id=txn.user_id,
        amount=txn.amount,
        recipient_upi=txn.recipient_upi,
        timestamp=timestamp,
        auth_method=txn.auth_method,
        risk_level=txn.risk_level or 'N/A',
        on_done=on_done,
    )


@app.route('/')
@app.route('/login')
def serve_login():
    return send_from_directory('templates', 'login.html')

@app.route('/signup')
def serve_signup():
    return send_from_directory('templates', 'signup.html')

@app.route('/enroll')
def serve_enroll():
    return send_from_directory('templates', 'enroll.html')

@app.route('/test')
def serve_test():
    return send_from_directory('templates', 'test.html')

@app.route('/payment')
def serve_payment():
    return send_from_directory('templates', 'payment.html')

@app.route('/dashboard')
def serve_dashboard():
    return send_from_directory('templates', 'dashboard.html')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)



@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    full_name = data.get('full_name', '').strip()
    
    if not all([email, password, full_name]):
        return jsonify({'error': 'All fields are required'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    upi_id = f"{full_name.split()[0].lower()}{random.randint(100,999)}@bioshield"

    user = User(email=email, password_hash=pw_hash, full_name=full_name, upi_id=upi_id)
    db.session.add(user)
    db.session.commit()

    token = generate_token(user.id)
    return jsonify({'token': token, 'user': user.to_dict()}), 201


@app.route('/api/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    keystroke_data = data.get('keystroke_data', {})

    user = User.query.filter_by(email=email).first()

    # Account lockout: after 5 failed attempts, lock for 15 minutes. Checked
    # before verifying the password so a locked account can't be brute-forced
    # further even with the correct password guessed mid-lockout.
    if user and user.locked_until and datetime.utcnow() < user.locked_until:
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
        return jsonify({
            'error': f'Account temporarily locked due to repeated failed logins. '
                     f'Try again in about {remaining} minute(s).'
        }), 423

    if not user or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= 5:
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                user.failed_login_attempts = 0
            db.session.commit()
        return jsonify({'error': 'Invalid email or password'}), 401

    # Successful password check - reset lockout counters
    user.failed_login_attempts = 0
    user.locked_until = None
    db.session.commit()

    token = generate_token(user.id)
    
    risk_result = {'decision': 'ALLOW', 'score': 0.0}
    if user.is_enrolled and keystroke_data:
        features = extract_features(keystroke_data)
        profile = KeystrokeProfile.query.filter_by(user_id=user.id).first()
        risk_result = predict_risk(features, profile)
        
        event = RiskEvent(
            user_id=user.id, event_type='LOGIN',
            risk_level=risk_result['decision'],
            risk_score=risk_result['score'],
            features_snapshot=json.dumps(features)
        )
        db.session.add(event)
        db.session.commit()

    return jsonify({
        'token': token,
        'user': user.to_dict(),
        'risk': risk_result
    })



@app.route('/api/enroll', methods=['POST'])
@require_auth
def enroll():
    data = request.get_json()
    samples_raw = data.get('samples', [])
    
    if len(samples_raw) < 5:
        return jsonify({'error': 'Minimum 5 samples required for enrollment'}), 400

    feature_list = [extract_features(s) for s in samples_raw]
    baseline = build_baseline_from_samples(feature_list)

    profile = KeystrokeProfile.query.filter_by(user_id=request.user_id).first()
    if not profile:
        profile = KeystrokeProfile(user_id=request.user_id)
        db.session.add(profile)

    profile.avg_dwell_time     = baseline['avg_dwell_time']
    profile.avg_flight_time    = baseline['avg_flight_time']
    profile.avg_press_interval = baseline['avg_press_interval']
    profile.avg_typing_speed   = baseline['avg_typing_speed']
    profile.avg_jitter         = baseline['avg_jitter']
    profile.avg_backspace_rate = baseline['avg_backspace_rate']
    profile.std_dwell          = baseline['std_dwell']
    profile.std_flight         = baseline['std_flight']
    profile.std_speed          = baseline['std_speed']
    profile.sample_count       = len(feature_list)
    profile.set_samples(feature_list)
    profile.updated_at         = datetime.utcnow()

    user = db.session.get(User, request.user_id)
    user.is_enrolled = True
    db.session.commit()

    return jsonify({
        'message': 'Enrollment successful',
        'profile': profile.to_dict()
    })


@app.route('/api/enroll-face', methods=['POST'])
@require_auth
def enroll_face():
    data = request.get_json()
    face_image_b64 = data.get('face_image', '')

    if not face_image_b64:
        return jsonify({'error': 'No image provided'}), 400

    file_path = os.path.join(FACE_STORAGE_DIR, f"user_{request.user_id}_ref.enc")
    save_base64_image(face_image_b64, file_path)

    return jsonify({'message': 'Facial DNA enrolled successfully'})



@app.route('/api/test', methods=['POST'])
@require_auth
def test_recognition():
    data = request.get_json()
    keystroke_data = data.get('keystroke_data', {})

    profile = KeystrokeProfile.query.filter_by(user_id=request.user_id).first()
    if not profile or profile.sample_count < 3:
        return jsonify({'result': 'Unknown', 'reason': 'No baseline profile'}), 200

    features = extract_features(keystroke_data)
    risk = predict_risk(features, profile)

    recognized = risk['score'] < 0.45
    return jsonify({
        'result': 'Recognized' if recognized else 'Unrecognized',
        'risk_score': round(risk['score'], 3),
        'decision': risk['decision'],
        'features': features,
        'details': risk.get('details', {})
    })



@app.route('/api/payment', methods=['POST'])
@require_auth
@limiter.limit("20 per minute")
def payment():
    data = request.get_json()
    recipient_upi = data.get('recipient_upi', '').strip()
    amount = float(data.get('amount', 0))
    keystroke_data = data.get('keystroke_data', {})

    if not recipient_upi or amount <= 0:
        return jsonify({'error': 'Invalid payment details'}), 400
    if not re.match(r'^[\w.+-]{2,256}@[a-zA-Z]{2,64}$', recipient_upi):
        return jsonify({'error': 'Invalid UPI ID format'}), 400
    if amount > 100000:
        return jsonify({'error': 'Amount exceeds maximum transaction limit of ₹100,000'}), 400

    user = db.session.get(User, request.user_id)
    if user.balance < amount:
        return jsonify({'error': 'Insufficient balance'}), 400

    features = extract_features(keystroke_data)
    profile = KeystrokeProfile.query.filter_by(user_id=request.user_id).first()
    risk = predict_risk(features, profile)

    event = RiskEvent(
        user_id=request.user_id, event_type='PAYMENT',
        risk_level=risk['decision'], risk_score=risk['score'],
        features_snapshot=json.dumps(features),
        amount=amount, recipient=recipient_upi
    )
    db.session.add(event)
    db.session.commit()

    if risk['decision'] == 'BLOCK':
        event.resolution = 'BLOCKED'
        db.session.commit()
        return jsonify({
            'status': 'BLOCKED',
            'message': 'Transaction blocked due to suspicious behaviour',
            'risk_score': round(risk['score'], 3),
            'event_id': event.id
        }), 403

    if risk['decision'] == 'OTP_REQUIRED':
        otp = generate_otp(request.user_id)
        # SECURITY: the OTP itself must never be returned to the client - that
        # defeats the entire point of a second factor. If SMTP_* env vars are
        # set it's actually emailed to the user; otherwise it's logged
        # server-side as a simulated send. DEMO_MODE surfaces it in the
        # response ONLY for local testing without any email provider set up.
        send_otp_email(user, otp)
        response = {
            'status': 'OTP_REQUIRED',
            'message': 'OTP sent to your registered mobile number',
            'risk_score': round(risk['score'], 3),
            'event_id': event.id
        }
        if DEMO_MODE:
            response['otp'] = otp
            response['_demo_mode_warning'] = 'OTP included because DEMO_MODE=true - disable in production'
        return jsonify(response), 200

    txn_id = 'TXN' + uuid.uuid4().hex[:12].upper()
    txn = Transaction(
        user_id=request.user_id, recipient_upi=recipient_upi,
        amount=amount, status='SUCCESS',
        risk_level=risk['decision'], auth_method='BIOMETRIC',
        txn_id=txn_id
    )
    user.balance -= amount
    event.resolution = 'PASSED_BIOMETRIC'
    db.session.add(txn)
    db.session.commit()

    if profile:
        update_profile_moving_average(profile, features)
        db.session.commit()

    notarize_transaction(txn)

    return jsonify({
        'status': 'SUCCESS',
        'txn_id': txn_id,
        'amount': amount,
        'recipient': recipient_upi,
        'new_balance': user.balance,
        'risk_score': round(risk['score'], 3),
        'auth_method': 'BIOMETRIC',
        'chain_status': 'PENDING' if blockchain.is_enabled() else 'SKIPPED'
    })



@app.route('/api/otp-verify', methods=['POST'])
@require_auth
def otp_verify():
    data = request.get_json()
    otp = data.get('otp', '')
    event_id = data.get('event_id')
    amount = float(data.get('amount', 0))
    recipient_upi = data.get('recipient_upi', '')

    if not verify_otp(request.user_id, otp):
        return jsonify({'error': 'Invalid or expired OTP'}), 400


    user = db.session.get(User, request.user_id)
    if user.balance < amount:
        return jsonify({'error': 'Insufficient balance'}), 400

    txn_id = 'TXN' + uuid.uuid4().hex[:12].upper()
    txn = Transaction(
        user_id=request.user_id, recipient_upi=recipient_upi,
        amount=amount, status='SUCCESS',
        risk_level='OTP_VERIFIED', auth_method='OTP',
        txn_id=txn_id
    )
    user.balance -= amount

    if event_id:

        event = db.session.get(RiskEvent, event_id)
        if event:
            event.resolution = 'PASSED_OTP'

    db.session.add(txn)
    db.session.commit()

    notarize_transaction(txn)

    return jsonify({
        'status': 'SUCCESS',
        'txn_id': txn_id,
        'amount': amount,
        'recipient': recipient_upi,
        'new_balance': user.balance,
        'auth_method': 'OTP',
        'chain_status': 'PENDING' if blockchain.is_enabled() else 'SKIPPED'
    })



@app.route('/api/face-verify', methods=['POST'])
@require_auth
def face_verify():
    """Real Face ID verification using DeepFace"""
    data = request.get_json()
    live_face_b64 = data.get('face_image', '')   
    event_id = data.get('event_id')
    amount = float(data.get('amount', 0))
    recipient_upi = data.get('recipient_upi', '')


    # live_path_enc holds the encrypted-at-rest copy; live_plain is the
    # decrypted temp copy DeepFace actually reads. Both get deleted below.
    live_path_enc = os.path.join(FACE_STORAGE_DIR, f"temp_{request.user_id}_live.enc")
    live_plain = os.path.join(FACE_STORAGE_DIR, f"temp_{request.user_id}_live_plain.jpg")
    ref_path_enc = os.path.join(FACE_STORAGE_DIR, f"user_{request.user_id}_ref.enc")
    ref_plain = os.path.join(FACE_STORAGE_DIR, f"temp_{request.user_id}_ref_plain.jpg")

    save_base64_image(live_face_b64, live_path_enc)

    if not os.path.exists(ref_path_enc):
        for p in (live_path_enc, live_plain):
            if os.path.exists(p):
                os.remove(p)
        return jsonify({'error': 'No reference face found. Please enroll your face first.'}), 400

    try:
        read_encrypted_image_to_temp(live_path_enc, live_plain)
        read_encrypted_image_to_temp(ref_path_enc, ref_plain)

        result = DeepFace.verify(
            img1_path=ref_plain, 
            img2_path=live_plain, 
            model_name="VGG-Face",
            enforce_detection=False 
        )
        face_matched = result["verified"]
        confidence = 1.0 - result["distance"] 

    except Exception as e:
        print("DeepFace Error:", e)
        return jsonify({'error': 'Face ML processing failed'}), 500
    finally:
        # Always scrub every plaintext/temp face file, matched or not.
        for p in (live_path_enc, live_plain, ref_plain):
            if os.path.exists(p):
                os.remove(p)

    if not face_matched:
        return jsonify({'error': 'Face verification failed. Identity mismatch.', 'confidence': confidence}), 403

    user = db.session.get(User, request.user_id)
    if user.balance < amount:
        return jsonify({'error': 'Insufficient balance'}), 400

    txn_id = 'TXN' + uuid.uuid4().hex[:12].upper()
    txn = Transaction(
        user_id=request.user_id, recipient_upi=recipient_upi,
        amount=amount, status='SUCCESS',
        risk_level='FACE_VERIFIED', auth_method='FACE_ID',
        txn_id=txn_id
    )
    user.balance -= amount

    if event_id:
        event = db.session.get(RiskEvent, event_id)
        if event:
            event.resolution = 'PASSED_FACE'

    db.session.add(txn)
    db.session.commit()

    notarize_transaction(txn)

    return jsonify({
        'status': 'SUCCESS',
        'txn_id': txn_id,
        'amount': amount,
        'recipient': recipient_upi,
        'new_balance': user.balance,
        'confidence': round(confidence, 3),
        'auth_method': 'FACE_ID',
        'chain_status': 'PENDING' if blockchain.is_enabled() else 'SKIPPED'
    })




@app.route('/api/risk-history', methods=['GET'])
@require_auth
def risk_history():
    events = RiskEvent.query.filter_by(user_id=request.user_id)\
        .order_by(RiskEvent.created_at.desc()).limit(50).all()
    transactions = Transaction.query.filter_by(user_id=request.user_id)\
        .order_by(Transaction.created_at.desc()).limit(20).all()
    

    user = db.session.get(User, request.user_id)
    profile = KeystrokeProfile.query.filter_by(user_id=request.user_id).first()

    return jsonify({
        'user': user.to_dict(),
        'profile': profile.to_dict() if profile else None,
        'risk_events': [e.to_dict() for e in events],
        'transactions': [t.to_dict() for t in transactions]
    })



@app.route('/api/verify-chain/<txn_id>', methods=['GET'])
@require_auth
def verify_chain(txn_id):
    txn = Transaction.query.filter_by(txn_id=txn_id, user_id=request.user_id).first()
    if not txn:
        return jsonify({'error': 'Transaction not found'}), 404

    if txn.chain_status != 'CONFIRMED':
        return jsonify({
            'txn_id': txn_id,
            'chain_status': txn.chain_status,
            'message': 'Not yet notarized on-chain, or notarization was skipped/failed'
        })

    try:
        matches = blockchain.verify_on_chain(
            txn.txn_id, txn.user_id, txn.amount, txn.recipient_upi, txn.created_at.isoformat()
        )
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    return jsonify({
        'txn_id': txn_id,
        'chain_status': txn.chain_status,
        'chain_tx_hash': txn.chain_tx_hash,
        'chain_block_number': txn.chain_block_number,
        'integrity_verified': matches,
        'message': 'Record matches on-chain hash - untampered' if matches
                   else 'MISMATCH: this record does not match the on-chain hash'
    })


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'BioShield'})


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, port=5000)