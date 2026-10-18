"""End-to-end tests of the PIN -> rhythm -> step-up payment state machine and the
security controls around it. Keystroke timings are synthetic: these tests check the
control flow and thresholds, not real-world biometric accuracy."""
import math
from datetime import datetime, timedelta

import numpy as np
import pytest

import app as appmod
from models import db, KeystrokeProfile, RiskEvent, User


@pytest.fixture()
def client():
    appmod.app.config['TESTING'] = True
    appmod.limiter.enabled = False
    with appmod.app.app_context():
        db.drop_all(); db.create_all()
        yield appmod.app.test_client()


def _session(rng, dwell=95, flight=140, press=235, keys=10, dur=1800, back=0):
    return {
        'dwell_times': list(rng.normal(dwell, 4, keys)),
        'flight_times': list(rng.normal(flight, 8, keys)),
        'press_intervals': list(rng.normal(press, 8, keys)),
        'backspace_count': back, 'total_keys': keys, 'duration_ms': dur,
    }


MATCH = lambda rng: _session(rng)
MISMATCH = lambda rng: _session(rng, dwell=210, flight=420, press=600, dur=6000, back=4)


def _user(c, rng, email='a@b.com', enroll=True):
    r = c.post('/api/signup', json={'email': email, 'password': 'pw123456',
                                    'full_name': 'Test User', 'phone': '9876543210'})
    assert r.status_code == 201
    body = r.get_json()
    h = {'Authorization': f"Bearer {body['token']}"}
    if enroll:
        samples = [_session(rng) for _ in range(10)]
        assert c.post('/api/enroll', json={'samples': samples}, headers=h).status_code == 200
    return h, body['authorization_pin'], body['user']['id']


def _initiate(c, h, amount=100, to='bob@bank'):
    r = c.post('/api/payment/initiate', json={'recipient_upi': to, 'amount': amount}, headers=h)
    return r, (r.get_json() or {}).get('event_id')


def _balance(uid):
    return db.session.get(User, uid).balance


# ---------- feature extraction & profile maths ----------

def test_extract_features_known_values():
    f = appmod.extract_features({'dwell_times': [100, 100, 100], 'flight_times': [50, 150],
                                 'press_intervals': [200, 300], 'backspace_count': 1,
                                 'total_keys': 10, 'duration_ms': 2000})
    assert f['avg_dwell_time'] == 100
    assert f['avg_flight_time'] == 100
    assert f['avg_press_interval'] == 250
    assert f['avg_typing_speed'] == pytest.approx(5.0)       # 10 keys / 2 s
    assert f['avg_jitter'] == pytest.approx(50.0)            # population std of [50, 150]
    assert f['avg_backspace_rate'] == pytest.approx(0.1)


def test_extract_features_handles_empty_input():
    f = appmod.extract_features({})
    assert all(math.isfinite(v) for v in f.values())


def test_ema_update_formula():
    p = KeystrokeProfile(user_id=1, avg_dwell_time=100.0, avg_flight_time=100.0, avg_press_interval=100.0,
                         avg_typing_speed=5.0, avg_jitter=10.0, avg_backspace_rate=0.0)
    feats = {'avg_dwell_time': 200.0, 'avg_flight_time': 100.0, 'avg_press_interval': 100.0,
             'avg_typing_speed': 5.0, 'avg_jitter': 10.0, 'avg_backspace_rate': 0.0}
    appmod.update_profile_moving_average(p, feats, alpha=0.15)
    assert p.avg_dwell_time == pytest.approx(0.85 * 100 + 0.15 * 200)


def test_zscore_engine_separates_match_from_mismatch(client):
    rng = np.random.default_rng(0)
    h, _, uid = _user(client, rng)
    profile = KeystrokeProfile.query.filter_by(user_id=uid).first()
    ok = appmod._predict_risk_zscore(appmod.extract_features(MATCH(rng)), profile)
    bad = appmod._predict_risk_zscore(appmod.extract_features(MISMATCH(rng)), profile)
    assert ok['decision'] == 'ALLOW'
    assert bad['decision'] != 'ALLOW' and bad['score'] > ok['score']


# ---------- payment happy path & step-up ----------

def test_matching_rhythm_pays_without_step_up(client):
    rng = np.random.default_rng(1)
    h, pin, uid = _user(client, rng)
    r, eid = _initiate(client, h, 250)
    assert r.get_json()['status'] == 'PIN_REQUIRED'
    r = client.post('/api/payment/pin', headers=h,
                    json={'event_id': eid, 'pin': pin, 'keystroke_data': MATCH(rng)})
    body = r.get_json()
    assert r.status_code == 200 and body['status'] == 'SUCCESS'
    assert body['auth_method'] == 'PIN_BIOMETRIC' and body['new_balance'] == 9750
    assert _balance(uid) == 9750


def test_rhythm_mismatch_requires_step_up_then_otp_pays(client, monkeypatch):
    monkeypatch.setattr(appmod, 'DEMO_MODE', True)
    rng = np.random.default_rng(2)
    h, pin, uid = _user(client, rng)
    _, eid = _initiate(client, h, 500)
    r = client.post('/api/payment/pin', headers=h,
                    json={'event_id': eid, 'pin': pin, 'keystroke_data': MISMATCH(rng)})
    body = r.get_json()
    assert body['status'] == 'STEPUP_REQUIRED' and 'OTP' in body['options']
    assert _balance(uid) == 10000                       # nothing moved yet
    otp = client.post('/api/payment/otp/send', headers=h, json={'event_id': eid}).get_json()['otp']
    r = client.post('/api/payment/otp', headers=h, json={'event_id': eid, 'otp': otp})
    assert r.get_json()['status'] == 'SUCCESS' and r.get_json()['auth_method'] == 'OTP'
    assert _balance(uid) == 9500


def test_wrong_otp_three_times_cancels_payment(client, monkeypatch):
    monkeypatch.setattr(appmod, 'DEMO_MODE', True)
    rng = np.random.default_rng(3)
    h, pin, uid = _user(client, rng)
    _, eid = _initiate(client, h)
    client.post('/api/payment/pin', headers=h, json={'event_id': eid, 'pin': pin, 'keystroke_data': MISMATCH(rng)})
    real = client.post('/api/payment/otp/send', headers=h, json={'event_id': eid}).get_json()['otp']
    wrong = '000000' if real != '000000' else '111111'
    codes = [client.post('/api/payment/otp', headers=h, json={'event_id': eid, 'otp': wrong}).status_code
             for _ in range(3)]
    assert codes == [401, 401, 403]
    # even the right code is now refused, and no money moved
    assert client.post('/api/payment/otp', headers=h, json={'event_id': eid, 'otp': real}).status_code == 400
    assert _balance(uid) == 10000


def test_otp_resend_does_not_reset_attempt_budget(client, monkeypatch):
    monkeypatch.setattr(appmod, 'DEMO_MODE', True)
    rng = np.random.default_rng(4)
    h, pin, uid = _user(client, rng)
    _, eid = _initiate(client, h)
    client.post('/api/payment/pin', headers=h, json={'event_id': eid, 'pin': pin, 'keystroke_data': MISMATCH(rng)})
    client.post('/api/payment/otp/send', headers=h, json={'event_id': eid})
    for _ in range(2):
        client.post('/api/payment/otp', headers=h, json={'event_id': eid, 'otp': '000000'})
    client.post('/api/payment/otp/send', headers=h, json={'event_id': eid})       # resend
    r = client.post('/api/payment/otp', headers=h, json={'event_id': eid, 'otp': '000000'})
    assert r.status_code == 403                          # third miss still cancels


# ---------- state machine integrity ----------

def test_step_up_endpoints_cannot_skip_the_pin_stage(client):
    rng = np.random.default_rng(5)
    h, pin, _ = _user(client, rng)
    _, eid = _initiate(client, h)
    assert client.post('/api/payment/otp', headers=h, json={'event_id': eid, 'otp': '123456'}).status_code == 400
    assert client.post('/api/payment/otp/send', headers=h, json={'event_id': eid}).status_code == 400
    assert client.post('/api/payment/face', headers=h, json={'event_id': eid, 'face_image': 'x'}).status_code == 400


def test_payment_resolves_only_once(client):
    rng = np.random.default_rng(6)
    h, pin, uid = _user(client, rng)
    _, eid = _initiate(client, h)
    body = {'event_id': eid, 'pin': pin, 'keystroke_data': MATCH(rng)}
    assert client.post('/api/payment/pin', headers=h, json=body).status_code == 200
    assert client.post('/api/payment/pin', headers=h, json=body).status_code == 400   # replay
    assert _balance(uid) == 9900


def test_user_cannot_complete_someone_elses_payment(client):
    rng = np.random.default_rng(7)
    h1, pin1, _ = _user(client, rng, 'one@x.com')
    h2, _, uid2 = _user(client, rng, 'two@x.com')
    _, eid = _initiate(client, h1)
    r = client.post('/api/payment/pin', headers=h2,
                    json={'event_id': eid, 'pin': pin1, 'keystroke_data': MATCH(rng)})
    assert r.status_code == 400 and _balance(uid2) == 10000


def test_new_attempt_supersedes_old_pending_payment(client):
    rng = np.random.default_rng(8)
    h, pin, _ = _user(client, rng)
    _, old = _initiate(client, h, 100)
    _, new = _initiate(client, h, 200)
    r = client.post('/api/payment/pin', headers=h,
                    json={'event_id': old, 'pin': pin, 'keystroke_data': MATCH(rng)})
    assert r.status_code == 400
    assert db.session.get(RiskEvent, old).resolution == 'SUPERSEDED'


def test_expired_payment_is_rejected(client):
    rng = np.random.default_rng(9)
    h, pin, uid = _user(client, rng)
    _, eid = _initiate(client, h)
    ev = db.session.get(RiskEvent, eid)
    ev.created_at = datetime.utcnow() - appmod.PAYMENT_TTL - timedelta(minutes=1)
    db.session.commit()
    r = client.post('/api/payment/pin', headers=h, json={'event_id': eid, 'pin': pin, 'keystroke_data': MATCH(rng)})
    assert r.status_code == 400 and db.session.get(RiskEvent, eid).resolution == 'EXPIRED'
    assert _balance(uid) == 10000


# ---------- PIN brute-force controls ----------

def test_pin_attempt_limits_and_account_lockout(client):
    rng = np.random.default_rng(10)
    h, pin, uid = _user(client, rng)
    bad = '000000' if pin != '000000' else '111111'
    _, e1 = _initiate(client, h)
    codes = [client.post('/api/payment/pin', headers=h, json={'event_id': e1, 'pin': bad}).status_code
             for _ in range(3)]
    assert codes == [401, 401, 403]                        # 3 misses cancel this payment
    _, e2 = _initiate(client, h)
    codes = [client.post('/api/payment/pin', headers=h, json={'event_id': e2, 'pin': bad}).status_code
             for _ in range(2)]
    assert codes == [401, 403]                             # 5th wrong PIN account-wide locks
    _, e3 = _initiate(client, h)
    r = client.post('/api/payment/pin', headers=h,
                    json={'event_id': e3, 'pin': pin, 'keystroke_data': MATCH(rng)})
    assert r.status_code == 429                            # correct PIN refused while locked
    assert _balance(uid) == 10000


# ---------- input validation ----------

@pytest.mark.parametrize('amount,to', [(0, 'bob@bank'), (-5, 'bob@bank'), ('abc', 'bob@bank'),
                                       (float('nan'), 'bob@bank'), (100001, 'bob@bank'),
                                       (10, 'not-a-upi-id'), (10, '')])
def test_initiate_rejects_bad_input(client, amount, to):
    rng = np.random.default_rng(11)
    h, _, _ = _user(client, rng)
    r = client.post('/api/payment/initiate', json={'recipient_upi': to, 'amount': amount}, headers=h)
    assert r.status_code == 400


def test_insufficient_balance_rejected(client):
    rng = np.random.default_rng(12)
    h, _, _ = _user(client, rng)
    r, _ = _initiate(client, h, 50000)
    assert r.status_code == 400


# ---------- cold start / enumeration / CORS ----------

def test_unenrolled_user_is_stepped_up_not_waved_through(client, monkeypatch):
    rng = np.random.default_rng(13)
    h, pin, uid = _user(client, rng, enroll=False)
    _, eid = _initiate(client, h)
    r = client.post('/api/payment/pin', headers=h, json={'event_id': eid, 'pin': pin, 'keystroke_data': MATCH(rng)})
    assert r.get_json()['status'] == 'STEPUP_REQUIRED' and _balance(uid) == 10000
    monkeypatch.setattr(appmod, 'COLD_START_POLICY', 'allow')
    _, eid = _initiate(client, h)
    r = client.post('/api/payment/pin', headers=h, json={'event_id': eid, 'pin': pin, 'keystroke_data': MATCH(rng)})
    assert r.get_json()['status'] == 'SUCCESS'


def test_login_error_is_identical_for_unknown_email_and_wrong_password(client):
    rng = np.random.default_rng(14)
    _user(client, rng)
    a = client.post('/api/login', json={'email': 'a@b.com', 'password': 'wrong'})
    b = client.post('/api/login', json={'email': 'nobody@b.com', 'password': 'wrong'})
    assert (a.status_code, a.get_json()) == (b.status_code, b.get_json())


def test_empty_bodies_do_not_crash(client):
    assert client.post('/api/login').status_code in (400, 401)
    assert client.post('/api/signup').status_code == 400


def test_no_cors_headers_for_foreign_origin(client):
    r = client.get('/api/health', headers={'Origin': 'https://evil.example'})
    assert 'Access-Control-Allow-Origin' not in r.headers


def test_tampered_or_missing_token_rejected(client):
    assert client.post('/api/payment/initiate', json={}, headers={'Authorization': 'Bearer junk'}).status_code == 401
