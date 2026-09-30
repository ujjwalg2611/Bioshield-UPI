"""Per-user Mahalanobis risk scorer used at runtime by app.py.

Fits a shrunk covariance to the user's stored enrollment/update samples
(KeystrokeProfile.raw_samples, at most 20) and converts the squared
Mahalanobis distance of the new sample into a score in [0, 1] using the
chi-square CDF (df = number of features). Under a Gaussian assumption the
score is the fraction of genuine samples that would look *less* typical.

The thresholds in app.py (0.35 / 0.65) were tuned for the z-score heuristic and
have NOT been calibrated for this scorer. Run it in shadow mode
(RISK_ENGINE=zscore, the default) and compare with ml/evaluate.py before
switching RISK_ENGINE=mahalanobis.
"""
import numpy as np
from scipy.stats import chi2

FEATURES = ['avg_dwell_time', 'avg_flight_time', 'avg_press_interval',
            'avg_typing_speed', 'avg_jitter', 'avg_backspace_rate']
MIN_SAMPLES = 5
SHRINKAGE = 0.3          # blend of full covariance and its diagonal
STD_FLOOR = np.array([5.0, 5.0, 5.0, 0.05, 2.0, 0.01])  # per-feature minimum std


def _matrix(samples):
    return np.array([[float(s.get(f, 0.0)) for f in FEATURES] for s in samples])


def mahalanobis_sq(x, samples):
    X = _matrix(samples)
    mu = X.mean(axis=0)
    cov = np.cov(X, rowvar=False)
    diag = np.diag(np.maximum(np.diag(cov), STD_FLOOR ** 2))
    cov = (1 - SHRINKAGE) * cov + SHRINKAGE * diag + 1e-6 * np.eye(len(FEATURES))
    cov = cov + np.diag(np.maximum(STD_FLOOR ** 2 - np.diag(cov), 0))
    d = x - mu
    return float(d @ np.linalg.solve(cov, d))


def score_sample(features, samples):
    """Return risk in [0, 1], or None if there isn't enough history."""
    if len(samples) < MIN_SAMPLES:
        return None
    x = np.array([float(features.get(f, 0.0)) for f in FEATURES])
    return float(chi2.cdf(mahalanobis_sq(x, samples), df=len(FEATURES)))
