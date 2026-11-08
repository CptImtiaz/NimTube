import json

from app import llm as llm_mod
from app.research import engine, tavily


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)
        self.headers = {"Content-Type": "application/json"}

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
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: fake("POST", url, **kw))
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
                {"name": "Titan", "value": 9999, "unit": "km", "as_of": "2024", "evidence": [
                    {"url": "https://www.nasa.gov/moons", "quote": "Titan has a mean diameter of 9,999 kilometers, invented."}]},
                {"name": "Callisto", "value": 4821, "unit": "km", "as_of": "2024", "evidence": [
                    {"url": "https://www.nasa.gov/moons", "quote": "Callisto is 4,821 km wide."}]}]}
    # the fetcher is blocked in tests -> verifier must fall back to the Tavily-extracted text
    monkeypatch.setattr(engine, "fetch_page", lambda s, u: {"url": u, "final_url": u, "domain": tavily.domain_of(u),
                                                            "title": "", "text": "", "sha256": "",
                                                            "status": "error:http 403"})
    settings.mock = False
    res = engine.research_topic(settings, db, FakeLLM(), {"title": "Top 10 Largest Moons", "ranking_type": "factual",
                                                         "metric": "mean diameter", "unit": "km"}, "j")
    assert [e["name"] for e in res["entries"]] == ["Ganymede"]         # invented value / absent entry rejected
    assert set(res["domains"]) == {"nasa.gov", "britannica.com"}


def test_queries_cover_list_and_metric():
    qs = tavily.build_queries({"title": "Top 10 Best-Selling Video Games of All Time", "metric": "units sold"}, 3)
    assert len(qs) == 3 and any("list of" in q for q in qs) and any("units sold" in q for q in qs)


def test_nvidia_skips_unavailable_and_stalled_models(monkeypatch, settings):
    import requests
    from app import llm as L
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    calls = []

    class R:
        def __init__(self, code, body=None, stream_lines=None, stall=False):
            self.status_code, self._b, self.text = code, body, str(body)
            self.headers = {"Content-Type": "text/event-stream" if stream_lines is not None else "application/json"}
            self._lines, self._stall = stream_lines or [], stall

        def json(self):
            return self._b

        def iter_lines(self, decode_unicode=True):
            if self._stall:
                raise requests.exceptions.ReadTimeout("stalled")
            yield from self._lines

    def fake_get(method, url, **k):
        return R(200, {"data": [{"id": "deepseek-ai/deepseek-v4.1-flash"}, {"id": "nvidia/nemotron-3-super-120b-a12b"},
                                {"id": "nvidia/nemotron-3-nano-30b-a3b"}]})

    def fake_post(url, json=None, **k):
        calls.append(json["model"])
        if json["model"] == "deepseek-ai/deepseek-v4.1-flash":
            return R(200, stream_lines=[], stall=True)               # too slow -> stalled stream
        if json["model"] == "nvidia/nemotron-3-super-120b-a12b":
            return R(404, {"detail": "Function not found for account"})
        return R(200, stream_lines=['data: {"choices":[{"delta":{"content":"{\\"ok\\": "}}]}',
                                    'data: {"choices":[{"delta":{"content":"1}"}}]}', "data: [DONE]"])
    monkeypatch.setattr(L.http, "request", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    p = L.OpenAICompatProvider(settings, None, "nvidia")
    assert p.complete("s", "p") == '{"ok": 1}'
    assert calls == ["deepseek-ai/deepseek-v4.1-flash", "nvidia/nemotron-3-super-120b-a12b",
                     "nvidia/nemotron-3-nano-30b-a3b"]


def test_omniroute_provider_uses_gateway_url_and_listed_model(monkeypatch, settings):
    import requests
    from app import llm as L
    monkeypatch.setenv("OMNIROUTE_BASE_URL", "https://omniroute.example.com")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "sk-omni")
    seen = {}
    monkeypatch.setattr(L.http, "request", lambda m, url, **k: Resp({"data": [{"id": "text-embedding-3"},
                                                                             {"id": "kimi/kimi-k2"}]}))

    def post(url, json=None, **k):
        seen["url"], seen["model"] = url, json["model"]
        return Resp({"choices": [{"message": {"content": "{\"ok\": 1}"}}], "usage": {}})
    monkeypatch.setattr(requests, "post", post)
    p = L.OpenAICompatProvider(settings, None, "omniroute")
    assert p.available and p.complete("s", "p") == '{"ok": 1}'
    assert seen["url"] == "https://omniroute.example.com/v1/chat/completions" and seen["model"] == "kimi/kimi-k2"
    monkeypatch.delenv("OMNIROUTE_BASE_URL")
    assert not L.OpenAICompatProvider(settings, None, "omniroute").available


def test_repair_quote_finds_real_sentence():
    from app.research.verifier import repair_quote, verify_fact
    page = ("Overview of lakes. The Caspian Sea covers an area of 371,000 square kilometres, making it the largest. "
            "Lake Superior has a surface area of 82,100 km2 and is the largest freshwater lake by area.")
    q = repair_quote("Lake Superior", 82100, page)
    assert q and q.startswith("Lake Superior has a surface area of 82,100")
    assert verify_fact({"quote": q, "value": 82100}, {"status": "ok", "text": page})[0]
    assert repair_quote("Lake Victoria", 68800, page) is None          # never invents evidence
    assert repair_quote("Lake Superior", 99999, page) is None
