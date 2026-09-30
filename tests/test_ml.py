import numpy as np
import pandas as pd
from ml.evaluate import eer_and_frr, evaluate
from ml.risk_model import score_sample, FEATURES


def _samples(rng, n=12):
    base = np.array([95, 140, 235, 5.5, 30, 0.03])
    return [dict(zip(FEATURES, base + rng.normal(0, [6, 12, 15, 0.4, 5, 0.01]))) for _ in range(n)]


def test_eer_perfect_and_random():
    assert eer_and_frr(np.zeros(50), np.ones(50) * 5)[0] == 0.0
    rng = np.random.default_rng(0)
    e, _ = eer_and_frr(rng.random(2000), rng.random(2000))
    assert 0.4 < e < 0.6


def test_mahalanobis_genuine_scores_below_impostor():
    rng = np.random.default_rng(1)
    hist = _samples(rng)
    genuine = _samples(rng, 20)
    impostor = dict(zip(FEATURES, [140, 220, 340, 3.0, 70, 0.10]))
    g = np.mean([score_sample(x, hist) for x in genuine])
    assert score_sample(impostor, hist) > g
    assert score_sample(impostor, hist) > 0.95


def test_needs_history():
    assert score_sample({f: 1.0 for f in FEATURES}, []) is None


def test_pipeline_runs_on_synthetic_data():
    """Smoke test of the harness only - synthetic data says nothing about real accuracy."""
    rng = np.random.default_rng(2)
    rows = []
    for s in range(4):
        mu = rng.uniform(0.05, 0.25, 6)
        for _ in range(400):
            rows.append({'subject': f's{s}', **{f'H.k{i}': mu[i] + rng.normal(0, .01) for i in range(3)},
                         **{f'DD.k{i}': mu[3 + i] + rng.normal(0, .02) for i in range(3)}})
    res = evaluate(pd.DataFrame(rows))
    assert set(res) >= {'mahalanobis', 'one_class_svm', 'isolation_forest'}
    assert all(0 <= r['mean_eer'] <= 1 for r in res.values())
