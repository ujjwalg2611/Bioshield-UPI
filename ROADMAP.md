# Architecture & Security Notes

Details on the blockchain notarization layer, security hardening, and deployment — kept out of the main README to keep that scannable.

## Security hardening

- OTP is never returned in the API response in production (`DEMO_MODE` gates this for local testing only)
- Face images are encrypted at rest (Fernet) and only decrypted briefly in-memory for DeepFace comparison
- Rate limiting on `/api/login` and `/api/payment`
- **Account lockout**: 5 failed logins locks the account for 15 minutes (`User.failed_login_attempts`, `User.locked_until`)
- **Input validation on payments**: UPI ID format is regex-checked, with a ₹100,000 max transaction limit
- **OTP email delivery**: `send_otp_email()` sends via SMTP if `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` are set; otherwise falls back to a console log so local dev works without any SMTP setup

## Blockchain notarization

- `contracts/BioShieldLedger.sol` — Solidity contract for on-chain transaction notarization (testnet only, no real money moved)
- `blockchain.py` — web3.py wrapper; notarization runs asynchronously so payments stay instant
- `scripts/deploy_contract.py` — compiles and deploys with py-solc-x (no Node/Hardhat dependency)
- `GET /api/verify-chain/<txn_id>` — recomputes and compares the hash to prove a record wasn't tampered with
- `static/keystroke.js` (`BioShieldAPI.verifyChain()`, `renderChainBadge()`) and `templates/payment.html` show a live badge after every successful payment — "Notarizing on-chain…" → polls every 5s → "Verified on-chain" with an Etherscan link and a "Verify integrity" button

## Before running locally

The `User` model has two columns (`failed_login_attempts`, `locked_until`) that `db.create_all()` won't add to an existing SQLite file — it only creates new tables. If you already have a `bioshield.db` / `instance/` folder from earlier testing, delete it once so it's recreated with the current schema:

```bash
rm -f instance/bioshield.db bioshield.db
```

(This drops any test users/transactions — fine for a dev DB; don't do this against a real production database without a proper migration.)

## Deploying to Render

1. Push this repo to GitHub
2. In Render: **New → Blueprint**, point it at your repo — it reads `render.yaml` and creates the web service + a free Postgres DB automatically
3. In the service's **Environment** tab, set:
   - `FACE_ENCRYPTION_KEY` — generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `RPC_URL`, `PRIVATE_KEY`, `CONTRACT_ADDRESS` — from the testnet contract deployment (run `scripts/deploy_contract.py` once, locally, first)
   - `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` — optional, for real OTP emails
4. Deploy. First boot is slow (DeepFace downloads the VGG-Face model weights); subsequent boots are faster since Render caches the image layer.
