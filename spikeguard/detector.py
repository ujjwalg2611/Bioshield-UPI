"""
SpikeGuard detection engine.

Deliberately mirrors BioShield-UPI's existing `predict_risk` /
`update_profile_moving_average` pattern (app.py) — same weighted Z-score
scoring against a self-updating EWMA baseline, same tiered decision
output — just applied to merchant-level transaction metrics (failure
rate, refund rate, chargeback rate, volume) instead of per-keystroke
biometric features. This keeps the fraud-spike detector consistent with
the rest of the codebase rather than introducing a second unrelated
approach.

Tiering (analogous to ALLOW / OTP_REQUIRED / BLOCK in the original engine):
  NORMAL → nothing unusual, no action
  WATCH  → mild anomaly, log for review, no alert fatigue
  ALERT  → flag for manual review, this is what gets evaluated as a "positive"
"""

from dataclasses import dataclass, field


@dataclass
class MerchantBaseline:
    """EWMA baseline per merchant, one instance per merchant_id — the direct
    analog of BioShield's KeystrokeProfile."""
    merchant_id: str
    avg_volume: float = 0.0
    avg_fail_rate: float = 0.0
    avg_refund_rate: float = 0.0
    avg_chargeback_rate: float = 0.0

    std_volume: float = 1.0
    std_fail_rate: float = 0.01
    std_refund_rate: float = 0.01
    std_chargeback_rate: float = 0.005

    sample_count: int = 0
    recent_samples: list = field(default_factory=list)  # bounded history for std estimation
    recent_chargeback_devs: list = field(default_factory=list)  # for CUSUM-style trend check


def compute_hour_features(record):
    volume = record["volume"]
    fail_rate = record["failed"] / volume if volume > 0 else 0.0
    refund_rate = record["refunded"] / volume if volume > 0 else 0.0
    chargeback_rate = record["chargebacks"] / volume if volume > 0 else 0.0
    return {
        "volume": volume,
        "fail_rate": fail_rate,
        "refund_rate": refund_rate,
        "chargeback_rate": chargeback_rate,
    }


def _std_from_samples(samples, key):
    vals = [s[key] for s in samples]
    if len(vals) < 2:
        return 1e-3
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return max(var ** 0.5, 1e-3)


def update_baseline(baseline: MerchantBaseline, features: dict, alpha: float = 0.10,
                     history_len: int = 72):
    """EWMA update, same alpha-blend approach as BioShield's profile update.
    Lower alpha (0.10 vs BioShield's 0.15) since merchant metrics are noisier
    hour-to-hour and we want a more stable baseline."""
    a = alpha
    baseline.avg_volume = (1 - a) * baseline.avg_volume + a * features["volume"] if baseline.sample_count > 0 else features["volume"]
    baseline.avg_fail_rate = (1 - a) * baseline.avg_fail_rate + a * features["fail_rate"] if baseline.sample_count > 0 else features["fail_rate"]
    baseline.avg_refund_rate = (1 - a) * baseline.avg_refund_rate + a * features["refund_rate"] if baseline.sample_count > 0 else features["refund_rate"]
    baseline.avg_chargeback_rate = (1 - a) * baseline.avg_chargeback_rate + a * features["chargeback_rate"] if baseline.sample_count > 0 else features["chargeback_rate"]

    baseline.recent_samples.append(features)
    if len(baseline.recent_samples) > history_len:
        baseline.recent_samples.pop(0)

    if len(baseline.recent_samples) >= 5:
        baseline.std_volume = _std_from_samples(baseline.recent_samples, "volume")
        baseline.std_fail_rate = _std_from_samples(baseline.recent_samples, "fail_rate")
        baseline.std_refund_rate = _std_from_samples(baseline.recent_samples, "refund_rate")
        baseline.std_chargeback_rate = _std_from_samples(baseline.recent_samples, "chargeback_rate")

    baseline.sample_count += 1


def update_trend_tracker(baseline: MerchantBaseline, features: dict, window: int = 24):
    """Tracks chargeback-rate deviation from baseline EVERY hour, regardless
    of decision (unlike update_baseline, which only learns from NORMAL
    hours). This is deliberate: to detect a slow ramp we need to keep
    watching the ramp build up against a FIXED pre-incident reference —
    since update_baseline freezes baseline.avg_chargeback_rate during
    WATCH/ALERT hours, that frozen value is exactly the right reference
    point for measuring how far the current trend has drifted from
    "normal for this merchant"."""
    dev = features["chargeback_rate"] - baseline.avg_chargeback_rate
    baseline.recent_chargeback_devs.append(dev)
    if len(baseline.recent_chargeback_devs) > window:
        baseline.recent_chargeback_devs.pop(0)


def _trend_z_chargeback(baseline: MerchantBaseline) -> float:
    """A sustained-drift test: sums recent per-hour deviations from baseline
    and normalizes by the expected standard deviation of that sum under the
    null hypothesis of no change (per-hour binomial std x sqrt(n)). A single
    noisy hour barely moves this; a real multi-hour upward ramp accumulates
    and eventually crosses the threshold even though no individual hour
    looked alarming on its own — this is what an instantaneous z-score
    structurally cannot catch."""
    n = len(baseline.recent_chargeback_devs)
    if n < 6:
        return 0.0
    cumulative = sum(baseline.recent_chargeback_devs)
    avg_vol = max(baseline.avg_volume, 1)
    p = max(min(baseline.avg_chargeback_rate, 0.99), 0.0005)
    per_hour_std = (p * (1 - p) / avg_vol) ** 0.5
    expected_std = per_hour_std * (n ** 0.5)
    if expected_std < 1e-6:
        return 0.0
    return max(0.0, cumulative / expected_std)


def detect_spike(features: dict, baseline: MerchantBaseline) -> dict:
    """Core detection function — direct structural analog of BioShield's
    predict_risk(). Returns a tier + score + per-feature audit detail."""
    if baseline.sample_count < 10:
        return {
            "decision": "NORMAL",
            "score": 0.0,
            "reason": "Insufficient baseline history — defaulting to NORMAL",
            "details": {},
        }

    def z_score(val, mean, std):
        if std < 1e-6:
            return 0.0
        return abs(val - mean) / std

    def z_score_upside(val, mean, std):
        if std < 1e-6:
            return 0.0
        return max(0.0, (val - mean) / std)

    def rate_std(baseline_rate, volume, empirical_std):
        """Rates (fail/refund/chargeback) are proportions of a small integer
        count out of `volume` trials — at low volume, sampling noise alone
        can swing a rate a lot even with no change in true underlying risk
        (e.g. 1 failure out of 5 transactions = 20%, pure chance). Using
        analytic binomial standard error (sqrt(p(1-p)/n)) alongside the
        empirical EWMA-history std, and taking the max of the two, prevents
        low-volume hours from triggering false alarms on noise while still
        catching genuine sustained rate shifts (where empirical std, built
        from many prior hours, correctly reflects the merchant's real
        volatility)."""
        p = max(min(baseline_rate, 0.99), 0.001)
        analytic_std = (p * (1 - p) / max(volume, 1)) ** 0.5
        return max(analytic_std, empirical_std)

    volume = features["volume"]

    z_volume = z_score(features["volume"], baseline.avg_volume, baseline.std_volume)
    z_fail = z_score_upside(
        features["fail_rate"], baseline.avg_fail_rate,
        rate_std(baseline.avg_fail_rate, volume, baseline.std_fail_rate),
    )
    z_refund = z_score_upside(
        features["refund_rate"], baseline.avg_refund_rate,
        rate_std(baseline.avg_refund_rate, volume, baseline.std_refund_rate),
    )
    z_chargeback_instant = z_score_upside(
        features["chargeback_rate"], baseline.avg_chargeback_rate,
        rate_std(baseline.avg_chargeback_rate, volume, baseline.std_chargeback_rate),
    )
    z_chargeback_trend = _trend_z_chargeback(baseline)
    # Take whichever signal is stronger: a sharp single-hour spike (instant)
    # or a slow sustained ramp (trend) — either pattern should trigger.
    z_chargeback = max(z_chargeback_instant, z_chargeback_trend)

    # Weights: chargeback and refund rate get the most weight since they're
    # the most direct fraud signals; volume alone is weighted lower since
    # legitimate demand spikes (e.g. flash sales) also raise volume.
    raw_score = (
        z_volume * 0.15 +
        z_fail * 0.30 +
        z_refund * 0.25 +
        z_chargeback * 0.30
    )
    score = min(raw_score / 5.0, 1.0)

    details = {
        "z_volume": round(z_volume, 3),
        "z_fail_rate": round(z_fail, 3),
        "z_refund_rate": round(z_refund, 3),
        "z_chargeback_rate_instant": round(z_chargeback_instant, 3),
        "z_chargeback_rate_trend": round(z_chargeback_trend, 3),
        "observed": {k: round(v, 4) for k, v in features.items()},
        "baseline": {
            "avg_volume": round(baseline.avg_volume, 2),
            "avg_fail_rate": round(baseline.avg_fail_rate, 4),
            "avg_refund_rate": round(baseline.avg_refund_rate, 4),
            "avg_chargeback_rate": round(baseline.avg_chargeback_rate, 4),
        },
    }

    if score < 0.30:
        decision = "NORMAL"
    elif score < 0.55:
        decision = "WATCH"
    else:
        decision = "ALERT"

    return {"decision": decision, "score": round(score, 4), "details": details}
