# 🛡 BioShield – Behavioral Biometric UPI System

Secure UPI payments using **keystroke dynamics**, real-time **risk scoring**, blockchain **transaction notarization**, and fallback **OTP + Face ID** authentication.

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

See `.env.example` for the full list of optional environment variables (blockchain notarization, SMTP for OTP email, face-data encryption key).

---

## Pages

| URL | Description |
|-----|-------------|
| `/` or `/login` | Login with keystroke analysis |
| `/signup` | Create account |
| `/enroll` | Capture 10 typing samples for baseline |
| `/test` | Test biometric recognition |
| `/payment` | UPI payment with risk engine + on-chain notarization badge |
| `/dashboard` | Security dashboard + transaction history |

---

## API Reference

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/signup` | POST | ✗ | Register new user |
| `/api/login` | POST | ✗ | Login + biometric check |
| `/api/enroll` | POST | ✓ | Submit keystroke samples |
| `/api/test` | POST | ✓ | Test recognition |
| `/api/payment/initiate` | POST | ✓ | Validate transfer, open a pending payment (stage FACE, or PIN if no face enrolled) |
| `/api/payment/face` | POST | ✓ | Step 1: DeepFace match to enrolled photo. Match pays; mismatch moves to PIN |
| `/api/payment/pin` | POST | ✓ | Step 2: Authorization PIN + typing-rhythm check. Rhythm ok pays; mismatch sends OTP |
| `/api/payment/otp` | POST | ✓ | Step 3: OTP sent to the registered mobile number |
| `/api/pin/setup` | POST | ✓ | One-time PIN issue for accounts created before PINs existed |
| `/api/phone/setup` | POST | ✓ | Add a mobile number to an older account (only while none is on file) |
| `/api/risk-history` | GET | ✓ | Dashboard data |
| `/api/verify-chain/<txn_id>` | GET | ✓ | Recompute + compare on-chain hash to prove a record wasn't tampered with |

---

## Payment Authentication Flow

```
Face match ──ok──────────────────────────────► paid
   │ mismatch
   ▼
Authorization PIN (set at signup) ──wrong ×3──► cancelled
   │ correct
   ├─ typing rhythm matches ────────────────► paid
   └─ typing rhythm mismatch
        ▼
     OTP to registered mobile ──wrong ×3────► cancelled
        │ correct
        ▼
      paid
```

The server keeps the current step on the payment record and each endpoint only accepts a payment at its own step, so a client cannot skip ahead. Amount and recipient are read from the stored record, and each payment resolves once. OTP delivery: SMS via Twilio when `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_FROM` are set; otherwise email via SMTP; otherwise the server log (dev only).

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

Profiles self-update after every successful payment via an exponential moving average (α = 0.15): `new_avg = 0.85 × old_avg + 0.15 × new_sample`, so the baseline adapts to natural typing drift over time.

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

## Blockchain Notarization

Every payment is hashed and notarized on a Sepolia testnet smart contract (`contracts/BioShieldLedger.sol`), asynchronously so the payment flow itself stays instant. `/payment` shows a live badge that polls until confirmation, with an Etherscan link and a one-click integrity check (`GET /api/verify-chain/<txn_id>`) that recomputes the hash and compares it on-chain. See [`ROADMAP.md`](ROADMAP.md) for the architecture and deployment details.

---

## File Structure

```
bioshield/
├── app.py                  ← Flask server + all API routes + risk engine
├── models.py                ← SQLAlchemy models (User, KeystrokeProfile, RiskEvent, Transaction)
├── blockchain.py             ← web3.py wrapper for async on-chain notarization
├── contracts/
│   └── BioShieldLedger.sol   ← Solidity contract (testnet notarization)
├── scripts/
│   └── deploy_contract.py    ← Compiles + deploys the contract (py-solc-x, no Node/Hardhat)
├── requirements.txt
├── Procfile / render.yaml     ← Render deployment config
├── static/
│   ├── keystroke.js          ← Biometric capture library (KeystrokeCapture, EnrollmentCollector, BiometricHUD)
│   └── style.css             ← Design system
└── templates/
    ├── login.html
    ├── signup.html
    ├── enroll.html
    ├── test.html
    ├── payment.html
    └── dashboard.html
```
