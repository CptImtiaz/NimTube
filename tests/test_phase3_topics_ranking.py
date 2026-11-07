import pytest

from app.llm import LLM, parse_json
from app.ranking.engine import build_ranking, RankingRejected
from app.research.engine import research_topic
from app.topic_selection import selector
from app.trend_discovery import engine


def test_parse_json_handles_fences_and_prose():
    assert parse_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert parse_json('text {"x": 1} more') == {"x": 1}


def test_score_range():
    c = {"scores": {k: 10 for k in list(selector.RUBRIC_WEIGHTS) + ["policy_safety"]}, "niche": "science"}
    assert selector.compute_score(c, {}) == 100.0
    c0 = {"scores": {}, "niche": "x"}
    assert selector.compute_score(c0, {}) == 0.0


def test_topic_selection_rejects_unsafe_fake_trends_and_records_reasons(settings, db):
    trends = engine.discover(settings, db)
    chosen = selector.select_topic(settings, db, LLM(settings), trends, "job1")
    assert chosen["title"] == "Top 10 Largest Moons in the Solar System"
    assert chosen["trend_scope"] == "global"
    rows = {r["title"]: r for r in db.query("SELECT * FROM topics WHERE job_id='job1'")}
    assert "policy" in rows["Top 10 Richest People Ranked by Religion"]["rejected_reason"]
    assert "not in today's signals" in rows["Top 10 Viral Dances This Week"]["rejected_reason"]
    assert rows["Top 10 Largest Moons in the Solar System"]["selected"] == 1


def test_repeat_topic_is_blocked(settings, db):
    trends = engine.discover(settings, db)
    selector.select_topic(settings, db, LLM(settings), trends, "job1")
    chosen = selector.select_topic(settings, db, LLM(settings), trends, "job2")
    assert chosen["title"] != "Top 10 Largest Moons in the Solar System"
    row = db.one("SELECT rejected_reason FROM topics WHERE job_id='job2' AND title LIKE '%Moons%'")
    assert "similar" in row["rejected_reason"]


def _topic():
    return {"title": "Top 10 Largest Moons in the Solar System", "ranking_type": "factual",
            "metric": "mean diameter", "unit": "km"}


def test_research_verifies_quotes_and_drops_fabricated_evidence(settings, db):
    res = research_topic(settings, db, LLM(settings), _topic(), "job1")
    names = [e["name"] for e in res["entries"]]
    assert "Iapetus" not in names                 # its quote is not on the page -> rejected
    assert "Iapetus" in res["rejected_entries"]
    assert len(res["domains"]) >= 2
    bad = db.one("SELECT verified, note FROM facts WHERE entry_name='Iapetus'")
    assert bad["verified"] == 0 and "not found" in bad["note"]


def test_ranking_order_and_evidence(settings, db):
    res = research_topic(settings, db, LLM(settings), _topic(), "job1")
    r = build_ranking(settings, _topic(), res)
    assert [e["rank"] for e in r["entries"]] == list(range(10, 0, -1))
    assert r["entries"][-1]["name"] == "Ganymede" and r["entries"][-1]["value"] == 5268
    assert r["entries"][0]["name"] == "Oberon"  # Iapetus rejected, so Charon is #11
    assert all(e["evidence"] for e in r["entries"])
    assert r["label"] == "Factual ranking"


def test_ranking_rejects_mixed_units_and_thin_evidence(settings, db):
    res = research_topic(settings, db, LLM(settings), _topic(), "job1")
    res2 = {**res, "entries": [dict(e) for e in res["entries"]]}
    res2["entries"][0]["unit"] = "miles"
    with pytest.raises(RankingRejected, match="mixed units"):
        build_ranking(settings, _topic(), res2)
    res3 = {**res, "entries": res["entries"][:7]}
    with pytest.raises(RankingRejected, match="only 7"):
        build_ranking(settings, _topic(), res3)
    one_domain = [{**e, "evidence_domains": ["nasa.gov"]} for e in res["entries"]]
    with pytest.raises(RankingRejected, match="independent"):
        build_ranking(settings, _topic(), {**res, "entries": one_domain})
