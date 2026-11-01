"""Tekst-til-tale. Én lydfil pr. replik.

To udbydere (vælges i config.yaml → tts.udbyder):
  - openai      gpt-4o-mini-tts. Billig. Styres med en stilinstruktion pr. replik.
  - elevenlabs  eleven_v3. Mest naturlig dansk tale, forstår lyd-tags som
                [laughs] og [excited]. Dyrere.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .util import env, log

TONER = {
    "neutral": "",
    "glad": "Lidt lysere og smilende stemme.",
    "alvorlig": "Dæmpet, alvorlig og respektfuld. Intet smil i stemmen. Lidt langsommere.",
    "energisk": "Ekstra energi og lidt højere tempo, som når man sætter gang i morgenen.",
    "rolig": "Rolig og afslappet.",
}
# Til ElevenLabs v3: tone → lyd-tag (engelske tags virker bedst)
EL_TAGS = {"glad": "[cheerfully]", "alvorlig": "[seriously]", "energisk": "[excited]", "rolig": "[calmly]"}
EL_REGI = {"griner": "[laughs]", "ler": "[laughs]", "overrask": "[surprised]", "drill": "[playfully]",
           "tænker": "[thoughtfully]", "nysgerrig": "[curious]", "sukker": "[sighs]", "hvisk": "[whispers]"}


class TTS:
    def __init__(self, cfg: dict):
        t = cfg.get("tts") or {}
        self.udbyder = t.get("udbyder", "openai")
        self.cfg = cfg
        self.base = cfg["stemme_instruktion"].strip()
        self.vaerter = {v["navn"]: v for v in cfg["vaerter"]}
        if self.udbyder == "elevenlabs":
            self.h = {"xi-api-key": env("ELEVENLABS_API_KEY"), "Content-Type": "application/json"}
            self.el_model = t.get("elevenlabs_model", "eleven_v3")
        else:
            self.h = {"Authorization": f"Bearer {env('OPENAI_API_KEY')}"}
            o = cfg.get("openai_tts", {})
            self.model = o.get("model", "gpt-4o-mini-tts")
            self.reserve = list(o.get("reserve_stemmer", []))
        log.info("Stemmer: %s", self.udbyder)

    # ── OpenAI ──
    def _openai(self, tekst: str, vaert: str, tone: str, regi: str, ud: Path) -> Path:
        v = self.vaerter.get(vaert) or next(iter(self.vaerter.values()))
        instr = " ".join(x for x in [
            self.base,
            f"Du er {v['navn']}, {v.get('personlighed', '')}",
            v.get("stemme_stil", ""),
            TONER.get(tone, ""),
            f"I denne replik: {regi}." if regi else "",
            "Lyd som et menneske der taler spontant i en radio — ikke som en der læser op. "
            "Naturlige mikropauser, let varierende tempo og tonefald, ingen overdreven betoning.",
        ] if x)
        stemme = v["stemme"]
        kandidater = [stemme] + [s for s in self.reserve if s != stemme]
        for forsøg in range(5):
            r = requests.post("https://api.openai.com/v1/audio/speech", headers=self.h, timeout=120, json={
                "model": self.model, "voice": kandidater[0], "input": tekst[:4000],
                "instructions": instr, "response_format": "wav",
            })
            if r.status_code == 200:
                ud.write_bytes(r.content)
                return ud
            if r.status_code == 400 and "voice" in r.text.lower() and len(kandidater) > 1:
                log.warning("Stemmen '%s' virker ikke — skifter til '%s'", kandidater[0], kandidater[1])
                v["stemme"] = kandidater[1]
                kandidater.pop(0)
                continue
            if r.status_code in (429, 500, 502, 503):
                time.sleep(5 * (forsøg + 1))
                continue
            raise RuntimeError(f"OpenAI TTS-fejl {r.status_code}: {r.text[:300]}")
        raise RuntimeError("OpenAI TTS svarede ikke efter flere forsøg")

    # ── ElevenLabs ──
    def _eleven(self, tekst: str, vaert: str, tone: str, regi: str, ud: Path) -> Path:
        v = self.vaerter.get(vaert) or next(iter(self.vaerter.values()))
        voice = v.get("elevenlabs_voice_id")
        if not voice:
            raise SystemExit(f"Værten {v['navn']} mangler elevenlabs_voice_id i config.yaml")
        tag = next((t for k, t in EL_REGI.items() if k in (regi or "").lower()), EL_TAGS.get(tone, ""))
        body = {"text": (f"{tag} " if tag and self.el_model == "eleven_v3" else "") + tekst,
                "model_id": self.el_model, "language_code": "da"}
        if self.el_model != "eleven_v3":
            body["voice_settings"] = {"stability": 0.4, "similarity_boost": 0.8, "style": 0.35, "use_speaker_boost": True}
        for forsøg in range(5):
            r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
                              headers=self.h, json=body, timeout=180)
            if r.status_code == 200:
                ud = ud.with_suffix(".mp3")
                ud.write_bytes(r.content)
                return ud
            if r.status_code == 400 and "language_code" in r.text:
                body.pop("language_code", None)
                continue
            if r.status_code in (429, 500, 502, 503):
                time.sleep(6 * (forsøg + 1))
                continue
            raise RuntimeError(f"ElevenLabs-fejl {r.status_code}: {r.text[:300]}")
        raise RuntimeError("ElevenLabs svarede ikke efter flere forsøg")

    def lav_alle(self, replikker: list[dict], mappe: Path) -> list[Path]:
        mappe.mkdir(parents=True, exist_ok=True)
        fn = self._eleven if self.udbyder == "elevenlabs" else self._openai
        jobs = [(r["tekst"], r["vaert"], r.get("tone", "neutral"), r.get("regi", ""), mappe / f"l{i:03d}.wav")
                for i, r in enumerate(replikker)]
        with ThreadPoolExecutor(max_workers=4 if self.udbyder == "elevenlabs" else 6) as pool:
            return list(pool.map(lambda j: fn(*j), jobs))
