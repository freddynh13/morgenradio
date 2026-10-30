"""Tekst-til-tale med OpenAI (gpt-4o-mini-tts). Én lydfil pr. replik."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .util import log

API = "https://api.openai.com/v1/audio/speech"

TONER = {
    "neutral": "",
    "glad": "Lidt lysere og smilende stemme.",
    "alvorlig": "Dæmpet, alvorlig og respektfuld. Intet smil i stemmen.",
    "energisk": "Ekstra energi og tempo, som når man sætter gang i morgenen.",
    "rolig": "Rolig og afslappet.",
}


class TTS:
    def __init__(self, api_key: str, cfg: dict):
        self.h = {"Authorization": f"Bearer {api_key}"}
        self.model = cfg["openai_tts"]["model"]
        self.reserve = list(cfg["openai_tts"].get("reserve_stemmer", []))
        self.base = cfg["stemme_instruktion"].strip()
        self.stemmer = {v["navn"]: v["stemme"] for v in cfg["vaerter"]}
        self.personer = {v["navn"]: v["personlighed"] for v in cfg["vaerter"]}

    def _en(self, tekst: str, vaert: str, tone: str, ud: Path) -> Path:
        stemme = self.stemmer.get(vaert) or next(iter(self.stemmer.values()))
        instr = f"{self.base} Du er {vaert}: {self.personer.get(vaert, '')} {TONER.get(tone, '')}".strip()
        kandidater = [stemme] + [s for s in self.reserve if s != stemme]
        for forsøg in range(5):
            r = requests.post(API, headers=self.h, timeout=120, json={
                "model": self.model, "voice": kandidater[0], "input": tekst[:4000],
                "instructions": instr, "response_format": "wav",
            })
            if r.status_code == 200:
                ud.write_bytes(r.content)
                return ud
            if r.status_code == 400 and "voice" in r.text.lower() and len(kandidater) > 1:
                log.warning("Stemmen '%s' virker ikke — skifter til '%s'", kandidater[0], kandidater[1])
                self.stemmer[vaert] = kandidater[1]
                kandidater.pop(0)
                continue
            if r.status_code in (429, 500, 502, 503):
                time.sleep(5 * (forsøg + 1))
                continue
            raise RuntimeError(f"OpenAI TTS-fejl {r.status_code}: {r.text[:300]}")
        raise RuntimeError("OpenAI TTS svarede ikke efter flere forsøg")

    def lav_alle(self, replikker: list[dict], mappe: Path) -> list[Path]:
        mappe.mkdir(parents=True, exist_ok=True)
        jobs = [(r["tekst"], r["vaert"], r.get("tone", "neutral"), mappe / f"l{i:03d}.wav") for i, r in enumerate(replikker)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(lambda j: self._en(*j), jobs))
