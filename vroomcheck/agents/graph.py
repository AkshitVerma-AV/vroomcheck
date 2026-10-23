"""Defect-brief agents, orchestrated with LangGraph.

    signal_agent ─► retrieval_agent ─► drafting_agent ─► verifier_agent ─► publish
                                             ▲                  │
                                             └── retry (≤2) ────┘  if too many claims rejected

* signal_agent     pulls the cluster statistics and its most representative and
                   most harmful complaints from the warehouse.
* retrieval_agent  RAG step: past recalls most similar to the cluster centroid
                   (any manufacturer), recalls / TSBs / investigations on the
                   same vehicle line.
* drafting_agent   LLM writes a brief as JSON; every claim must cite evidence IDs.
* verifier_agent   guardrail: rejects any claim whose citations are missing,
                   unknown, or (when an LLM is available) not supported by the
                   cited text. Only verified claims reach the user.

Evidence IDs are typed so the verifier can resolve them:
    C-<ODINO> complaint · R-<CAMPNO> recall · T-<NHTSA_ID> bulletin · I-<ACTION_NUMBER> investigation
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, TypedDict

import numpy as np
from langgraph.graph import END, StateGraph

from vroomcheck.agents import llm
from vroomcheck.detection.text import strip_recall_intro
from vroomcheck.services import queries as q

log = logging.getLogger(__name__)
ID_RE = re.compile(r"\b([CRTI])-([A-Z0-9]+)\b")
MAX_ATTEMPTS = 2
MAX_REJECT_RATE = 0.3


class BriefState(TypedDict, total=False):
    cluster_id: str
    cluster: dict
    evidence: dict[str, dict]          # evidence_id -> record
    draft: dict
    verification: dict
    attempts: int
    feedback: str
    brief: dict
    trace: list[str]


def _short(t: Any, n: int = 450) -> str:
    t = "" if t is None else str(t)
    return t if len(t) <= n else t[:n] + "..."


def _log(state: BriefState, msg: str) -> list[str]:
    log.info(msg)
    return state.get("trace", []) + [msg]


# ---------------------------------------------------------------- agents
def signal_agent(state: BriefState) -> BriefState:
    cid = state["cluster_id"]
    c = q.get_cluster(cid)
    if c is None:
        raise ValueError(f"unknown cluster {cid}")
    complaints = q.cluster_complaints(cid)
    ev = {}
    for r in complaints.itertuples():
        ev[f"C-{r.odino}"] = {
            "type": "complaint", "date": str(r.received_date), "model_year": int(r.model_year),
            "component": r.primary_component, "crash": bool(r.is_crash), "fire": bool(r.is_fire),
            "injured": int(r.injured), "deaths": int(r.deaths), "selected_as": r.why,
            "text": _short(r.narrative, 600),
        }
    m = q.cluster_monthly(cid)
    recent = m.tail(12)
    c["monthly_last_12"] = {str(k.date()): int(v) for k, v in zip(recent.month, recent.n)}
    c["spike_months"] = [str(x.date()) for x in m.loc[m.is_spike, "month"].tail(6)]
    trace = _log(state, f"signal_agent: cluster {cid} size={c['size']} with {len(ev)} evidence complaints")
    return {"cluster": c, "evidence": ev, "trace": trace, "attempts": 0}


def retrieval_agent(state: BriefState) -> BriefState:
    c, ev = state["cluster"], dict(state["evidence"])
    vec = q.cluster_centroid(state["cluster_id"])
    # (a) past recalls most similar to this failure pattern, any manufacturer (competency Q3)
    for r in q.similar_recalls(vec, k=5).itertuples():
        ev[f"R-{r.campno}"] = {"type": "recall", "scope": "similar past recall (any make)",
                               "date": str(r.recall_date), "makes": r.makes, "component": r.component_tops,
                               "similarity": round(float(r.similarity), 3),
                               "text": _short(strip_recall_intro(r.defect_summary), 500), "consequence": _short(r.consequence_summary, 250)}
    # (b) recalls on this same vehicle line
    for r in q.cluster_recall_matches(state["cluster_id"]).head(3).itertuples():
        key = f"R-{r.campno}"
        ev.setdefault(key, {"type": "recall", "scope": "same vehicle line",
                            "date": str(r.recall_date), "component": r.component_tops,
                            "similarity": round(float(r.similarity), 3),
                            "text": _short(strip_recall_intro(r.defect_summary), 500)})
    # (c) service bulletins on the same line, re-ranked by semantic similarity to the cluster
    tsbs = q.related_tsbs(c["make"], c["model"], int(c["min_model_year"]), int(c["max_model_year"]), None)
    if not tsbs.empty:
        from vroomcheck.detection.embed import embed_texts

        sims = embed_texts([str(s).lower()[:800] for s in tsbs.summary]) @ vec
        tsbs = tsbs.assign(similarity=sims).sort_values("similarity", ascending=False).head(3)
        for r in tsbs.itertuples():
            ev[f"T-{r.nhtsa_id}"] = {"type": "service_bulletin", "doc": r.tsb_document_id,
                                     "date": str(r.communication_date), "component": r.component_tops,
                                     "similarity": round(float(r.similarity), 3), "text": _short(r.summary, 500)}
    # (d) NHTSA investigations on the same line (timeline check)
    inv = q.related_investigations(c["make"], c["model"], int(c["min_model_year"]), int(c["max_model_year"]))
    for r in inv.head(3).itertuples():
        ev[f"I-{r.action_number}"] = {"type": "investigation", "opened": str(r.opened_date),
                                      "closed": str(r.closed_date), "recall": r.campno,
                                      "component": r.component_tops, "text": _short(r.subject, 200)}
    n_new = len(ev) - len(state["evidence"])
    return {"evidence": ev, "trace": _log(state, f"retrieval_agent: added {n_new} recall/TSB/investigation documents")}


DRAFT_SYSTEM = """You are a vehicle-safety analyst writing an early-warning defect brief for NHTSA-style reviewers.
Use ONLY the evidence provided. Every claim must cite one or more evidence IDs exactly as given
(e.g. C-11234567, R-23V456000, T-10234567, I-PE23010). Do not invent IDs, numbers or facts.
Return JSON with keys:
  title (string, <= 12 words),
  risk_level ("HIGH" | "MEDIUM" | "LOW"),
  summary (2-3 sentences, no citations needed),
  claims (list of {"text": string, "citations": [ids]}; 4-7 claims covering: the failure mode,
          conditions / mileage, safety consequence, any similar past recall, any related bulletin or investigation),
  recommended_action (string)."""


def _template_draft(c: dict, ev: dict) -> dict:
    """Deterministic draft used when no LLM key is configured."""
    comp = [k for k, v in ev.items() if v["type"] == "complaint"]
    harm = [k for k in comp if ev[k]["crash"] or ev[k]["fire"] or ev[k]["injured"]]
    rec = [k for k, v in ev.items() if v["type"] == "recall"]
    tsb = [k for k, v in ev.items() if v["type"] == "service_bulletin"]
    claims = [{"text": f"Owners of {c['min_model_year']}-{c['max_model_year']} {c['make']} {c['model']} describe a "
                       f"recurring {str(c['top_component']).lower()} problem ({c['keywords']}).",
               "citations": comp[:3]}]
    if harm:
        claims.append({"text": "Some reports involve a crash, fire or injury.", "citations": harm[:3]})
    if rec:
        top = rec[0]
        claims.append({"text": f"The most similar past recall ({top[2:]}, {ev[top]['makes'] if 'makes' in ev[top] else ''}) "
                               f"concerned: {ev[top]['text'][:160]}", "citations": [top]})
    if tsb:
        claims.append({"text": f"A related manufacturer bulletin exists: {ev[tsb[0]]['text'][:160]}",
                       "citations": [tsb[0]]})
    risk = "HIGH" if (c["n_crash"] + c["n_fire"] + c["n_injured"]) > 5 else "MEDIUM"
    return {"title": f"{c['make'].title()} {c['model'].title()} {str(c['top_component']).title()} complaint cluster",
            "risk_level": risk,
            "summary": f"{c['size']} complaints cluster around {c['keywords']}. Status: {c['status']}.",
            "claims": claims,
            "recommended_action": "Analyst review of the cited complaints; consider a preliminary evaluation."}


def drafting_agent(state: BriefState) -> BriefState:
    c, ev = state["cluster"], state["evidence"]
    attempts = state.get("attempts", 0) + 1
    if llm.active_provider() == "offline":
        draft = _template_draft(c, ev)
    else:
        stats = {k: c.get(k) for k in ("make", "model", "min_model_year", "max_model_year", "size", "top_component",
                                        "keywords", "n_crash", "n_fire", "n_injured", "n_deaths", "status",
                                        "first_spike_month", "last_spike_month", "monthly_last_12", "spike_months")}
        user = (f"CLUSTER STATISTICS (from the warehouse, may be stated without citation):\n"
                f"{json.dumps(stats, default=str)}\n\nEVIDENCE:\n{json.dumps(ev, default=str, indent=1)}")
        if state.get("feedback"):
            user += f"\n\nYOUR PREVIOUS DRAFT WAS REJECTED BY THE VERIFIER:\n{state['feedback']}\nFix these problems."
        draft = llm.chat_json(DRAFT_SYSTEM, user)
    return {"draft": draft, "attempts": attempts,
            "trace": _log(state, f"drafting_agent: attempt {attempts} produced {len(draft.get('claims', []))} claims "
                                 f"via {llm.model_name()}")}


VERIFY_SYSTEM = """You check whether claims are supported by cited evidence. For each claim decide
"supported" only if the cited evidence text directly supports it; otherwise "unsupported".
Return JSON: {"results": [{"index": int, "verdict": "supported"|"unsupported", "reason": string}]}"""


def verifier_agent(state: BriefState) -> BriefState:
    ev = state["evidence"]
    claims = state["draft"].get("claims", [])
    checked = []
    for i, cl in enumerate(claims):
        cites = [x.strip() for x in cl.get("citations", []) if isinstance(x, str)]
        # also accept IDs written inline in the text
        cites += [f"{a}-{b}" for a, b in ID_RE.findall(cl.get("text", "")) if f"{a}-{b}" not in cites]
        unknown = [x for x in cites if x not in ev]
        if not cites:
            checked.append({**cl, "index": i, "verdict": "rejected", "reason": "no citation"})
        elif unknown:
            checked.append({**cl, "index": i, "verdict": "rejected", "reason": f"unknown evidence IDs {unknown}"})
        else:
            checked.append({**cl, "citations": cites, "index": i, "verdict": "pending", "reason": ""})

    pending = [c for c in checked if c["verdict"] == "pending"]
    if pending and llm.active_provider() != "offline":
        payload = [{"index": c["index"], "claim": c["text"],
                    "evidence": {x: ev[x].get("text", "") for x in c["citations"]}} for c in pending]
        try:
            res = llm.chat_json(VERIFY_SYSTEM, json.dumps(payload))
            verdicts = {r["index"]: r for r in res.get("results", [])}
        except Exception as e:  # verifier must fail closed
            log.warning("LLM verification failed (%s); rejecting unverified claims", e)
            verdicts = {}
        for c in pending:
            v = verdicts.get(c["index"])
            if v and v.get("verdict") == "supported":
                c.update(verdict="verified", reason=v.get("reason", ""))
            else:
                c.update(verdict="rejected", reason=(v or {}).get("reason", "not confirmed by verifier"))
    else:
        for c in pending:
            c.update(verdict="verified", reason="citations resolve to warehouse records")

    rejected = [c for c in checked if c["verdict"] == "rejected"]
    rate = len(rejected) / max(len(checked), 1)
    feedback = "\n".join(f"- claim {c['index']}: {c['reason']}" for c in rejected)
    return {"verification": {"claims": checked, "reject_rate": rate},
            "feedback": feedback,
            "trace": _log(state, f"verifier_agent: {len(checked) - len(rejected)} verified, {len(rejected)} rejected")}


def route_after_verify(state: BriefState) -> str:
    if state["verification"]["reject_rate"] > MAX_REJECT_RATE and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "retry"
    return "publish"


def publish(state: BriefState) -> BriefState:
    d, v, c = state["draft"], state["verification"], state["cluster"]
    verified = [x for x in v["claims"] if x["verdict"] == "verified"]
    brief = {
        "cluster_id": state["cluster_id"],
        "vehicle": f"{c['min_model_year']}-{c['max_model_year']} {c['make']} {c['model']}",
        "status": c["status"],
        "title": d.get("title"), "risk_level": d.get("risk_level"), "summary": d.get("summary"),
        "claims": [{"text": x["text"], "citations": x["citations"]} for x in verified],
        "rejected_claims": [{"text": x["text"], "reason": x["reason"]} for x in v["claims"] if x["verdict"] == "rejected"],
        "recommended_action": d.get("recommended_action"),
        "evidence": state["evidence"],
        "citation_coverage": 1.0 if verified else 0.0,  # by construction every published claim is cited & verified
        "generated_by": llm.model_name(),
        "attempts": state.get("attempts"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trace": state.get("trace", []) + ["publish: brief saved"],
    }
    q.save_brief(state["cluster_id"], brief)
    return {"brief": brief}


def build_graph():
    g = StateGraph(BriefState)
    g.add_node("signal_agent", signal_agent)
    g.add_node("retrieval_agent", retrieval_agent)
    g.add_node("drafting_agent", drafting_agent)
    g.add_node("verifier_agent", verifier_agent)
    g.add_node("publish", publish)
    g.set_entry_point("signal_agent")
    g.add_edge("signal_agent", "retrieval_agent")
    g.add_edge("retrieval_agent", "drafting_agent")
    g.add_edge("drafting_agent", "verifier_agent")
    g.add_conditional_edges("verifier_agent", route_after_verify, {"retry": "drafting_agent", "publish": "publish"})
    g.add_edge("publish", END)
    return g.compile()


_GRAPH = None


def generate_brief(cluster_id: str) -> dict:
    global _GRAPH
    _GRAPH = _GRAPH or build_graph()
    return _GRAPH.invoke({"cluster_id": cluster_id})["brief"]


def generate_top_briefs(n: int = 5) -> list[str]:
    ids = q.list_alerts("EMERGING", limit=n).cluster_id.tolist()
    for cid in ids:
        try:
            generate_brief(cid)
        except Exception as e:
            log.exception("brief failed for %s: %s", cid, e)
    return ids


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) > 1:
        print(json.dumps(generate_brief(sys.argv[1]), indent=2, default=str)[:4000])
    else:
        print(generate_top_briefs())
