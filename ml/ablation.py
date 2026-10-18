"""Feature ablation: which of the app's aggregate features actually carry identity signal?

The CMU benchmark (DSL-StrongPasswordData.csv) has per-key timings, not the six session
aggregates the app computes. This script derives the app's aggregates from those timings
(dwell, flight, press interval, speed, jitter), then re-runs the evaluation protocol of
ml/evaluate.py with each feature (a) removed and (b) used alone.

Caveats - read before quoting any number:
  * It measures the information in the *aggregates* on an 11-key password typed 400 times,
    not the live app. Real PIN sessions are shorter and noisier.
  * `backspace_rate` cannot be assessed: CMU samples are error-free by construction.
  * CMU times are in seconds; they are converted to ms to match the app's units.
  * Two scorers are compared: the app's runtime-style shrunk Mahalanobis (ml/risk_model.py
    constants) and the app's hand-set z-score weights (dwell .25, flight .30, speed .25,
    jitter .10; press_interval has weight 0 in the app).

Usage: python -m ml.ablation --csv DSL-StrongPasswordData.csv   # writes results/ablation.json
"""
import argparse
import json
import os
import numpy as np
import pandas as pd

from ml.evaluate import eer_and_frr

FEATURES = ['dwell', 'flight', 'press_interval', 'speed', 'jitter']
STD_FLOOR = {'dwell': 5.0, 'flight': 5.0, 'press_interval': 5.0, 'speed': 0.05, 'jitter': 2.0}
APP_WEIGHTS = {'dwell': 0.25, 'flight': 0.30, 'press_interval': 0.0, 'speed': 0.25, 'jitter': 0.10}
SHRINKAGE = 0.3


def derive_features(df):
    """CMU per-key timings (seconds) -> the app's session aggregates (ms, keys/s)."""
    H = [c for c in df.columns if c.startswith('H.')]
    DD = [c for c in df.columns if c.startswith('DD.')]
    UD = [c for c in df.columns if c.startswith('UD.')]
    total_s = df[DD].sum(axis=1) + df[H[-1]]
    return pd.DataFrame({
        'subject': df['subject'].values,
        'dwell': df[H].mean(axis=1).values * 1000,
        'flight': df[UD].mean(axis=1).values * 1000,
        'press_interval': df[DD].mean(axis=1).values * 1000,
        'speed': (len(H) / total_s).values,
        'jitter': df[UD].std(axis=1, ddof=0).values * 1000,
    })


class ShrunkMahalanobis:
    """Same recipe as ml/risk_model.py (shrinkage + per-feature std floor), any feature subset."""
    def __init__(self, cols):
        self.cols = cols
        self.floor = np.array([STD_FLOOR[c] for c in cols])

    def fit(self, X):
        self.mu = X.mean(0)
        if X.shape[1] == 1:
            cov = np.array([[max(X.var(0, ddof=1)[0], self.floor[0] ** 2)]])
        else:
            cov = np.cov(X, rowvar=False)
            diag = np.diag(np.maximum(np.diag(cov), self.floor ** 2))
            cov = (1 - SHRINKAGE) * cov + SHRINKAGE * diag + 1e-6 * np.eye(len(self.cols))
            cov = cov + np.diag(np.maximum(self.floor ** 2 - np.diag(cov), 0))
        self.inv = np.linalg.inv(cov)
        return self

    def score(self, X):
        d = X - self.mu
        return np.einsum('ij,jk,ik->i', d, self.inv, d)


class WeightedZ:
    """|z| per feature, combined with the app's hand-set weights (or equal weights)."""
    def __init__(self, cols, weights):
        self.cols = cols
        self.w = np.array([weights[c] for c in cols], dtype=float)
        self.floor = np.array([STD_FLOOR[c] for c in cols])

    def fit(self, X):
        self.mu = X.mean(0)
        self.sd = np.maximum(X.std(0), self.floor)
        return self

    def score(self, X):
        return (np.abs((X - self.mu) / self.sd) * self.w).sum(1)


def _scorers(cols):
    equal = {c: 1.0 for c in cols}
    out = {'mahalanobis': ShrunkMahalanobis(cols), 'z_equal_weights': WeightedZ(cols, equal)}
    if sum(APP_WEIGHTS[c] for c in cols) > 0:       # app weights are all zero for press_interval alone
        out['z_app_weights'] = WeightedZ(cols, APP_WEIGHTS)
    return out


def _mean_eer(F, cols, scorer_name, n_train, n_impostor):
    subjects = sorted(F['subject'].unique())
    per_subject = []
    for subj in subjects:
        own = F[F.subject == subj][cols].to_numpy()
        train, genuine = own[:n_train], own[n_train:]
        imp = np.vstack([F[F.subject == o][cols].to_numpy()[:n_impostor] for o in subjects if o != subj])
        det = _scorers(cols)[scorer_name].fit(train)
        per_subject.append(eer_and_frr(det.score(genuine), det.score(imp))[0])
    return float(np.mean(per_subject)), float(np.std(per_subject))


def run_ablation(df, n_train=200, n_impostor=5):
    F = derive_features(df)
    res = {'n_subjects': int(F['subject'].nunique()), 'features': FEATURES, 'scorers': {}}
    for scorer in ['mahalanobis', 'z_equal_weights', 'z_app_weights']:
        base_mean, base_std = _mean_eer(F, FEATURES, scorer, n_train, n_impostor)
        rows = {'all_features': {'mean_eer': base_mean, 'std_eer': base_std}, 'drop_one': {}, 'only_one': {}}
        for f in FEATURES:
            rest = [c for c in FEATURES if c != f]
            m, s = _mean_eer(F, rest, scorer, n_train, n_impostor)
            rows['drop_one'][f] = {'mean_eer': m, 'std_eer': s, 'delta_vs_all': m - base_mean}
            if scorer == 'z_app_weights' and APP_WEIGHTS[f] == 0:
                continue
            m, s = _mean_eer(F, [f], scorer, n_train, n_impostor)
            rows['only_one'][f] = {'mean_eer': m, 'std_eer': s}
        res['scorers'][scorer] = rows
    return res


def _print(res):
    print(f"subjects: {res['n_subjects']}   (lower EER is better; delta > 0 means dropping the feature hurts)")
    for scorer, rows in res['scorers'].items():
        print(f"\n[{scorer}]  all features: EER {rows['all_features']['mean_eer']:.3f}")
        print(f"  {'feature':16s} {'drop-one EER':>13s} {'delta':>8s} {'alone EER':>10s}")
        for f, r in sorted(rows['drop_one'].items(), key=lambda kv: -kv[1]['delta_vs_all']):
            alone = rows['only_one'].get(f)
            alone_txt = '%.3f' % alone['mean_eer'] if alone else 'n/a'
            print(f"  {f:16s} {r['mean_eer']:13.3f} {r['delta_vs_all']:+8.3f} {alone_txt:>10s}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', required=True)
    ap.add_argument('--out', default='results/ablation.json')
    a = ap.parse_args()
    res = run_ablation(pd.read_csv(a.csv))
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    json.dump(res, open(a.out, 'w'), indent=2)
    _print(res)
