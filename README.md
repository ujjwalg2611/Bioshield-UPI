# 🛡 BioShield – Behavioral Biometric UPI System

Secure UPI payments using **keystroke dynamics**, real-time **risk scoring**, and fallback **OTP + Face ID** authentication.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the server
python app.py
# → http://localhost:5000

# Optional: PostgreSQL (default: SQLite)
export DATABASE_URL=postgresql://user:pass@localhost/bioshield
```

---

## Pages

| URL | Description |
|-----|-------------|
| `/` or `/login` | Login with keystroke analysis |
| `/signup` | Create account |
| `/enroll` | Capture 10 typing samples for baseline |
| `/test` | Test biometric recognition |
| `/payment` | UPI payment with risk engine |
| `/dashboard` | Security dashboard + transaction history |

---

## API Reference

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/signup` | POST | ✗ | Register new user |
| `/api/login` | POST | ✗ | Login + biometric check |
| `/api/enroll` | POST | ✓ | Submit keystroke samples |
| `/api/test` | POST | ✓ | Test recognition |
| `/api/payment` | POST | ✓ | Initiate UPI payment |
| `/api/otp-verify` | POST | ✓ | Verify OTP fallback |
| `/api/face-verify` | POST | ✓ | Mock Face ID fallback |
| `/api/risk-history` | GET | ✓ | Dashboard data |

---

## Risk Engine Logic

```
predict_risk(features, profile) → ALLOW | OTP_REQUIRED | BLOCK

Score = weighted Z-score of:
  • Dwell time deviation    (25%)
  • Flight time deviation   (30%)
  • Typing speed deviation  (25%)
  • Backspace rate delta    (10%)
  • Jitter anomaly          (10%)

score < 0.35  → ALLOW
score < 0.65  → OTP_REQUIRED
score ≥ 0.65  → BLOCK
```

---

## Keystroke Features Captured

| Feature | Description |
|---------|-------------|
| `dwell_time` | How long each key is held down (ms) |
| `flight_time` | Time between key-up and next key-down (ms) |
| `press_interval` | Time between consecutive key-down events (ms) |
| `backspace_count` | Number of corrections made |
| `typing_speed` | Characters per second |
| `jitter` | Standard deviation of flight times |

---

## File Structure

```
bioshield/
├── app.py              ← Flask server + all API routes + risk engine
├── models.py           ← SQLAlchemy models (User, KeystrokeProfile, RiskEvent, Transaction)
├── requirements.txt
├── static/
│   ├── keystroke.js    ← Biometric capture library (KeystrokeCapture, EnrollmentCollector, BiometricHUD)
│   └── style.css       ← Design system
└── templates/
    ├── login.html
    ├── signup.html
    ├── enroll.html
    ├── test.html
    ├── payment.html
    └── dashboard.html
```

---

## Self-Learning

After every successful payment, the profile auto-updates via **exponential moving average** (α=0.15):

```python
new_avg = 0.85 × old_avg + 0.15 × new_sample
```

This adapts to natural typing drift over time.

---

## Production Notes

- Replace SQLite with PostgreSQL via `DATABASE_URL` env var
- Remove demo OTP from payment response (search `demo only`)
- Replace mock Face ID in `/api/face-verify` with real ML (DeepFace / AWS Rekognition)
- Set a strong `SECRET_KEY` environment variable
- Add HTTPS, rate limiting, and Redis for OTP storage
