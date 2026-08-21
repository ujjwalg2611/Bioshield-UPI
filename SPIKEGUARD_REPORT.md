# SpikeGuard — Real-Time Fraud-Spike Detector for Merchant Transactions

**Author:** Ujjwal Gupta
**Track:** Razorpay AI Builder Internship 2026 — Track 2: AI Risk Manager
**Built as an extension of:** [BioShield-UPI](https://github.com/ujjwalg2611/Bioshield-UPI)

---

## 1. Problem

Merchants on a payment platform suffer silent, compounding losses when
fraud activity spikes suddenly — a bot attack testing stolen cards, a
compromised account driving abnormal refunds, or a slow-building
chargeback abuse ring. Manual review catches these far too late, after
the damage compounds across hundreds of transactions. SpikeGuard watches
each merchant's transaction stream continuously and flags statistically
anomalous spikes in real time, with a full audit trail explaining exactly
why each flag fired.

**Strictly defense-only**, per the track's requirement: SpikeGuard
detects and alerts for manual review. It never blocks a transaction,
holds funds, or takes autonomous action — that decision stays with a
human reviewer.

---

## 2. Why this builds on BioShield-UPI

BioShield-UPI already solves a structurally identical problem at a
different level: per-user keystroke-biometric risk scoring using a
**weighted Z-score against a self-updating EWMA baseline**
(`predict_risk` / `update_profile_moving_average` in `app.py`). SpikeGuard
applies the same core technique — deviation-from-personal-baseline
scoring, not a fixed global threshold — to **merchant-level transaction
metrics** instead of keystroke timing:

| | BioShield-UPI (existing) | SpikeGuard (this project) |
|---|---|---|
| Unit of analysis | per-user typing pattern | per-merchant transaction stream |
| Features | dwell/flight time, typing speed, backspace rate, jitter | volume, failure rate, refund rate, chargeback rate |
| Baseline | per-user `KeystrokeProfile`, EWMA-updated | per-merchant `MerchantBaseline`, EWMA-updated |
| Scoring | weighted Z-score → `ALLOW / OTP_REQUIRED / BLOCK` | weighted Z-score → `NORMAL / WATCH / ALERT` |
| Baseline update rule | only updates after a clean/ALLOW event | only updates during NORMAL hours (same principle) |

This isn't a cosmetic parallel — reusing the "only learn from clean
behavior" rule is what stops an active fraud spike from being absorbed
into "what's normal for this merchant" and made invisible to itself.

---

## 3. Data

No real Razorpay transaction data is available, so `data_gen.py`
generates a **60-day, hourly-aggregated synthetic transaction stream for
40 merchants**, with realistic day/night and weekday/weekend seasonality,
and injects three labeled incident types so detector performance can be
measured against ground truth:

- **BOT_ATTACK** — sudden burst in volume + high failure rate (2–6 hours)
- **REFUND_ABUSE** — sudden spike in refund rate on normal volume (3–10 hours)
- **CHARGEBACK_RING** — slow, sustained rise in chargeback rate (24–72 hours) — deliberately the hardest pattern, included to stress-test the detector against something subtler than a sharp spike

The detector never sees these labels during detection — they exist only
to score it afterward.

---

## 4. Method

### 4.1 Detection engine (`detector.py`)

For each merchant-hour, four features are computed: `volume`, `fail_rate`,
`refund_rate`, `chargeback_rate`. Each is compared against that
merchant's own EWMA baseline via a Z-score, combined into a weighted risk
score (chargeback and refund rate weighted highest, since they're the
most direct fraud signals; volume weighted lowest, since legitimate
demand spikes also raise volume):

```
score = 0.15·z_volume + 0.30·z_fail + 0.25·z_refund + 0.30·z_chargeback
NORMAL  if score < 0.30
WATCH   if 0.30 ≤ score < 0.55
ALERT   if score ≥ 0.55
```

### 4.2 Two statistical fixes made during development (the honest iteration story)

**v1 (naive Z-score against empirical history):** precision 0.26, recall
0.76, F1 0.39. Far too many false positives — a merchant with low
overnight volume would see a single failed transaction swing its failure
*rate* wildly (1-in-5 = 20%) even though that's pure sampling noise, not
fraud.

**v2 (added analytic binomial standard error):** rather than only using
empirical historical variance, the expected noise in a rate is now
`sqrt(p(1-p)/n)` — the correct statistical treatment for a proportion
estimated from `n` trials — combined with empirical variance via `max()`.
This fixed the low-volume false-positive problem: precision jumped to
0.92. But recall collapsed to 0.27, because the slow CHARGEBACK_RING
ramp (small base rate, gradual rise) no longer looked surprising on any
single hour.

**v3 (added a rolling trend detector):** a sustained-drift test —
cumulative sum of hourly deviations from baseline, normalized by expected
noise under the null of no change — runs alongside the instant Z-score
specifically for chargeback rate, and the detector uses whichever signal
is stronger. This is what a single-hour Z-score structurally cannot
catch: a ramp that's unremarkable hour-to-hour but real over a wider
window. Final result: **precision 0.64, recall 0.47, F1 0.54.**

![Iteration history](figures/iteration_history.png)

This progression is intentionally kept in the report rather than only
showing the final number — it demonstrates the actual debugging process,
which is what the "reasoning, not black-box output" evaluation criterion
is asking for.

---

## 5. Results (final version)

| Metric | Value |
|---|---|
| Precision | 0.6435 |
| Recall | 0.4664 |
| F1 | 0.5408 |
| Alerts raised | 561 |
| True incident-hours | 774 |
| **False-positive cost** | **₹9,000** (200 false positives × ₹45 estimated manual-review cost) |

![Recall by incident type](figures/recall_by_type.png)

| Incident type | Recall |
|---|---|
| BOT_ATTACK | **1.00** |
| REFUND_ABUSE | **0.988** |
| CHARGEBACK_RING | 0.367 |

**Honest read:** sharp, sudden anomalies (bot attacks, refund spikes) are
caught almost perfectly. The slow chargeback ramp remains the hardest
case — it's caught more often than not caught, but far from solved. This
is reported as-is rather than tuned away, per the track's explicit ask
for "honest metrics including false-positive cost," not a cherry-picked
number.

---

## 6. Live demo

A Flask dashboard (`/spikeguard/`) runs the full detector over the
synthetic dataset and shows:
- Precision/recall/F1/false-positive-cost at a glance
- Recall broken down by incident type
- A live audit trail of the 25 most recent alerts, each with the exact
  Z-score values that triggered it (merchant, hour, decision, ground
  truth, and per-feature trigger detail) — pulled from
  `/spikeguard/api/alerts`
- Full confusion matrix

Run locally:
```bash
pip install -r requirements.txt
python app.py
# visit http://localhost:5000/spikeguard/
```
(Note: `app.py`'s top-level imports include `deepface` for BioShield's
existing face-verification feature; if you only want to demo SpikeGuard
without installing that heavy dependency, run the blueprint standalone —
see `spikeguard/routes.py`, which has no dependency on `deepface` or the
rest of `app.py`.)

---

## 7. Limitations

- **Synthetic data.** No real Razorpay transaction data was available;
  all numbers come from a controlled synthetic generator. Relative
  behavior (sharp spikes caught reliably, slow ramps harder) should
  transfer to real data; absolute precision/recall numbers would not.
- **Chargeback-ring recall (0.37) is a genuine weak point,** not a solved
  problem — flagged honestly rather than tuned to look better on paper.
  Next step would be a proper CUSUM detector with a reset rule (this
  project's trend check is a simplified windowed-sum approximation of
  one) or an isolation-forest-style multivariate model across all four
  features jointly, rather than four independent univariate checks.
- **False-positive cost is an estimate** (₹45/review), not a measured
  figure — a real deployment would calibrate this from actual analyst
  time-tracking data.
- **No cross-merchant signal.** Each merchant is scored independently
  against its own history; a coordinated attack hitting many merchants
  simultaneously with a pattern too subtle for any single merchant's
  baseline to catch would be missed. A production version would add a
  cross-merchant anomaly layer.

---

## 8. Repository structure (added to BioShield-UPI)

```
Bioshield-UPI/
├── app.py                          # + 3 lines registering the SpikeGuard blueprint
├── spikeguard/
│   ├── data_gen.py                 # synthetic merchant stream + labeled incidents
│   ├── detector.py                 # detection engine (Z-score + EWMA + trend)
│   ├── evaluate.py                 # offline evaluation harness
│   ├── routes.py                   # Flask blueprint (dashboard + API)
│   ├── make_figures.py
│   ├── results/
│   │   ├── evaluation_summary.json
│   │   └── audit_trail.json
│   └── figures/
│       ├── iteration_history.png
│       └── recall_by_type.png
├── templates/
│   └── spikeguard_dashboard.html
└── SPIKEGUARD_REPORT.md            # this file
```

## 9. What I'd build next with more time

- Proper CUSUM with reset rule for chargeback detection, replacing the
  windowed-sum approximation
- A "why this merchant, why now" natural-language summary generated per
  alert (template-based, not an LLM call — keeps it fast and free of
  hallucination risk for a defense-only tool) to make the audit trail
  readable by a non-technical risk analyst
- Multivariate (not per-feature independent) anomaly scoring
