"""Tekst-til-tale.

Udbydere (config.yaml → tts.udbyder):
  - openai      gpt-4o-mini-tts. Billig. Én lydfil pr. replik.
  - elevenlabs  eleven_v3. Den mest naturlige danske tale.
                tts.elevenlabs_tilstand:
                  "dialog" (anbefalet) — Text to Dialogue-API'et indtaler flere
                      replikker i ét kald, så modellen selv laver naturlige
                      overgange, timing og reaktioner mellem værterne.
                  "replik" — én lydfil pr. replik (mere kontrol, mindre flow).
                Lyd-tags som [laughs] og [surprised] kommer fra manuskriptets "regi".
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
# Til ElevenLabs v3: tone → lyd-tag når manuskriptet ikke selv har en regi
EL_TAGS = {"alvorlig": "[serious]", "energisk": "[excited]"}
DIALOG_MAX_TEGN = 1800   # ElevenLabs anbefaler max ~2000 tegn pr. dialog-kald


def _el_tekst(r: dict) -> str:
    regi = (r.get("regi") or "").strip().strip("[]")
    tag = f"[{regi}]" if regi else EL_TAGS.get(r.get("tone", ""), "")
    return (tag + " " if tag else "") + r["tekst"]


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
    def _voice(self, vaert: str) -> str:
        v = self.vaerter.get(vaert) or next(iter(self.vaerter.values()))
        if not v.get("elevenlabs_voice_id"):
            raise SystemExit(f"Værten {v['navn']} mangler elevenlabs_voice_id i config.yaml")
        return v["elevenlabs_voice_id"]

    def _el_post(self, url: str, body: dict) -> requests.Response:
        for forsøg in range(5):
            r = requests.post(url, headers=self.h, json=body, timeout=300)
            if r.status_code == 200:
                return r
            if r.status_code in (400, 422) and "language_code" in r.text and "language_code" in body:
                body.pop("language_code")
                continue
            if r.status_code in (429, 500, 502, 503):
                time.sleep(8 * (forsøg + 1))
                continue
            hint = ""
            if r.status_code == 401:
                hint = " (forkert ELEVENLABS_API_KEY)"
            elif "paid_plan_required" in r.text or "library voices" in r.text:
                hint = (" (gratis-kontoen må ikke bruge stemmer fra Voice Library via API'et — opgradér til en "
                        "betalt plan, eller vælg en af ElevenLabs' standardstemmer)")
            elif "quota" in r.text.lower() or r.status_code == 402:
                hint = " (ikke flere ElevenLabs-credits denne måned — opgradér planen eller skift tts.udbyder til openai)"
            raise RuntimeError(f"ElevenLabs-fejl {r.status_code}{hint}: {r.text[:300]}")
        raise RuntimeError("ElevenLabs svarede ikke efter flere forsøg")

    def _eleven(self, tekst: str, vaert: str, tone: str, regi: str, ud: Path) -> Path:
        body = {"text": _el_tekst({"tekst": tekst, "tone": tone, "regi": regi}) if self.el_model == "eleven_v3" else tekst,
                "model_id": self.el_model, "language_code": "da"}
        r = self._el_post(f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice(vaert)}?output_format=mp3_44100_128", body)
        ud = ud.with_suffix(".mp3")
        ud.write_bytes(r.content)
        return ud

    def _dialog(self, reps: list[dict], ud: Path) -> Path:
        body = {"inputs": [{"text": _el_tekst(r), "voice_id": self._voice(r["vaert"])} for r in reps],
                "model_id": self.el_model, "language_code": "da"}
        r = self._el_post("https://api.elevenlabs.io/v1/text-to-dialogue?output_format=mp3_44100_128", body)
        rid = r.headers.get("request-id")
        if rid:
            self._req_ids.append(rid)
        ud.write_bytes(r.content)
        return ud

    # ── Fælles indgang: talelyd pr. segment ──
    def lav_blokke(self, segmenter: list[tuple[str, list[dict]]], mappe: Path) -> dict[str, list[dict]]:
        """Returnerer {segment_id: [{"fil", "reps", "vaert"}]}. En blok er enten én
        replik eller (i dialog-tilstand) flere replikker indtalt i ét stykke."""
        mappe.mkdir(parents=True, exist_ok=True)
        dialog = self.udbyder == "elevenlabs" and (self.cfg.get("tts") or {}).get("elevenlabs_tilstand", "dialog") == "dialog"
        ud: dict[str, list[dict]] = {}
        if not dialog:
            alle = [r for _, reps in segmenter for r in reps]
            filer = self.lav_alle(alle, mappe)
            fil_for = {id(r): f for r, f in zip(alle, filer)}
            for sid, reps in segmenter:
                ud[sid] = [{"fil": fil_for[id(r)], "reps": [r], "vaert": r["vaert"]} for r in reps]
            return ud
        self._req_ids: list[str] = []
        n = 0
        for sid, reps in segmenter:              # i rækkefølge, så sammenhængen bevares
            blokke, bunke, tegn = [], [], 0
            for r in reps + [None]:
                if r is None or (bunke and tegn + len(r["tekst"]) > DIALOG_MAX_TEGN):
                    if bunke:
                        f = self._dialog(bunke, mappe / f"d{n:03d}.mp3"); n += 1
                        blokke.append({"fil": f, "reps": bunke, "vaert": "dialog"})
                    bunke, tegn = [], 0
                if r is not None:
                    bunke.append(r); tegn += len(r["tekst"]) + 12
            ud[sid] = blokke
            log.info("  🎙 %s: %d replikker i %d dialog-kald", sid, len(reps), len(blokke))
        return ud

    def lav_alle(self, replikker: list[dict], mappe: Path) -> list[Path]:
        mappe.mkdir(parents=True, exist_ok=True)
        fn = self._eleven if self.udbyder == "elevenlabs" else self._openai
        jobs = [(r["tekst"], r["vaert"], r.get("tone", "neutral"), r.get("regi", ""), mappe / f"l{i:03d}.wav")
                for i, r in enumerate(replikker)]
        with ThreadPoolExecutor(max_workers=4 if self.udbyder == "elevenlabs" else 6) as pool:
            return list(pool.map(lambda j: fn(*j), jobs))
