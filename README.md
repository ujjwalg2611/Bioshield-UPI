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
| `/api/payment/initiate` | POST | ✓ | Validate transfer, open a pending payment (stage PIN) |
| `/api/payment/pin` | POST | ✓ | Authorization PIN + typing rhythm. Rhythm ok pays; mismatch opens step-up |
| `/api/payment/face` | POST | ✓ | Step-up A: DeepFace match to enrolled photo |
| `/api/payment/otp/send` | POST | ✓ | Step-up B: send OTP to the registered mobile (also resend) |
| `/api/payment/otp` | POST | ✓ | Verify the OTP |
| `/api/pin/setup` | POST | ✓ | One-time PIN issue for accounts created before PINs existed |
| `/api/phone/setup` | POST | ✓ | Add a mobile number to an older account (only while none is on file) |
| `/api/risk-history` | GET | ✓ | Dashboard data |
| `/api/verify-chain/<txn_id>` | GET | ✓ | Recompute + compare on-chain hash to prove a record wasn't tampered with |

---

## Payment Authentication Flow

```
Authorization PIN (typed naturally) ──wrong ×3 (or ×5 account-wide → 15 min lock)──► cancelled
   │ correct
   ├─ typing rhythm matches ─────────────────────────────► paid   (fast path, no camera)
   └─ typing rhythm mismatch → choose one:
        ├─ Face match to profile photo ──ok──► paid   (3 misses disables this option)
        └─ OTP to registered mobile ──ok──► paid      (wrong ×3 → cancelled)
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

Thresholds live in `app.py` (`ALLOW_THRESHOLD`, `BLOCK_THRESHOLD`) and are shared by login, `/api/test` and payments. In the payment flow, anything other than ALLOW opens step-up (face or OTP); set `BLOCK_ENFORCED=true` to make BLOCK cancel the payment instead.

**Cold start:** a user with fewer than 3 enrollment samples is stepped up (face/OTP) rather than allowed - the engine fails closed. Set `COLD_START_POLICY=allow` to restore the old behaviour. The weights above and the 0.35 / 0.65 thresholds are hand-chosen, not fitted to data; the evaluation below is how they should be tuned.

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
├── ml/                       ← evaluate.py (CMU benchmark), risk_model.py (Mahalanobis scorer)
├── tests/                    ← pytest suite
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


---

## ML: evaluation & alternative scorer

`ml/evaluate.py` benchmarks detectors (the z-score heuristic, Manhattan, Mahalanobis, One-Class SVM, Isolation Forest) on the public CMU keystroke dataset using EER and FRR@1%FAR:

```bash
pip install -r requirements-dev.txt
python -m ml.evaluate --csv DSL-StrongPasswordData.csv   # writes results/metrics.json
```

`ml/risk_model.py` is a per-user Mahalanobis scorer used at runtime. By default (`RISK_ENGINE=zscore`) it runs in shadow mode: both scores are stored in each payment's `features_snapshot`, so they can be compared before switching with `RISK_ENGINE=mahalanobis`. Its scores are not yet calibrated against the 0.35 / 0.65 thresholds.

Which features matter? `ml/ablation.py` derives the app's aggregate features from the CMU per-key timings and re-runs the protocol with each feature dropped and used alone, for the Mahalanobis scorer and for the app's hand-set z-score weights:

```bash
python -m ml.ablation --csv DSL-StrongPasswordData.csv   # writes results/ablation.json
```

It cannot assess `backspace_rate` (CMU samples are error-free), and it measures the aggregates on an 11-key password, not live PIN sessions.

Results table: _not yet run — fill in from `results/metrics.json` and `results/ablation.json` after running the scripts. No accuracy, FAR/FRR or fraud-reduction figure should be quoted for this project until they exist._

## Tests

```bash
python -m pytest -q tests
```
CI runs the same on every push (`.github/workflows/ci.yml`). The suite covers feature extraction and the EMA update, the score-to-decision thresholds, the full payment state machine (PIN -> rhythm -> OTP, no skipping stages, replay and cross-user rejection, expiry, supersede), PIN/OTP/login lockouts, input validation, cold start, login error uniformity, and CORS. Keystroke timings in tests are synthetic: they verify control flow, not real-world biometric accuracy.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | required in production | JWT signing and OTP HMAC. In dev a random per-process key is used |
| `DATABASE_URL` | `sqlite:///bioshield.db` | Use PostgreSQL in production |
| `FACE_ENCRYPTION_KEY` | random per process | Fernet key for face images at rest |
| `RISK_ENGINE` | `zscore` | `mahalanobis` switches the decider (recalibrate thresholds first) |
| `BLOCK_ENFORCED` | `false` | `true` cancels the payment at score >= 0.65 instead of stepping up |
| `COLD_START_POLICY` | `stepup` | `allow` lets unenrolled users through the rhythm check |
| `CORS_ORIGINS` | none (same-origin only) | Comma-separated allowed origins |
| `DEMO_MODE` | `false` | Echoes OTPs in API responses - never in production |

## Security notes & known limitations

Controls: bcrypt for passwords and PINs; random server-issued PINs; account and PIN lockouts; per-route rate limits; OTPs stored as HMACs with a 5-minute TTL and a fixed attempt budget; face images encrypted at rest; server-side payment state machine with row locks; one bcrypt comparison per login even for unknown emails (no timing oracle).

Known limitations (deliberately listed):
- **Client-reported biometrics.** Keystroke timings come from the browser, so anyone with a valid token and PIN can post forged timings straight to the API. The rhythm check defends against a human impostor typing, not against scripted API abuse. Fixing this needs device attestation.
- **No face liveness check.** A photo or replay could pass the DeepFace match.
- **Account enumeration is reduced, not removed.** Signup still reveals duplicate emails (rate-limited) and only real accounts show the lockout message; closing this needs email verification.
- **JWTs** are HS256, 24 h, with no revocation.
- **Rate limiter** uses in-process memory; use Redis behind multiple workers.
- **Face images** are stored on local disk, which is ephemeral on most free hosts.
- **Thresholds and weights** are hand-set and uncalibrated (see ML section).
