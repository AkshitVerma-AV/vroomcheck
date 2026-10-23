"""Unit tests for the parts of Vroomcheck that must not silently regress:
the causal spike test, narrative cleaning and the verifier guardrail."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vroomcheck.agents import graph  # noqa: E402
from vroomcheck.detection.spike import score_cluster  # noqa: E402
from vroomcheck.detection.text import clean_narrative  # noqa: E402

MONTHS = pd.date_range("2020-01-01", periods=36, freq="MS")


def test_flat_series_never_spikes():
    counts = pd.Series(3.0, index=MONTHS)
    line = pd.Series(60.0, index=MONTHS)
    assert not score_cluster(counts, line).is_spike.any()


def test_sudden_rise_spikes_and_is_causal():
    counts = pd.Series(1.0, index=MONTHS)
    counts.iloc[24:] = 12.0
    line = pd.Series(80.0, index=MONTHS)
    s = score_cluster(counts, line)
    first = s.index[s.is_spike][0]
    assert first >= MONTHS[24]           # cannot fire before the rise happens
    assert first <= MONTHS[26]           # fires within a quarter of the rise
    # causal: truncating the future must not change past scores
    s_trunc = score_cluster(counts.iloc[:26], line.iloc[:26])
    assert s_trunc.is_spike.equals(s.is_spike.iloc[:26])


def test_fleet_growth_is_not_a_spike():
    # whole vehicle line grows 4x (new model launch) and the cluster grows with it
    ramp = np.linspace(1, 4, 36)
    counts = pd.Series(2.0 * ramp, index=MONTHS)
    line = pd.Series(100.0 * ramp, index=MONTHS)
    assert not score_cluster(counts, line).is_spike.any()


def test_clean_narrative_strips_boilerplate():
    t = ("THE CONTACT OWNS A 2019 FORD EXPLORER. THE CONTACT STATED THAT THE REAR SUSPENSION "
         "TOE LINK FRACTURED. THE MANUFACTURER WAS NOT NOTIFIED.")
    out = clean_narrative(t)
    assert "contact owns" not in out and "manufacturer was not notified" not in out
    assert "toe link fractured" in out


def test_verifier_rejects_uncited_and_unknown_ids(monkeypatch):
    monkeypatch.setattr(graph.llm, "active_provider", lambda: "offline")
    state = {
        "evidence": {"C-11111111": {"type": "complaint", "text": "steering locked"},
                     "R-23V100000": {"type": "recall", "text": "steering column"}},
        "draft": {"claims": [
            {"text": "Steering locks while driving.", "citations": ["C-11111111"]},
            {"text": "This affects 2 million cars.", "citations": []},
            {"text": "Matches recall R-99V999000.", "citations": ["R-99V999000"]},
        ]},
        "trace": [],
    }
    v = graph.verifier_agent(state)["verification"]["claims"]
    assert [c["verdict"] for c in v] == ["verified", "rejected", "rejected"]


def test_graph_retries_when_too_many_claims_rejected():
    assert graph.route_after_verify({"verification": {"reject_rate": 0.6}, "attempts": 1}) == "retry"
    assert graph.route_after_verify({"verification": {"reject_rate": 0.6}, "attempts": 2}) == "publish"
    assert graph.route_after_verify({"verification": {"reject_rate": 0.0}, "attempts": 1}) == "publish"
