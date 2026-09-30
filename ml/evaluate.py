"""Offline evaluation of keystroke-dynamics detectors on the CMU benchmark.

Dataset: Killourhy & Maxion (2009), "Comparing Anomaly-Detection Algorithms for
Keystroke Dynamics" - DSL-StrongPasswordData.csv (51 subjects x 400 samples of
the same password, 31 timing features: H.* hold, DD.* down-down, UD.* up-down).
Download it from https://www.cs.cmu.edu/~keystroke/ and pass the path.

Protocol (per subject, then averaged):
  train    = first 200 genuine samples
  genuine  = remaining 200 samples
  impostor = first 5 samples of every other subject (250 samples)
Metrics: EER, plus FRR at 1% FAR. Every number in results/ is produced by this
script - nothing is hard-coded.

Usage: python -m ml.evaluate --csv path/to/DSL-StrongPasswordData.csv
"""
import argparse
import json
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_curve
from sklearn.svm import OneClassSVM


def eer_and_frr(genuine_scores, impostor_scores):
    """Scores are anomaly scores (higher = more suspicious)."""
    y = np.r_[np.zeros(len(genuine_scores)), np.ones(len(impostor_scores))]
    s = np.r_[genuine_scores, impostor_scores]
    far, tpr, _ = roc_curve(y, s)      # far here = false-reject rate of genuine users
    frr_gen = far                       # genuine flagged as impostor
    miss = 1 - tpr                      # impostor accepted
    i = np.nanargmin(np.abs(frr_gen - miss))
    eer = float((frr_gen[i] + miss[i]) / 2)
    ok = np.where(miss <= 0.01)[0]      # impostor acceptance <= 1%
    frr_at_1 = float(frr_gen[ok].min()) if len(ok) else 1.0
    return eer, frr_at_1


class ZScoreHeuristic:
    """Mean |z| over features - stand-in for the app's hand-weighted score."""
    def fit(self, X):
        self.mu, self.sd = X.mean(0), np.maximum(X.std(0), 1e-3)
        return self
    def score(self, X):
        return np.abs((X - self.mu) / self.sd).mean(1)


class Manhattan:
    def fit(self, X):
        self.mu = X.mean(0)
        self.mad = np.maximum(np.abs(X - self.mu).mean(0), 1e-3)
        return self
    def score(self, X):
        return (np.abs(X - self.mu) / self.mad).sum(1)


class Mahalanobis:
    def fit(self, X):
        self.mu = X.mean(0)
        cov = np.cov(X, rowvar=False) + 1e-4 * np.eye(X.shape[1])
        self.inv = np.linalg.inv(cov)
        return self
    def score(self, X):
        d = X - self.mu
        return np.einsum('ij,jk,ik->i', d, self.inv, d)


class OCSVM:
    def fit(self, X):
        self.mu, self.sd = X.mean(0), np.maximum(X.std(0), 1e-3)
        self.m = OneClassSVM(kernel='rbf', gamma='scale', nu=0.05).fit((X - self.mu) / self.sd)
        return self
    def score(self, X):
        return -self.m.decision_function((X - self.mu) / self.sd)


class IForest:
    def fit(self, X):
        self.m = IsolationForest(n_estimators=200, random_state=0).fit(X)
        return self
    def score(self, X):
        return -self.m.score_samples(X)


DETECTORS = {'zscore_heuristic': ZScoreHeuristic, 'manhattan': Manhattan,
             'mahalanobis': Mahalanobis, 'one_class_svm': OCSVM, 'isolation_forest': IForest}


def evaluate(df, n_train=200, n_impostor=5):
    feats = [c for c in df.columns if c.startswith(('H.', 'DD.', 'UD.'))]
    subjects = sorted(df['subject'].unique())
    out = {name: {'eer': [], 'frr_at_1pct_far': []} for name in DETECTORS}
    for subj in subjects:
        own = df[df.subject == subj][feats].to_numpy()
        train, genuine = own[:n_train], own[n_train:]
        imp = np.vstack([df[df.subject == o][feats].to_numpy()[:n_impostor]
                         for o in subjects if o != subj])
        for name, cls in DETECTORS.items():
            det = cls().fit(train)
            eer, frr = eer_and_frr(det.score(genuine), det.score(imp))
            out[name]['eer'].append(eer)
            out[name]['frr_at_1pct_far'].append(frr)
    return {n: {'mean_eer': float(np.mean(v['eer'])), 'std_eer': float(np.std(v['eer'])),
                'mean_frr_at_1pct_far': float(np.mean(v['frr_at_1pct_far'])),
                'n_subjects': len(subjects)} for n, v in out.items()}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', required=True)
    ap.add_argument('--out', default='results/metrics.json')
    a = ap.parse_args()
    res = evaluate(pd.read_csv(a.csv))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, 'w'), indent=2)
    print(f"{'detector':20s} {'EER':>8s} {'FRR@1%FAR':>10s}")
    for n, r in sorted(res.items(), key=lambda kv: kv[1]['mean_eer']):
        print(f"{n:20s} {r['mean_eer']:8.3f} {r['mean_frr_at_1pct_far']:10.3f}")
