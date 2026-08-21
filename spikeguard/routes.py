"""
SpikeGuard Flask blueprint — a live-demoable merchant risk dashboard.

Runs the detector over the synthetic dataset on first request (cached
after that), and serves:
  - GET  /spikeguard/                 -> dashboard page
  - GET  /spikeguard/api/summary      -> evaluation summary JSON
  - GET  /spikeguard/api/alerts       -> paginated alert list (audit trail)
  - GET  /spikeguard/api/merchant/<id>-> hourly series + alerts for one merchant (for charting)

This is intentionally separate from the main BioShield auth/payment flow —
it's a standalone risk-ops view an internal team would use, not something
an end user hits — so it doesn't touch the existing auth decorators.
"""

import os
import sys
from flask import Blueprint, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_gen import generate_dataset
from detector import MerchantBaseline, compute_hour_features, update_baseline, update_trend_tracker, detect_spike

spikeguard_bp = Blueprint(
    "spikeguard", __name__,
    url_prefix="/spikeguard",
    template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
)

_cache = {"dataset": None, "by_merchant": None, "summary": None, "audit_trail": None, "merchant_series": None}


def _run_and_cache():
    if _cache["summary"] is not None:
        return

    dataset = generate_dataset(n_merchants=40, hours=1440, seed=42)
    by_merchant = {}
    for r in dataset:
        by_merchant.setdefault(r["merchant_id"], []).append(r)

    baselines = {mid: MerchantBaseline(merchant_id=mid) for mid in by_merchant}
    audit_trail = []
    merchant_series = {mid: [] for mid in by_merchant}
    tp, fp, fn, tn = 0, 0, 0, 0
    incident_kind_recall = {}

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

            entry = {
                "merchant_id": mid, "hour": r["hour"], "decision": result["decision"],
                "score": result["score"], "ground_truth_incident": is_incident,
                "ground_truth_kind": r["incident_kind"], "volume": r["volume"],
                "failed": r["failed"], "refunded": r["refunded"], "chargebacks": r["chargebacks"],
            }
            merchant_series[mid].append(entry)
            if is_alert:
                audit_trail.append({**entry, "triggering_features": result["details"]})

            update_trend_tracker(baseline, features)
            if result["decision"] == "NORMAL":
                update_baseline(baseline, features)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    summary = {
        "n_merchants": len(by_merchant),
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "false_positive_cost_inr": fp * 45.0,
        "n_alerts_raised": tp + fp,
        "n_true_incident_hours": tp + fn,
        "recall_by_incident_type": {
            k: {"caught": c, "total": t, "recall": round(c / t, 3) if t else None}
            for k, (c, t) in incident_kind_recall.items()
        },
    }

    _cache["dataset"] = dataset
    _cache["by_merchant"] = by_merchant
    _cache["summary"] = summary
    _cache["audit_trail"] = audit_trail
    _cache["merchant_series"] = merchant_series


@spikeguard_bp.route("/")
def dashboard():
    _run_and_cache()
    return render_template("spikeguard_dashboard.html", summary=_cache["summary"])


@spikeguard_bp.route("/api/summary")
def api_summary():
    _run_and_cache()
    return jsonify(_cache["summary"])


@spikeguard_bp.route("/api/alerts")
def api_alerts():
    _run_and_cache()
    limit = int(request.args.get("limit", 50))
    only_true_positives = request.args.get("true_positives_only") == "1"
    alerts = _cache["audit_trail"]
    if only_true_positives:
        alerts = [a for a in alerts if a["ground_truth_incident"]]
    return jsonify(alerts[:limit])


@spikeguard_bp.route("/api/merchant/<merchant_id>")
def api_merchant_series(merchant_id):
    _run_and_cache()
    series = _cache["merchant_series"].get(merchant_id)
    if series is None:
        return jsonify({"error": "unknown merchant_id"}), 404
    return jsonify(series)


@spikeguard_bp.route("/api/merchants")
def api_merchant_list():
    _run_and_cache()
    ids = sorted(_cache["by_merchant"].keys())
    return jsonify(ids)
