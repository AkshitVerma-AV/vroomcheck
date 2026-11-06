"""Unit tests for the parts of Vroomcheck that must not silently regress:
the causal spike test, the ESV26-289 indicator and model-year test, narrative
cleaning and the verifier guardrail."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vroomcheck.agents import graph  # noqa: E402
from vroomcheck.detection.indicator import merge_events, score_indicator  # noqa: E402
from vroomcheck.detection.model_years import elevated_years  # noqa: E402
from vroomcheck.detection.spike import combine_flags, score_cluster  # noqa: E402
from vroomcheck.detection.text import clean_narrative  # noqa: E402

MONTHS = pd.date_range("2020-01-01", periods=36, freq="MS")
COMPONENTS = ["BRAKES", "ENGINE", "STEERING", "ELECTRICAL"]


def _fleet(bad_from: int | None = None) -> pd.DataFrame:
    """A fleet with an even complaint mix: one big seller, twenty small lines.
    From month bad_from, line SMALL_0 gets a surge of brake complaints. (With only a
    handful of lines, one surge shifts every other component's baseline, because the
    shares sum to one: a known limitation of the paper's normalisation.)"""
    rows = []
    for v, per in [("BIG", 100)] + [(f"SMALL_{i}", 4) for i in range(20)]:
        for comp in COMPONENTS:
            for i, m in enumerate(MONTHS):
                n = 20 if (v == "SMALL_0" and comp == "BRAKES" and bad_from is not None and i >= bad_from) else per
                rows.append(("MAKE", v, comp, m, n))
    return pd.DataFrame(rows, columns=["make", "model", "component", "month", "n"])


def test_indicator_ignores_volume_when_the_mix_is_normal():
    ind = score_indicator(_fleet(), MONTHS)
    big = ind[ind.model == "BIG"]
    assert big.n_12m.max() > 1000          # by far the most complaints ...
    assert not ind.is_elevated.any()       # ... but nobody's mix differs from the fleet
    assert big.p_indicator.abs().max() < 0.2


def test_indicator_flags_a_sustained_abnormal_share_causally():
    ind = score_indicator(_fleet(bad_from=20), MONTHS)
    bad = ind[(ind.model == "SMALL_0") & (ind.component == "BRAKES")].set_index("month")
    first = bad.index[bad.is_elevated][0]
    # the 6-month test window has to fill with elevated months first, as with the paper's 180 days
    assert first >= MONTHS[20] and first <= MONTHS[26]
    assert not ind[ind.model == "BIG"].is_elevated.any()
    # causal: scoring only the first 28 months gives the same flags for those months
    short = score_indicator(_fleet(bad_from=20).query("month < @MONTHS[28]"), MONTHS[:28])
    short = short[(short.model == "SMALL_0") & (short.component == "BRAKES")].set_index("month")
    assert short.is_elevated.any()
    assert short.is_elevated.equals(bad.is_elevated.iloc[:28])


def test_flags_less_than_a_year_apart_merge_into_one_event():
    months = pd.date_range("2020-01-01", periods=40, freq="MS")
    flagged = [0, 1, 2, 10, 11, 30]       # gap of 8 months merges; gap of 19 starts a new event
    ind = pd.DataFrame({"make": "M", "model": "X", "component": "BRAKES", "month": months, "n": 5.0,
                        "p_indicator": 0.9, "is_elevated": [i in flagged for i in range(40)]})
    ev = merge_events(ind)
    assert len(ev) == 2
    assert ev.start_month.tolist() == [months[0], months[30]]
    assert ev.flagged_months.tolist() == [5, 1]


def test_binomial_names_only_the_overrepresented_model_year():
    years = elevated_years(pd.Series({2018: 10, 2019: 60, 2020: 12, 2021: 9}))
    assert years.loc[years.is_elevated, "model_year"].tolist() == [2019]


def test_combine_flags_for_each_detector():
    p, q = pd.Series([True, True, False]), pd.Series([True, False, False])
    assert combine_flags(p, q, "poisson").tolist() == [True, True, False]
    assert combine_flags(p, q, "paper").tolist() == [True, False, False]
    assert combine_flags(p, q, "combined").tolist() == [True, False, False]
    assert combine_flags(p, q, "either").tolist() == [True, True, False]


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
