"""Visual plan — REAL FOOTAGE FIRST.

Per section the plan is a list of shots:
  intro  : 1-2 licensed footage clips (title overlaid by the editor); title card only if no footage exists
  entry  : 1-2 unique licensed footage clips  +  a short original data card (ranking chart)
  outro  : 1 footage clip (fallback: outro card)
If no footage is found for an entry, an AI still (with camera move) is tried, then original cards.
Every asset is registered in the provenance database; clips are never reused within a video, and nothing is
silently replaced with blank or placeholder media."""
from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..copyright.provenance import register_asset
from ..db import DB
from ..log import get_logger
from . import graphics
from .providers import FOOTAGE_PROVIDERS, IMAGE_PROVIDERS, SEARCH_IMAGE_PROVIDERS

logger = get_logger("visuals")


class Visuals:
    def __init__(self, s: Settings, db: DB, job_id: str, work: Path):
        self.s, self.db, self.job_id, self.work = s, db, job_id, work
        self.used: set[str] = set()
        self.footage_order = [] if s.mock else [p for p in s.get("visuals.footage_providers", []) if p in FOOTAGE_PROVIDERS]
        self.image_order = [] if s.mock else [p for p in s.get("visuals.image_providers", []) if p in IMAGE_PROVIDERS]
        self.search_image_order = [] if s.mock else [p for p in s.get("visuals.search_image_providers", [])
                                                     if p in SEARCH_IMAGE_PROVIDERS]
        self.n = 0

    def _register(self, kind, path, source, creator, proof) -> dict | None:
        proof = dict(proof or {})
        override = proof.pop("licence_override", None)          # per-asset licence (e.g. Openverse CC BY)
        a = register_asset(self.s, self.db, self.job_id, kind=kind, path=path, source=source, creator=creator,
                           proof=proof, licence_override=override)
        if a["status"] == "rejected":
            return None
        return {"kind": "video" if kind == "video" else "image", "path": str(path), "asset_id": a["asset_id"],
                "source": source, "footage": kind == "video"}

    def footage(self, queries: list[str], want: int) -> list[dict]:
        clips = []
        for q in [q for q in queries if q]:
            for prov in self.footage_order:
                if len(clips) >= want:
                    return clips
                self.n += 1
                try:
                    path, creator, proof = FOOTAGE_PROVIDERS[prov](self.s, q, self.work / f"clip{self.n:03d}_{prov}",
                                                                  self.used)
                except Exception as e:
                    logger.info(f"{prov} '{q}': {str(e)[:120]}")
                    continue
                item = self._register("video", path, prov, creator, proof)
                if item:
                    clips.append(item)
                    break            # next query for the next clip -> more variety
        return clips

    def search_image(self, queries: list[str]) -> dict | None:
        """Real photos found by query (NASA for space topics, Openverse CC0/PD/CC-BY) — tried before AI images."""
        for q in [q for q in queries if q][:3]:
            for prov in self.search_image_order:
                self.n += 1
                try:
                    path, creator, proof = SEARCH_IMAGE_PROVIDERS[prov](self.s, q, self.work / f"photo{self.n:03d}_{prov}")
                except Exception as e:
                    logger.info(f"{prov} '{q}': {str(e)[:120]}")
                    continue
                if str(path) in self.used:
                    continue
                item = self._register("image", path, prov, creator, proof)
                if item:
                    self.used.add(str(path))
                    return item
        return None

    def ai_image(self, prompt: str) -> dict | None:
        for prov in self.image_order:
            self.n += 1
            try:
                path, creator, proof = IMAGE_PROVIDERS[prov](self.s, prompt, self.work / f"img{self.n:03d}_{prov}")
            except Exception as e:
                logger.warning(f"{prov} image failed: {str(e)[:150]}")
                continue
            item = self._register("image", path, prov, creator, proof)
            if item:
                return item
        return None

    def original(self, path: Path, what: str) -> dict:
        # always returned: if its rights record were ever rejected, the quality gate blocks the upload
        a = register_asset(self.s, self.db, self.job_id, kind="graphic", path=path, source="original_graphics",
                           creator="GLOBAL TOP 10 AI", proof={"generator": "app.visual_generation.graphics",
                                                              "what": what})
        return {"kind": "image", "path": str(path), "asset_id": a["asset_id"], "source": "original_graphics",
                "footage": False}


def plan_visuals(s: Settings, db: DB, job_id: str, ranking: dict, script: dict, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    v = Visuals(s, db, job_id, work)
    per_entry = int(s.get("visuals.clips_per_entry", 2))
    plan: dict = {"intro": [], "segments": {}, "outro": []}

    intro = v.footage(script.get("intro_footage") or [], int(s.get("visuals.intro_clips", 2)))
    plan["intro"] = intro or [v.original(graphics.title_card(ranking["title"], ranking.get("methodology", "")[:140],
                                                             work / "title.png"), "title card")]
    plan["intro_has_footage"] = bool(intro)

    by_rank = {e["rank"]: e for e in ranking["entries"]}
    footage_entries = 0
    for seg in script["segments"]:
        e = by_rank[seg["rank"]]
        queries = list(seg.get("footage") or [])
        if not queries and seg.get("visual"):                         # older script format
            queries = [seg["visual"].get("stock_query", "")]
        clips = v.footage(queries, per_entry)
        if not clips:                                                 # broaden: entry name alone
            clips = v.footage([e["name"]], 1)
        items = list(clips)
        if clips:
            footage_entries += 1
        else:
            img = v.search_image([*queries, e["name"]]) or \
                v.ai_image(seg.get("image_prompt") or (seg.get("visual") or {}).get("prompt") or e["name"])
            if img:
                items.append(img)
        card = v.original(graphics.entry_card(e, ranking, work / f"rank{e['rank']:02d}_bars.png", "bars"),
                          f"data card #{e['rank']}")
        items.append({**card, "role": "data_card"})
        if len(items) == 1 and ranking["ranking_type"] == "factual":     # cards only: add comparison view
            items.insert(0, v.original(graphics.entry_card(e, ranking, work / f"rank{e['rank']:02d}_circles.png",
                                                           "circles"), f"comparison card #{e['rank']}"))
        plan["segments"][e["rank"]] = items

    outro = v.footage(script.get("outro_footage") or [], 1)
    plan["outro"] = outro or [v.original(graphics.outro_card(script.get("ending", ""), work / "outro.png"), "outro card")]
    plan["outro_has_footage"] = bool(outro)

    all_items = [*plan["intro"], *plan["outro"], *[i for items in plan["segments"].values() for i in items]]
    plan["unique_assets"] = len({i["path"] for i in all_items})
    plan["footage_clips"] = sum(1 for i in all_items if i.get("footage"))
    plan["footage_share"] = round(footage_entries / max(len(script["segments"]), 1), 2)
    plan["external_visuals"] = sum(1 for i in all_items if i["source"] != "original_graphics")
    logger.info(f"visual plan: {plan['footage_clips']} footage clips, footage share {plan['footage_share']}")
    return plan
