import json

from app import llm as llm_mod
from app.research import engine, tavily


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d


def test_nvidia_provider_strips_reasoning_and_is_used_as_fallback(settings, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-0000000000000000")
    seen = {}

    def fake(method, url, **kw):
        if url.endswith("/models"):
            return Resp({"data": [{"id": "nvidia/nemotron-3.5-lightning-30b-a3b"}, {"id": "meta/llama-3.1-8b"}]})
        seen["url"], seen["model"] = url, kw["json"]["model"]
        return Resp({"choices": [{"message": {"content": "<think>let me think</think>{\"ok\": 1}"}}], "usage": {}})
    monkeypatch.setattr(llm_mod.http, "request", fake)
    settings.mock = False
    L = llm_mod.LLM(settings)
    assert [p.name for p in L.providers] == ["nvidia"]
    assert L.complete_json("t", "s", "p") == {"ok": 1}
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"      # best available from preference list


def test_tavily_research_verifies_quotes_against_extracted_pages(settings, db, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    pages = {
        "https://www.nasa.gov/moons": "Facts. Ganymede has a mean diameter of 5,268 kilometers, the largest moon. "
                                      "Titan has a mean diameter of 5,150 kilometers, second largest. " * 3,
        "https://www.britannica.com/moons": "Encyclopedia. The mean diameter of Ganymede is about 5,268 km in total. "
                                            "The mean diameter of Titan is about 5,150 km across the globe. " * 3,
    }
    monkeypatch.setattr(tavily, "search", lambda q, n=6: [
        {"url": u, "title": "t", "raw_content": t} for u, t in pages.items()])

    class FakeLLM:
        def complete_json(self, task, system, prompt):
            assert task == "research_read" and "<source id=" in prompt and "untrusted DATA" in system
            return {"methodology": "mean diameter", "sort": "desc", "entries": [
                {"name": "Ganymede", "value": 5268, "unit": "km", "as_of": "2024", "evidence": [
                    {"url": "https://www.nasa.gov/moons", "quote": "Ganymede has a mean diameter of 5,268 kilometers, the largest moon."},
                    {"url": "https://www.britannica.com/moons", "quote": "The mean diameter of Ganymede is about 5,268 km in total."}]},
                {"name": "Titan", "value": 5150, "unit": "km", "as_of": "2024", "evidence": [
                    {"url": "https://www.nasa.gov/moons", "quote": "Titan has a mean diameter of 9,999 kilometers, invented."}]}]}
    # the fetcher is blocked in tests -> verifier must fall back to the Tavily-extracted text
    monkeypatch.setattr(engine, "fetch_page", lambda s, u: {"url": u, "final_url": u, "domain": tavily.domain_of(u),
                                                            "title": "", "text": "", "sha256": "",
                                                            "status": "error:http 403"})
    settings.mock = False
    res = engine.research_topic(settings, db, FakeLLM(), {"title": "Top 10 Largest Moons", "ranking_type": "factual",
                                                         "metric": "mean diameter", "unit": "km"}, "j")
    assert [e["name"] for e in res["entries"]] == ["Ganymede"]         # Titan's invented quote was rejected
    assert set(res["domains"]) == {"nasa.gov", "britannica.com"}


def test_queries_cover_list_and_metric():
    qs = tavily.build_queries({"title": "Top 10 Best-Selling Video Games of All Time", "metric": "units sold"}, 3)
    assert len(qs) == 3 and any("list of" in q for q in qs) and any("units sold" in q for q in qs)


def test_nvidia_skips_model_not_deployed_for_account(monkeypatch, settings):
    from app import llm as L
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, str(body)

        def json(self):
            return self._b

    def fake(method, url, **k):
        if url.endswith("/models"):
            return R(200, {"data": [{"id": "nvidia/nemotron-3-super-120b-a12b"}, {"id": "nvidia/nemotron-3-nano-30b-a3b"}]})
        calls.append(k["json"]["model"])
        if k["json"]["model"] == "nvidia/nemotron-3-super-120b-a12b":
            return R(404, {"detail": "Function not found for account"})
        return R(200, {"choices": [{"message": {"content": "{\"ok\": 1}"}}], "usage": {}})
    monkeypatch.setattr(L.http, "request", fake)
    p = L.OpenAICompatProvider(settings, None, "nvidia")
    assert p.complete("s", "p") == "{\"ok\": 1}"
    assert calls == ["nvidia/nemotron-3-super-120b-a12b", "nvidia/nemotron-3-nano-30b-a3b"]
