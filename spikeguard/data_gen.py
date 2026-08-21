"""
SpikeGuard — synthetic merchant transaction-stream generator.

Generates hourly aggregated transaction metrics per merchant over a fixed
window, with realistic day/night and weekday/weekend seasonality, then
injects three classes of labeled fraud-spike incidents so detector
performance can be measured against ground truth:

  - BOT_ATTACK        : sudden burst in transaction volume + high failure rate
                        (credential-stuffing / card-testing pattern)
  - REFUND_ABUSE       : sudden spike in refund rate on an otherwise normal
                        volume (compromised merchant account / return fraud)
  - CHARGEBACK_RING    : sustained low-and-slow rise in chargeback rate
                        (coordinated abuse ring — harder to detect than a
                        single-hour spike, deliberately included to test
                        the detector against a subtler pattern)

Every hour of every merchant is labeled `is_incident: bool` plus incident
type, which is used only for evaluation — the detector itself never sees
these labels.
"""

import random
import math

random.seed(7)

HOURS = 24 * 60  # 60 days of hourly data per merchant
N_MERCHANTS = 40


def _base_rate(hour_of_week, base_volume):
    """Simple day/night + weekday/weekend seasonality multiplier."""
    hour_of_day = hour_of_week % 24
    day_of_week = hour_of_week // 24
    diurnal = 0.4 + 0.6 * math.sin(math.pi * (hour_of_day - 6) / 14) if 6 <= hour_of_day <= 22 else 0.25
    diurnal = max(diurnal, 0.15)
    weekend_boost = 1.25 if day_of_week % 7 in (5, 6) else 1.0
    return base_volume * diurnal * weekend_boost


def generate_merchant_stream(merchant_id, hours=HOURS, seed=None):
    if seed is not None:
        random.seed(seed)

    base_volume = random.uniform(20, 200)     # avg transactions/hour at peak
    base_fail_rate = random.uniform(0.02, 0.06)
    base_refund_rate = random.uniform(0.01, 0.04)
    base_chargeback_rate = random.uniform(0.001, 0.006)
    avg_amount = random.uniform(300, 3000)

    incidents = []
    # Schedule 0-2 incidents randomly across the window, avoiding the first
    # 5 days (used as pure "clean" burn-in for the detector's baseline).
    n_incidents = random.choice([0, 1, 1, 2])
    for _ in range(n_incidents):
        start = random.randint(5 * 24, hours - 48)
        kind = random.choice(["BOT_ATTACK", "REFUND_ABUSE", "CHARGEBACK_RING"])
        if kind == "BOT_ATTACK":
            duration = random.randint(2, 6)
        elif kind == "REFUND_ABUSE":
            duration = random.randint(3, 10)
        else:  # CHARGEBACK_RING - slow and sustained
            duration = random.randint(24, 72)
        incidents.append({"start": start, "end": start + duration, "kind": kind})

    records = []
    for h in range(hours):
        expected_volume = _base_rate(h, base_volume)
        volume = max(0, int(random.gauss(expected_volume, expected_volume * 0.15)))

        fail_rate = base_fail_rate
        refund_rate = base_refund_rate
        chargeback_rate = base_chargeback_rate
        is_incident = False
        incident_kind = None

        for inc in incidents:
            if inc["start"] <= h < inc["end"]:
                is_incident = True
                incident_kind = inc["kind"]
                progress = (h - inc["start"]) / max(inc["end"] - inc["start"], 1)
                if inc["kind"] == "BOT_ATTACK":
                    volume = int(volume * random.uniform(3.5, 6.0))
                    fail_rate = min(0.9, base_fail_rate + random.uniform(0.35, 0.6))
                elif inc["kind"] == "REFUND_ABUSE":
                    refund_rate = min(0.9, base_refund_rate + random.uniform(0.25, 0.5))
                elif inc["kind"] == "CHARGEBACK_RING":
                    # ramps up slowly rather than jumping immediately
                    chargeback_rate = base_chargeback_rate + progress * random.uniform(0.06, 0.12)

        volume = max(volume, 1)
        failed = int(volume * min(fail_rate + random.gauss(0, 0.01), 0.95))
        refunded = int(volume * min(refund_rate + random.gauss(0, 0.01), 0.95))
        chargebacks = int(volume * min(chargeback_rate + random.gauss(0, 0.003), 0.5))
        gross_amount = volume * avg_amount * random.uniform(0.8, 1.2)

        records.append({
            "merchant_id": merchant_id,
            "hour": h,
            "volume": volume,
            "failed": failed,
            "refunded": refunded,
            "chargebacks": chargebacks,
            "gross_amount": round(gross_amount, 2),
            "is_incident": is_incident,
            "incident_kind": incident_kind,
        })

    return records


def generate_dataset(n_merchants=N_MERCHANTS, hours=HOURS, seed=42):
    random.seed(seed)
    all_records = []
    for m in range(n_merchants):
        merchant_id = f"M{m:03d}"
        stream = generate_merchant_stream(merchant_id, hours=hours, seed=seed * 1000 + m)
        all_records.extend(stream)
    return all_records


if __name__ == "__main__":
    data = generate_dataset(n_merchants=5, hours=200)
    n_incident_hours = sum(1 for r in data if r["is_incident"])
    print(f"Generated {len(data)} merchant-hours, {n_incident_hours} labeled incident-hours "
          f"({n_incident_hours/len(data)*100:.1f}%)")
    print("Sample record:", data[0])
    incident_sample = next(r for r in data if r["is_incident"])
    print("Sample incident record:", incident_sample)
