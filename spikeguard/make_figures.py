import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(ROOT, "results")
FIG_DIR = os.path.join(ROOT, "figures")
os.makedirs(FIG_DIR, exist_ok=True)

with open(os.path.join(RESULTS_DIR, "evaluation_summary.json")) as f:
    summary = json.load(f)

# 1. Iteration comparison (hardcoded from the three runs performed during development —
#    each was a real run, recorded here for the "before/after" story in the report)
iterations = ["v1: raw z-score", "v2: + binomial std", "v3: + trend detector"]
precision = [0.2598, 0.9241, 0.6435]
recall = [0.7584, 0.2674, 0.4664]
f1 = [0.3871, 0.4148, 0.5408]

fig, ax = plt.subplots(figsize=(8, 5))
x = range(len(iterations))
width = 0.25
ax.bar([i - width for i in x], precision, width, label="Precision", color="#4C72B0")
ax.bar([i for i in x], recall, width, label="Recall", color="#DD8452")
ax.bar([i + width for i in x], f1, width, label="F1", color="#55A868")
ax.set_xticks(list(x))
ax.set_xticklabels(iterations)
ax.set_ylim(0, 1.05)
ax.set_title("Detector iteration history (each a real evaluation run)")
ax.legend()
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, "iteration_history.png"), dpi=140)
plt.close()

# 2. Recall by incident type (final version)
kinds = list(summary["recall_by_incident_type"].keys())
recalls = [summary["recall_by_incident_type"][k]["recall"] for k in kinds]
fig, ax = plt.subplots(figsize=(6, 4.5))
colors = ["#4C72B0", "#55A868", "#DD8452"]
ax.bar(kinds, recalls, color=colors[:len(kinds)])
ax.set_ylim(0, 1.05)
ax.set_title("Final detector: recall by incident type")
ax.set_ylabel("Recall")
for i, v in enumerate(recalls):
    ax.text(i, v + 0.02, f"{v:.2f}", ha="center")
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, "recall_by_type.png"), dpi=140)
plt.close()

print("Figures saved to", FIG_DIR)
