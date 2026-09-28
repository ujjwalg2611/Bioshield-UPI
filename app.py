
import os
import re
import json
import math
import hmac
import random
import secrets
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

import blockchain
from models import db, User, KeystrokeProfile, RiskEvent, Transaction


app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app, supports_credentials=True)

_secret_key = os.environ.get('SECRET_KEY')
_is_production = os.environ.get('RENDER') is not None or os.environ.get('FLASK_ENV') == 'production'
if not _secret_key:
    if _is_production:
        raise RuntimeError("SECRET_KEY environment variable is required in production and is not set.")
    _secret_key = 'bioshield-secret-2024-change-in-prod'
    print("[WARN] SECRET_KEY not set - using an insecure dev default. Set SECRET_KEY before deploying.")
app.config['SECRET_KEY'] = _secret_key

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///bioshield.db')
# Render/Heroku-style providers hand out "postgres://" URLs, but SQLAlchemy
# 1.4+ requires the "postgresql://" scheme - rewrite it if needed.
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"])

# DEMO_MODE=true is only for local testing without a real SMS/email provider
# wired up. In production this must be unset/false so OTPs are never returned
# in the API response - only sent through a real out-of-band channel.
DEMO_MODE = os.environ.get('DEMO_MODE', 'false').lower() == 'true'


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


def generate_unique_pin() -> str:
    """6-digit payment Authorization PIN using a CSPRNG (not `random`, which
    is predictable). 'Unique' here means independently, randomly assigned per
    account with enough entropy (1M possibilities, bcrypt-hashed, rate-limited
    at verification) that guessing/collision isn't practically checkable
    against other users' PINs once hashed - the same trust model as the login
    passphrase."""
    return ''.join(secrets.choice(string.digits) for _ in range(6))


PIN_MAX_ATTEMPTS = 3
OTP_MAX_ATTEMPTS = 3
OTP_TTL = timedelta(minutes=5)
PAYMENT_TTL = timedelta(minutes=10)


def normalize_phone(raw):
    """10-digit Indian numbers get +91; otherwise require E.164 (+, 10-15 digits)."""
    digits = re.sub(r'[\s\-()]', '', raw or '')
    if re.fullmatch(r'[6-9]\d{9}', digits):
        return '+91' + digits
    if re.fullmatch(r'\+\d{10,15}', digits):
        return digits
    return None


def mask_phone(phone):
    return phone[:3] + '*' * (len(phone) - 7) + phone[-4:]


def get_pending_event(user_id: int, event_id, stage: str):
    """Row-locked fetch of the caller's own unresolved payment, only if it is
    currently at `stage`. Amount/recipient always come from this stored event
    (never the client), so a step-up endpoint can only finish a payment that
    /api/payment/initiate created, and each payment resolves exactly once."""
    try:
        event_id = int(event_id)
    except (TypeError, ValueError):
        return None
    event = (db.session.query(RiskEvent).filter_by(id=event_id)
             .with_for_update().first())
    if (not event or event.user_id != user_id or event.event_type != 'PAYMENT'
            or event.resolution is not None or event.stage != stage):
        return None
    if event.created_at < datetime.utcnow() - PAYMENT_TTL:
        event.resolution = 'EXPIRED'
        db.session.commit()
        return None
    return event


def otp_digest(event_id: int, otp: str) -> str:
    return hmac.new(app.config['SECRET_KEY'].encode(),
                    f"{event_id}:{otp}".encode(), 'sha256').hexdigest()


def send_otp(user, otp: str) -> str:
    """Deliver the OTP to the registered mobile via SMS (Twilio) when
    configured; otherwise fall back to email, then to a console log for local
    dev. Returns the channel actually used: SMS | EMAIL | LOG."""
    sid = os.environ.get('TWILIO_ACCOUNT_SID')
    token = os.environ.get('TWILIO_AUTH_TOKEN')
    sender = os.environ.get('TWILIO_FROM')
    if user.phone and sid and token and sender:
        try:
            import urllib.request, urllib.parse
            body = urllib.parse.urlencode({
                'To': user.phone, 'From': sender,
                'Body': f"Your BioShield-UPI payment code is {otp}. Valid for 5 minutes. Never share it."
            }).encode()
            req = urllib.request.Request(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=body)
            auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
            req.add_header('Authorization', f'Basic {auth}')
            urllib.request.urlopen(req, timeout=10).read()
            return 'SMS'
        except Exception as e:
            print(f"[OTP] SMS send failed ({e}) - trying email fallback")

    smtp_host = os.environ.get('SMTP_HOST')
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    if smtp_host and smtp_user and smtp_password:
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(f"Your BioShield-UPI verification code is: {otp}\n\n"
                           f"This code expires in 5 minutes. If you didn't request this, "
                           f"someone may be trying to use your account.")
            msg['Subject'] = 'Your BioShield-UPI verification code'
            msg['From'] = smtp_user
            msg['To'] = user.email
            with smtplib.SMTP(smtp_host, int(os.environ.get('SMTP_PORT', '587')), timeout=10) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, [user.email], msg.as_string())
            return 'EMAIL'
        except Exception as e:
            print(f"[OTP] Email send failed ({e})")

    print(f"[OTP] (simulated send - no SMS/SMTP configured) OTP for user {user.id}: {otp}")
    return 'LOG'


def finalize_payment(user_id: int, event, auth_method: str, resolution: str, risk_label: str):
    """Move the money and close the event. Returns (txn, user); (None, None)
    if the balance no longer covers it. Locks the user row against races."""
    user = db.session.query(User).filter_by(id=user_id).with_for_update().one()
    if user.balance < event.amount:
        event.resolution = 'FAILED_BALANCE'
        db.session.commit()
        return None, None
    txn = Transaction(
        user_id=user.id, recipient_upi=event.recipient, amount=event.amount,
        status='SUCCESS', risk_level=risk_label, auth_method=auth_method,
        txn_id='TXN' + uuid.uuid4().hex[:12].upper()
    )
    user.balance -= event.amount
    event.resolution = resolution
    event.stage = 'DONE'
    db.session.add(txn)
    db.session.commit()
    notarize_transaction(txn)
    return txn, user


def success_response(txn, user, auth_method, **extra):
    body = {
        'status': 'SUCCESS', 'txn_id': txn.txn_id, 'amount': txn.amount,
        'recipient': txn.recipient_upi, 'new_balance': user.balance,
        'auth_method': auth_method,
        'chain_status': 'PENDING' if blockchain.is_enabled() else 'SKIPPED'
    }
    body.update(extra)
    return jsonify(body)


def notarize_transaction(txn: Transaction):
    """Kick off async on-chain notarization for a just-completed transaction.
    Safe no-op if blockchain isn't configured (RPC_URL/PRIVATE_KEY/CONTRACT_ADDRESS
    unset) - the payment itself already succeeded and isn't affected either way."""
    if not blockchain.is_enabled():
        txn.chain_status = 'SKIPPED'
        db.session.commit()
        return

    txn.chain_status = 'PENDING'
    db.session.commit()

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
    phone = normalize_phone(data.get('phone', ''))

    if not all([email, password, full_name]):
        return jsonify({'error': 'All fields are required'}), 400
    if not phone:
        return jsonify({'error': 'Enter a valid mobile number (10 digits, or +country code)'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    upi_id = f"{full_name.split()[0].lower()}{random.randint(100,999)}@bioshield"

    # Every account gets its own unique 6-digit payment Authorization PIN,
    # generated server-side (never user-chosen, so it can't be a reused/weak
    # password). Only the bcrypt hash is persisted - the plaintext PIN is
    # returned to the client exactly once, in this response, and is
    # unrecoverable afterwards (matches how the passphrase itself is handled).
    raw_pin = generate_unique_pin()
    pin_hash = bcrypt.hashpw(raw_pin.encode(), bcrypt.gensalt()).decode()

    user = User(email=email, password_hash=pw_hash, pin_hash=pin_hash, phone=phone, full_name=full_name, upi_id=upi_id)
    db.session.add(user)
    db.session.commit()

    token = generate_token(user.id)
    return jsonify({
        'token': token,
        'user': user.to_dict(),
        'authorization_pin': raw_pin,
        '_pin_warning': 'Store this PIN now - it will not be shown again and is required for every payment.'
    }), 201


@app.route('/api/pin/setup', methods=['POST'])
@require_auth
@limiter.limit("5 per minute")
def pin_setup():
    """One-time issuance for accounts created before Authorization PINs
    existed (pin_hash is NULL). Does nothing for accounts that already have
    a PIN - use a dedicated reset/forgot-PIN flow for that, not this."""
    user = db.session.get(User, request.user_id)
    if user.pin_hash:
        return jsonify({'error': 'An Authorization PIN is already set on this account.'}), 409

    raw_pin = generate_unique_pin()
    user.pin_hash = bcrypt.hashpw(raw_pin.encode(), bcrypt.gensalt()).decode()
    db.session.commit()

    return jsonify({
        'authorization_pin': raw_pin,
        '_pin_warning': 'Store this PIN now - it will not be shown again and is required for every payment.'
    }), 201


@app.route('/api/phone/setup', methods=['POST'])
@require_auth
@limiter.limit("5 per minute")
def phone_setup():
    """For accounts created before mobile numbers were collected. Only works
    while no number is on file - changing an existing number needs a proper
    re-verification flow, otherwise a stolen session could redirect OTPs."""
    user = db.session.get(User, request.user_id)
    if user.phone:
        return jsonify({'error': 'A mobile number is already registered.'}), 409
    phone = normalize_phone((request.get_json() or {}).get('phone', ''))
    if not phone:
        return jsonify({'error': 'Enter a valid mobile number'}), 400
    user.phone = phone
    db.session.commit()
    return jsonify({'phone': mask_phone(phone)}), 201


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



@app.route('/api/payment/initiate', methods=['POST'])
@require_auth
@limiter.limit("20 per minute")
def payment_initiate():
    """Step 0: validate the transfer and open a pending payment. Nothing moves
    until the caller clears FACE (or PIN fallback) and, if typing rhythm
    mismatches, OTP."""
    data = request.get_json() or {}
    recipient_upi = str(data.get('recipient_upi', '')).strip()
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid payment details'}), 400

    if not recipient_upi or not math.isfinite(amount) or amount <= 0:
        return jsonify({'error': 'Invalid payment details'}), 400
    if not re.match(r'^[\w.+-]{2,256}@[a-zA-Z]{2,64}$', recipient_upi):
        return jsonify({'error': 'Invalid UPI ID format'}), 400
    if amount > 100000:
        return jsonify({'error': 'Amount exceeds maximum transaction limit of ₹100,000'}), 400

    user = db.session.get(User, request.user_id)
    if user.balance < amount:
        return jsonify({'error': 'Insufficient balance'}), 400

    # A new attempt cancels any older unfinished one.
    RiskEvent.query.filter_by(user_id=user.id, event_type='PAYMENT', resolution=None) \
        .update({'resolution': 'SUPERSEDED'})

    has_face = os.path.exists(os.path.join(FACE_STORAGE_DIR, f"user_{user.id}_ref.enc"))
    event = RiskEvent(
        user_id=user.id, event_type='PAYMENT', risk_level='PENDING', risk_score=0.0,
        amount=amount, recipient=recipient_upi,
        stage='FACE' if has_face else 'PIN', pin_attempts=0, otp_attempts=0
    )
    db.session.add(event)
    db.session.commit()

    if has_face:
        return jsonify({'status': 'FACE_REQUIRED', 'event_id': event.id,
                        'message': 'Look at the camera to verify your face.'})
    return jsonify({'status': 'PIN_REQUIRED', 'event_id': event.id,
                    'message': 'No enrolled face found. Enter your Authorization PIN.'})


@app.route('/api/payment/face', methods=['POST'])
@require_auth
@limiter.limit("5 per minute")
def payment_face():
    """Step 1: face match against the enrolled profile photo. Match -> paid.
    Genuine mismatch -> fall back to the Authorization PIN."""
    data = request.get_json() or {}
    event = get_pending_event(request.user_id, data.get('event_id'), 'FACE')
    if not event:
        return jsonify({'error': 'No pending payment to verify'}), 400

    live_path_enc = os.path.join(FACE_STORAGE_DIR, f"temp_{request.user_id}_live.enc")
    live_plain = os.path.join(FACE_STORAGE_DIR, f"temp_{request.user_id}_live_plain.jpg")
    ref_path_enc = os.path.join(FACE_STORAGE_DIR, f"user_{request.user_id}_ref.enc")
    ref_plain = os.path.join(FACE_STORAGE_DIR, f"temp_{request.user_id}_ref_plain.jpg")
    temp_files = (live_path_enc, live_plain, ref_plain)

    def scrub():
        for path in temp_files:
            if os.path.exists(path):
                os.remove(path)

    try:
        save_base64_image(data.get('face_image', ''), live_path_enc)
    except Exception:
        scrub()
        return jsonify({'error': 'Could not read the camera image. Try again.'}), 400

    if not os.path.exists(ref_path_enc):
        scrub()
        event.stage = 'PIN'
        db.session.commit()
        return jsonify({'status': 'PIN_REQUIRED', 'face_matched': False, 'event_id': event.id,
                        'message': 'No enrolled face found. Enter your Authorization PIN.'})

    try:
        read_encrypted_image_to_temp(live_path_enc, live_plain)
        read_encrypted_image_to_temp(ref_path_enc, ref_plain)
        from deepface import DeepFace
        result = DeepFace.verify(
            img1_path=ref_plain, img2_path=live_plain,
            model_name="VGG-Face", enforce_detection=True
        )
        face_matched = bool(result["verified"])
        confidence = 1.0 - result["distance"]
    except ValueError:
        return jsonify({'error': 'No face detected. Look at the camera and try again.'}), 400
    except Exception as e:
        print("DeepFace Error:", e)
        return jsonify({'error': 'Face ML processing failed'}), 500
    finally:
        scrub()

    if not face_matched:
        event.stage = 'PIN'
        db.session.commit()
        return jsonify({'status': 'PIN_REQUIRED', 'face_matched': False, 'event_id': event.id,
                        'message': 'Face did not match. Enter your Authorization PIN instead.'})

    event.risk_level = 'FACE_VERIFIED'
    txn, user = finalize_payment(request.user_id, event, 'FACE_ID', 'PASSED_FACE', 'FACE_VERIFIED')
    if not txn:
        return jsonify({'error': 'Insufficient balance'}), 400
    return success_response(txn, user, 'FACE_ID', confidence=round(confidence, 3))


@app.route('/api/payment/pin', methods=['POST'])
@require_auth
@limiter.limit("10 per minute")
def payment_pin():
    """Step 2 (fallback): Authorization PIN set at signup, plus typing-rhythm
    check on how the PIN was typed. Rhythm matches -> paid. Rhythm mismatch
    -> OTP to the registered mobile."""
    data = request.get_json() or {}
    event = get_pending_event(request.user_id, data.get('event_id'), 'PIN')
    if not event:
        return jsonify({'error': 'No pending payment to verify'}), 400

    user = db.session.get(User, request.user_id)
    if not user.pin_hash:
        return jsonify({'error': 'No Authorization PIN is set up on this account.'}), 400

    pin = str(data.get('pin', ''))
    if not pin or not bcrypt.checkpw(pin.encode(), user.pin_hash.encode()):
        event.pin_attempts = (event.pin_attempts or 0) + 1
        left = PIN_MAX_ATTEMPTS - event.pin_attempts
        if left <= 0:
            event.resolution = 'FAILED_PIN'
            db.session.commit()
            return jsonify({'error': 'Too many incorrect PIN attempts. Transaction cancelled.',
                            'terminated': True}), 403
        db.session.commit()
        return jsonify({'error': f'Incorrect Authorization PIN. {left} attempt(s) left.',
                        'attempts_left': left}), 401

    features = extract_features(data.get('keystroke_data', {}))
    profile = KeystrokeProfile.query.filter_by(user_id=user.id).first()
    risk = predict_risk(features, profile)
    event.risk_score = risk['score']
    event.features_snapshot = json.dumps(features)

    if risk['decision'] == 'ALLOW':
        event.risk_level = 'ALLOW'
        txn, user = finalize_payment(user.id, event, 'PIN_BIOMETRIC', 'PASSED_PIN', 'ALLOW')
        if not txn:
            return jsonify({'error': 'Insufficient balance'}), 400
        if profile:
            update_profile_moving_average(profile, features)
            db.session.commit()
        return success_response(txn, user, 'PIN_BIOMETRIC', risk_score=round(risk['score'], 3))

    # PIN correct but typing rhythm doesn't match -> second factor: OTP.
    otp = ''.join(secrets.choice(string.digits) for _ in range(6))
    event.risk_level = 'OTP_REQUIRED'
    event.stage = 'OTP'
    event.otp_hash = otp_digest(event.id, otp)
    event.otp_expires_at = datetime.utcnow() + OTP_TTL
    event.otp_attempts = 0
    db.session.commit()

    channel = send_otp(user, otp)
    response = {
        'status': 'OTP_REQUIRED', 'event_id': event.id,
        'risk_score': round(risk['score'], 3), 'channel': channel,
        'sent_to': mask_phone(user.phone) if (user.phone and channel == 'SMS') else None,
        'message': 'Typing pattern mismatch. Enter the one-time code sent to your registered mobile number.'
    }
    if DEMO_MODE:
        response['otp'] = otp
        response['_demo_mode_warning'] = 'OTP included because DEMO_MODE=true - disable in production'
    return jsonify(response)


@app.route('/api/payment/otp', methods=['POST'])
@require_auth
@limiter.limit("10 per minute")
def payment_otp():
    """Step 3: one-time code sent to the registered mobile."""
    data = request.get_json() or {}
    event = get_pending_event(request.user_id, data.get('event_id'), 'OTP')
    if not event:
        return jsonify({'error': 'No pending payment to verify'}), 400

    if not event.otp_hash or not event.otp_expires_at or datetime.utcnow() > event.otp_expires_at:
        event.resolution = 'EXPIRED'
        db.session.commit()
        return jsonify({'error': 'Code expired. Start the transfer again.', 'terminated': True}), 403

    otp = str(data.get('otp', ''))
    if not hmac.compare_digest(otp_digest(event.id, otp), event.otp_hash):
        event.otp_attempts = (event.otp_attempts or 0) + 1
        left = OTP_MAX_ATTEMPTS - event.otp_attempts
        if left <= 0:
            event.resolution = 'FAILED_OTP'
            db.session.commit()
            return jsonify({'error': 'Too many incorrect codes. Transaction cancelled.',
                            'terminated': True}), 403
        db.session.commit()
        return jsonify({'error': f'Incorrect code. {left} attempt(s) left.',
                        'attempts_left': left}), 401

    txn, user = finalize_payment(request.user_id, event, 'OTP', 'PASSED_OTP', 'OTP_VERIFIED')
    if not txn:
        return jsonify({'error': 'Insufficient balance'}), 400
    return success_response(txn, user, 'OTP')


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


def run_light_migrations():
    """db.create_all() never alters existing tables, so add any new columns
    ourselves. Idempotent; safe if two gunicorn workers race at startup."""
    from sqlalchemy import inspect, text
    wanted = {
        'users': {'pin_hash': 'VARCHAR(255)', 'phone': 'VARCHAR(20)'},
        'risk_events': {
            'stage': 'VARCHAR(10)', 'pin_attempts': 'INTEGER DEFAULT 0',
            'otp_hash': 'VARCHAR(64)', 'otp_expires_at': 'TIMESTAMP',
            'otp_attempts': 'INTEGER DEFAULT 0',
        },
    }
    insp = inspect(db.engine)
    for table, cols in wanted.items():
        if not insp.has_table(table):
            continue
        existing = {c['name'] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in existing:
                try:
                    db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                    db.session.commit()
                except Exception:
                    db.session.rollback()


with app.app_context():
    db.create_all()
    run_light_migrations()

if __name__ == '__main__':
    app.run(debug=True, port=5000)