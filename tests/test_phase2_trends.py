from app.trend_discovery import engine, sources
from app.trend_discovery.sources import _parse_traffic


def test_parse_traffic():
    assert _parse_traffic("200K+") == 200_000
    assert _parse_traffic("2M+") == 2_000_000
    assert _parse_traffic("1,000+") == 1000
    assert _parse_traffic(None) is None


def test_google_trends_rss_parsing(settings):
    items = sources.google_trends(settings, "US")
    assert items[0]["title"] == "europa clipper"
    assert items[0]["traffic"] == 200_000
    assert items[0]["source"] == "google_trends"


def test_discovery_scores_cross_country_topics_higher(settings, db):
    results = engine.discover(settings, db)
    top = {r["keyword"]: r for r in results}
    assert "europa clipper" in top
    ec = top["europa clipper"]
    assert set(ec["countries"]) >= {"US", "GB", "IN"}
    assert ec["is_global"] is True                       # >= 3 countries
    football = [r for r in results if "football" in r["keyword"]]
    assert all(not r["is_global"] for r in football)     # single-country signal never "global"
    assert results[0]["score"] >= results[-1]["score"]
    # stored historically
    assert db.one("SELECT COUNT(*) c FROM trend_signals")["c"] > 0
    assert db.one("SELECT COUNT(*) c FROM trend_keywords")["c"] > 0


def test_single_region_is_not_global(settings, db):
    settings.raw["trends"]["regions"] = ["US"]
    results = engine.discover(settings, db)
    assert all(not r["is_global"] for r in results)
