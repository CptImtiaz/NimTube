import copy

from app.fact_checking.checker import check_script
from app.llm import LLM
from app.ranking.engine import build_ranking
from app.research.engine import research_topic
from app.research.verifier import quote_in_page, number_matches, verify_fact
from app.script_generation.writer import write_script

TOPIC = {"title": "Top 10 Largest Moons in the Solar System", "ranking_type": "factual",
         "metric": "mean diameter", "unit": "km"}


def _ranking(settings, db):
    llm = LLM(settings)
    return llm, build_ranking(settings, TOPIC, research_topic(settings, db, llm, TOPIC, "j"))


def test_verifier_basics():
    page = "Intro.\nGanymede has a mean diameter of 5,268 kilometers (3,273 miles), the largest moon.[3]\nEnd."
    assert quote_in_page("Ganymede has a mean diameter of 5,268 kilometers (3,273 miles), the largest moon.", page)
    assert not quote_in_page("Ganymede has a mean diameter of 9,999 kilometers, the largest moon by far.", page + " x")
    assert not quote_in_page("too short", page)
    assert number_matches(5268, "diameter of 5,268 km")
    assert number_matches(1.5e9, "about 1.5 billion people")
    assert not number_matches(5268, "diameter of 5,150 km")
    ok, note = verify_fact({"quote": "x" * 30, "value": 1}, {"status": "error:http 403", "text": ""})
    assert not ok and "unavailable" in note


def test_fixture_script_passes_review(settings, db):
    llm, ranking = _ranking(settings, db)
    script = write_script(settings, llm, ranking)
    assert check_script(settings, script, ranking) == []


def test_review_catches_invented_number_copying_and_banned_phrases(settings, db):
    llm, ranking = _ranking(settings, db)
    script = write_script(settings, llm, ranking)
    bad = copy.deepcopy(script)
    bad["segments"][0]["narration"] = "Number ten: Oberon, which is 1,999 kilometres across."
    bad["hook"] = "Buckle up for 42 amazing moons."
    bad["segments"][1]["narration"] = ranking["entries"][1]["evidence"][0]["quote"]
    problems = check_script(settings, bad, ranking)
    text = " | ".join(problems)
    assert "1999" in text and "not in the verified facts" in text
    assert "hook: number 42" in text
    assert "buckle up" in text
    assert "copies 8+ consecutive words" in text


def test_review_requires_full_countdown(settings, db):
    llm, ranking = _ranking(settings, db)
    script = write_script(settings, llm, ranking)
    script["segments"] = script["segments"][:-1]
    assert any("cover #10..#1" in p for p in check_script(settings, script, ranking))
