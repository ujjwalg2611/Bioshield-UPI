import pytest
import app as appmod
from models import db, KeystrokeProfile


@pytest.fixture()
def client():
    appmod.app.config['TESTING'] = True
    appmod.limiter.enabled = False
    with appmod.app.app_context():
        db.drop_all(); db.create_all()
        yield appmod.app.test_client()


def _signup(c, email='a@b.com'):
    r = c.post('/api/signup', json={'email': email, 'password': 'pw123456',
                                    'full_name': 'Test User', 'phone': '9876543210'})
    assert r.status_code == 201
    return r.get_json()


def test_health(client):
    assert client.get('/api/health').status_code == 200


def test_signup_never_returns_hash_and_duplicate_rejected(client):
    body = _signup(client)
    assert 'password_hash' not in str(body)
    assert client.post('/api/signup', json={'email': 'a@b.com', 'password': 'x',
                                            'full_name': 'T U', 'phone': '9876543210'}).status_code == 409


def test_login_lockout_after_five_failures(client):
    _signup(client)
    for _ in range(5):
        assert client.post('/api/login', json={'email': 'a@b.com', 'password': 'bad'}).status_code == 401
    r = client.post('/api/login', json={'email': 'a@b.com', 'password': 'pw123456'})
    assert r.status_code == 423


def test_payment_requires_auth(client):
    assert client.post('/api/payment/initiate', json={}).status_code == 401


def test_decision_thresholds_are_shared():
    assert appmod.decision_for(0.0) == 'ALLOW'
    assert appmod.decision_for(appmod.ALLOW_THRESHOLD) == 'OTP_REQUIRED'
    assert appmod.decision_for(appmod.BLOCK_THRESHOLD) == 'BLOCK'


def test_predict_risk_without_baseline_allows():
    assert appmod.predict_risk({}, None)['decision'] == 'ALLOW'
