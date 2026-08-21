"""
SpikeGuard evaluation harness.

Runs the detector hour-by-hour over every merchant's transaction stream
(chronological order, as it would run in production — no peeking ahead),
compares ALERT decisions against the ground-truth incident labels from
data_gen.py, and reports:

  - precision / recall / F1 at the ALERT tier
  - false-positive cost: estimated rupee cost of manual review time spent
    on alerts that turned out to be normal activity
  - a full audit trail: every ALERT logged with the exact feature/z-score
    values that triggered it (required for the "explainable" bar in the
    track brief)

Baseline update policy mirrors BioShield's original risk engine: the
merchant's EWMA baseline is only updated on NORMAL hours, never on
WATCH/ALERT hours — this prevents an active fraud spike from being
absorbed into "what's normal for this merchant," which would make the
detector blind to the spike halfway through it.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_gen import generate_dataset
from detector import MerchantBaseline, compute_hour_features, update_baseline, update_trend_tracker, detect_spike

AVG_REVIEW_COST_INR = 45.0  # estimated analyst time cost per manual review of a flagged alert


def run_evaluation(n_merchants=40, hours=1440, seed=42):
    dataset = generate_dataset(n_merchants=n_merchants, hours=hours, seed=seed)

    # group by merchant, preserving chronological order (already sorted by construction)
    by_merchant = {}
    for r in dataset:
        by_merchant.setdefault(r["merchant_id"], []).append(r)

    baselines = {mid: MerchantBaseline(merchant_id=mid) for mid in by_merchant}

    audit_trail = []
    tp, fp, fn, tn = 0, 0, 0, 0
    incident_kind_recall = {}  # kind -> [caught, total]

    for mid, records in by_merchant.items():
        baseline = baselines[mid]
        for r in records:
            features = compute_hour_features(r)
            result = detect_spike(features, baseline)
            is_alert = result["decision"] == "ALERT"
            is_incident = r["is_incident"]

            if is_incident:
                kind = r["incident_kind"]
                incident_kind_recall.setdefault(kind, [0, 0])
                incident_kind_recall[kind][1] += 1
                if is_alert:
                    incident_kind_recall[kind][0] += 1

            if is_alert and is_incident:
                tp += 1
            elif is_alert and not is_incident:
                fp += 1
            elif not is_alert and is_incident:
                fn += 1
            else:
                tn += 1

            if is_alert:
                audit_trail.append({
                    "merchant_id": mid,
                    "hour": r["hour"],
                    "decision": result["decision"],
                    "score": result["score"],
                    "ground_truth_incident": is_incident,
                    "ground_truth_kind": r["incident_kind"],
                    "triggering_features": result["details"],
                })

            # Robust baseline update: only learn averages from NORMAL hours
            # (mirrors BioShield's "only update profile after clean/ALLOW
            # event" rule), but the trend tracker watches every hour
            # unconditionally so it can see a ramp build up during an
            # ongoing incident.
            update_trend_tracker(baseline, features)
            if result["decision"] == "NORMAL":
                update_baseline(baseline, features)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    false_positive_cost = fp * AVG_REVIEW_COST_INR

    per_kind_recall = {
        kind: {"caught": c, "total": t, "recall": round(c / t, 3) if t > 0 else None}
        for kind, (c, t) in incident_kind_recall.items()
    }

    summary = {
        "n_merchants": n_merchants,
        "hours_per_merchant": hours,
        "total_merchant_hours": len(dataset),
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_cost_inr": false_positive_cost,
        "avg_review_cost_inr": AVG_REVIEW_COST_INR,
        "recall_by_incident_type": per_kind_recall,
        "n_alerts_raised": tp + fp,
        "n_true_incident_hours": tp + fn,
    }
    return summary, audit_trail


def main():
    summary, audit_trail = run_evaluation()
    print(json.dumps(summary, indent=2))

    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(results_dir, exist_ok=True)

    with open(os.path.join(results_dir, "evaluation_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    with open(os.path.join(results_dir, "audit_trail.json"), "w") as f:
        json.dump(audit_trail, f, indent=2)

    print(f"\nSaved evaluation_summary.json and audit_trail.json ({len(audit_trail)} alert entries) to {results_dir}")


if __name__ == "__main__":
    main()
