"""Slår Claudes sangforslag op på Spotify (Client Credentials — ingen login).

Bemærk Spotifys regler for Development Mode-apps fra februar 2026: søgning
returnerer højst 10 resultater pr. kald, og /browse/new-releases er fjernet.
Vi bruger derfor kun GET /search med limit=5.
"""
from __future__ import annotations

import base64
import random
import re
import unicodedata
from difflib import SequenceMatcher

import requests

from .util import log


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = re.sub(r"\(.*?\)|\[.*?\]| - .*(remaster|version|edit|mix).*", "", s)
    return re.sub(r"[^a-z0-9æøå ]", "", s).strip()


class Spotify:
    def __init__(self, client_id: str, client_secret: str):
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        r = requests.post("https://accounts.spotify.com/api/token", timeout=20,
                          headers={"Authorization": f"Basic {auth}"}, data={"grant_type": "client_credentials"})
        r.raise_for_status()
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def _search(self, q: str) -> list[dict]:
        r = requests.get("https://api.spotify.com/v1/search", headers=self.h, timeout=20,
                         params={"q": q, "type": "track", "limit": 5, "market": "DK"})
        if r.status_code != 200:
            log.warning("Spotify-søgning fejlede (%s): %s", r.status_code, r.text[:200])
            return []
        return r.json().get("tracks", {}).get("items", [])

    def find(self, kunstner: str, titel: str) -> dict | None:
        for q in (f'track:"{titel}" artist:"{kunstner}"', f"{kunstner} {titel}"):
            bedst, score = None, 0.0
            for t in self._search(q):
                a = SequenceMatcher(None, _norm(kunstner), _norm(", ".join(x["name"] for x in t["artists"]))).ratio()
                b = SequenceMatcher(None, _norm(titel), _norm(t["name"])).ratio()
                s = 0.45 * a + 0.55 * b
                if _norm(kunstner) in _norm(" ".join(x["name"] for x in t["artists"])):
                    s += 0.15
                if s > score:
                    bedst, score = t, s
            if bedst and score >= 0.72:
                img = (bedst["album"].get("images") or [{}])[0].get("url", "")
                return {
                    "uri": bedst["uri"], "name": bedst["name"],
                    "artist": ", ".join(x["name"] for x in bedst["artists"]),
                    "album": bedst["album"]["name"], "image": img,
                    "duration_ms": bedst["duration_ms"],
                    "url": bedst.get("external_urls", {}).get("spotify", ""),
                }
        return None


def vaelg_sange(sp: Spotify, plan: dict, cfg: dict, spillet: list[str]) -> dict[str, dict]:
    """Første forslag der findes og ikke er spillet for nylig. Ellers reservesang."""
    brugt = set(spillet)
    ud: dict[str, dict] = {}
    reserver = list(cfg["musik"]["reservesange"])
    random.shuffle(reserver)
    for slot in plan["sange"]:
        valgt = None
        for f in slot["forslag"]:
            t = sp.find(f["kunstner"], f["titel"])
            if t and t["uri"] not in brugt:
                valgt = t
                break
            log.info("  ✗ %s – %s %s", f["kunstner"], f["titel"], "(spillet for nylig)" if t else "(ikke fundet)")
        if not valgt:
            for r in reserver:
                k, _, ti = r.partition(" - ")
                t = sp.find(k, ti)
                if t and t["uri"] not in brugt:
                    valgt = t
                    slot["kobling"] = ""
                    break
        if valgt:
            valgt["kobling"] = slot.get("kobling", "")
            brugt.add(valgt["uri"])
            ud[slot["segment_id"]] = valgt
            log.info("  ♪ %s: %s – %s", slot["segment_id"], valgt["artist"], valgt["name"])
    return ud
