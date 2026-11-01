"""Laver radioens jingler med ElevenLabs Music (én gang — de genbruges hver dag).

    python -m radio.main --lav-jingler

Filerne gemmes i site/jingles/ og følger med siden på gh-pages, så de bliver
liggende. Kør igen, hvis du vil have nye — eller ret stilen i config.yaml → jingler.
Pris: ca. 50 sekunders musik ≈ 1 krone (ElevenLabs Music, betalt plan).
"""
from __future__ import annotations

import time

import requests

from .audio import JINGLE_DIR
from .util import env, log

STANDARD = {
    "stil": ("Modern Scandinavian morning radio station branding. Bright, warm and optimistic "
             "synth-pop with crisp punchy drums, a bouncy bassline, shimmering plucks and a "
             "catchy 4-note signature melody. Polished, contemporary, professional broadcast "
             "mix. Not cheesy, not retro. Instrumental, no vocals."),
    "intro": ("A 9-second radio show opening jingle. Starts with a quick rising swoosh, the "
              "signature melody plays over a full energetic beat, and it ends on a clear, "
              "confident final hit with a short natural decay."),
    "sweeper": ("A 3-second transition sting between radio segments: a short whoosh into a "
                "single bright hit of the signature melody, clean ending."),
    "outro": ("A 7-second closing jingle for the end of the show: the signature melody, "
              "slightly slower and warmer, resolving to a satisfying final chord that rings out."),
    "bund": ("A 30-second calm, unobtrusive background music bed for radio hosts talking over "
             "it: soft steady beat, warm pads, light plucks, no melody that competes with speech, "
             "even dynamics, seamless and loopable."),
}
LAENGDER_MS = {"intro": 9000, "sweeper": 3500, "outro": 7000, "bund": 30000}


def lav_jingler(cfg: dict) -> None:
    j = {**STANDARD, **(cfg.get("jingler") or {})}
    key = env("ELEVENLABS_API_KEY")
    JINGLE_DIR.mkdir(parents=True, exist_ok=True)
    for navn, ms in LAENGDER_MS.items():
        prompt = f"{j['stil']} {j[navn]}"
        log.info("♫ Komponerer %s (%d sek.) …", navn, ms // 1000)
        for forsøg in range(4):
            r = requests.post("https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128", timeout=300,
                              headers={"xi-api-key": key, "Content-Type": "application/json"},
                              json={"prompt": prompt, "music_length_ms": ms, "force_instrumental": True})
            if r.status_code == 200:
                (JINGLE_DIR / f"{navn}.mp3").write_bytes(r.content)
                break
            if r.status_code in (429, 500, 502, 503):
                time.sleep(10 * (forsøg + 1))
                continue
            raise RuntimeError(f"ElevenLabs Music-fejl {r.status_code}: {r.text[:300]}")
    log.info("Jingler klar i site/jingles/ — de bruges fra næste udsendelse.")
