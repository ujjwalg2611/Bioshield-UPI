import numpy as np
import pandas as pd
import pytest
from ml.ablation import derive_features, run_ablation, FEATURES


def _frame(n_subjects=4, n=400, keys=4, seed=0, informative=('H', 'UD')):
    """Synthetic CMU-shaped data (seconds). Harness smoke test only - proves nothing about real accuracy."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_subjects):
        mu = {g: rng.uniform(0.07, 0.25, keys) for g in ('H', 'DD', 'UD')}
        for _ in range(n):
            r = {'subject': f's{s}'}
            for g in ('H', 'DD', 'UD'):
                sd = 0.004 if g in informative else 0.05
                for i in range(keys):
                    r[f'{g}.k{i}'] = mu[g][i] + rng.normal(0, sd)
            rows.append(r)
    return pd.DataFrame(rows)


def test_derive_features_known_values():
    df = pd.DataFrame([{'subject': 'a', 'H.a': 0.10, 'H.b': 0.12, 'DD.a.b': 0.20, 'UD.a.b': 0.10}])
    f = derive_features(df).iloc[0]
    assert f['dwell'] == pytest.approx(110.0)            # ms
    assert f['flight'] == pytest.approx(100.0)
    assert f['press_interval'] == pytest.approx(200.0)
    assert f['speed'] == pytest.approx(2 / (0.20 + 0.12))
    assert f['jitter'] == pytest.approx(0.0)


def test_ablation_runs_and_reports_every_feature():
    res = run_ablation(_frame())
    assert res['n_subjects'] == 4
    for scorer in ('mahalanobis', 'z_equal_weights', 'z_app_weights'):
        rows = res['scorers'][scorer]
        assert set(rows['drop_one']) == set(FEATURES)
        assert 0 <= rows['all_features']['mean_eer'] <= 1


def test_ablation_is_sensitive_to_informative_features():
    """On synthetic data where only timing noise is small, EER should be low; with huge noise it is not."""
    good = run_ablation(_frame(informative=('H', 'DD', 'UD')))['scorers']['mahalanobis']['all_features']['mean_eer']
    bad = run_ablation(_frame(informative=()))['scorers']['mahalanobis']['all_features']['mean_eer']
    assert good < bad
