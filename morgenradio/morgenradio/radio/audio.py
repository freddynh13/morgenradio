"""Lydsamling: jingler, replikker → klip, loudness-normalisering, MP3.

Alt mixes i numpy (44,1 kHz mono), og ffmpeg bruges til at læse/skrive filer
og til loudness-normalisering (EBU R128). Klippene normaliseres til -14 LUFS,
samme niveau som Spotify afspiller med, så der ikke er spring i lydstyrken
mellem værter og sange.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from .util import ROOT, log

SR = 44100
ASSETS = ROOT / "assets"
# Let "radio-behandling" af stemmerne: fjern rumlen, lidt varme, blid kompression.
# Får TTS-stemmerne til at lyde mere som en rigtig studiemikrofon.
RADIO_KAEDE = ("highpass=f=75,equalizer=f=160:t=q:w=1.2:g=1.5,equalizer=f=3200:t=q:w=1.5:g=1,"
               "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=150:makeup=2,")


# ── ffmpeg-hjælpere ─────────────────────────────────────────────────────────

def laes(path: Path) -> np.ndarray:
    """Vilkårlig lydfil → float32 mono 44,1 kHz."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def skriv_mp3(x: np.ndarray, ud: Path, lufs: float = -14.0, kbps: int = 128) -> float:
    """Normaliserer og gemmer som MP3. Returnerer længden i sekunder."""
    ud.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-af", f"{RADIO_KAEDE}loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample={SR}",
                    "-codec:a", "libmp3lame", "-b:a", f"{kbps}k", "-ac", "1", str(ud)],
                   input=pcm, check=True)
    return round(len(x) / SR, 2)


def _skriv_wav(x: np.ndarray, ud: Path) -> None:
    with wave.open(str(ud), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


# ── Jingler (genereres automatisk, kan erstattes med egne filer) ───────────

def _klokke(f: float, dur: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    env = np.exp(-t * 3.2) * np.minimum(1, t * 200)
    tone = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3.01 * f * t))
    return amp * env * tone


def _pad(fs: list[float], dur: float, amp: float = 0.08) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    env = np.minimum(1, t / 0.4) * np.minimum(1, (dur - t) / 0.8).clip(0)
    x = sum(np.sin(2 * np.pi * f * t + np.sin(2 * np.pi * 0.3 * t)) for f in fs)
    return amp * env * x / len(fs)


def _whoosh(dur: float, amp: float = 0.12) -> np.ndarray:
    n = int(SR * dur)
    rng = np.random.default_rng(7)
    støj = rng.standard_normal(n)
    # enkel lavpas der åbner sig: glidende gennemsnit med faldende vindue
    ud = np.zeros(n)
    for i, w in enumerate(np.linspace(40, 3, 12)):
        a, b = i * n // 12, (i + 1) * n // 12
        k = np.ones(int(w)) / int(w)
        ud[a:b] = np.convolve(støj[a:b], k, mode="same")
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return amp * env * ud / (np.abs(ud).max() + 1e-9)


def _placer(dst: np.ndarray, src: np.ndarray, start_s: float) -> None:
    a = int(start_s * SR)
    b = min(len(dst), a + len(src))
    dst[a:b] += src[: b - a]


def _syntetiser(navn: str) -> np.ndarray:
    C, E, G, A, C2, E2, G2 = 523.25, 659.25, 783.99, 880.0, 1046.5, 1318.5, 1568.0
    if navn == "intro":
        x = np.zeros(int(SR * 5.0))
        _placer(x, _whoosh(1.2), 0.0)
        for i, f in enumerate([C, E, G, C2, E2]):
            _placer(x, _klokke(f, 2.5, 0.22), 0.7 + i * 0.16)
        _placer(x, _klokke(G2, 3.0, 0.16), 0.7 + 5 * 0.16 + 0.1)
        _placer(x, _pad([261.6, 329.6, 392.0, 523.3], 4.2), 0.6)
    elif navn == "outro":
        x = np.zeros(int(SR * 4.0))
        for i, f in enumerate([G2, E2, C2, G, C]):
            _placer(x, _klokke(f, 2.6, 0.2), i * 0.2)
        _placer(x, _pad([261.6, 329.6, 392.0], 3.6), 0.2)
    else:  # sweeper: kort overgang mellem segmenter
        x = np.zeros(int(SR * 1.8))
        _placer(x, _whoosh(0.7, 0.1), 0.0)
        for i, f in enumerate([E, A, E2]):
            _placer(x, _klokke(f, 1.4, 0.2), 0.35 + i * 0.12)
    return 0.9 * x / (np.abs(x).max() + 1e-9)


JINGLE_DIR = ROOT / "site" / "jingles"   # ElevenLabs-genererede jingler (gemmes på gh-pages)


def _find_lyd(navn: str) -> Path | None:
    """Rækkefølge: dine egne filer (assets/jingle_<navn>.mp3) → ElevenLabs-jingler
    (site/jingles/<navn>.mp3) → de gamle genererede (assets/jingle_<navn>.wav)."""
    for p in (ASSETS / f"jingle_{navn}.mp3", JINGLE_DIR / f"{navn}.mp3", ASSETS / f"jingle_{navn}.wav"):
        if p.exists():
            return p
    return None


def jingle(navn: str) -> np.ndarray:
    p = _find_lyd(navn)
    if p:
        x = laes(p)
        return 0.9 * x / (np.abs(x).max() + 1e-9)
    x = _syntetiser(navn)
    ASSETS.mkdir(exist_ok=True)
    _skriv_wav(x, ASSETS / f"jingle_{navn}.wav")
    log.info("Genererede standard-jingle: assets/jingle_%s.wav", navn)
    return x


# ── Byg et klip ─────────────────────────────────────────────────────────────

def _trim(x: np.ndarray, thr: float = 0.004) -> np.ndarray:
    idx = np.where(np.abs(x) > thr)[0]
    return x[max(0, idx[0] - 400): idx[-1] + 1200] if len(idx) else x


def musikbund(sek: float) -> np.ndarray:
    """Musikbund under intro/outro. Bruger site/jingles/bund.mp3 (ElevenLabs) hvis
    den findes — looper den med crossfade — ellers en blød syntetisk akkordflade."""
    p = JINGLE_DIR / "bund.mp3"
    if p.exists():
        b = laes(p)
        n, xf = int(SR * sek), int(SR * 1.0)
        out = np.zeros(n + len(b), dtype=np.float32)
        pos = 0
        while pos < n:
            seg = b.copy()
            seg[:xf] *= np.linspace(0, 1, xf)
            seg[-xf:] *= np.linspace(1, 0, xf)
            out[pos:pos + len(seg)] += seg
            pos += len(seg) - xf
        out = out[:n]
        fade = int(SR * 1.5)
        out[:fade] *= np.linspace(0, 1, fade)
        out[-fade:] *= np.linspace(1, 0, fade)
        return 0.25 * out / (np.sqrt(np.mean(out ** 2)) + 1e-9)   # RMS-normaliseret
    akkorder = [[261.6, 329.6, 392.0], [220.0, 261.6, 329.6], [174.6, 220.0, 261.6], [196.0, 246.9, 293.7]]
    n = int(SR * sek)
    x = np.zeros(n, dtype=np.float32)
    seg = 4.0
    for i in range(int(sek / seg) + 1):
        p = _pad(akkorder[i % 4], seg + 1.0, 1.0)
        _placer(x, p, i * seg)
    fade = int(SR * 1.5)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    return x / (np.abs(x).max() + 1e-9)


def byg_klip(linjer: list[Path], vaerter: list[str], forjingle: str | None, efterjingle: str | None,
             tekster: list[str] | None = None, bund: bool = False, seed: int = 0) -> tuple[np.ndarray, list[float], list[float]]:
    """Sætter replikker sammen med naturlige, let varierende pauser. Korte
    reaktioner ("Ja, præcis.") overlapper en anelse, som når folk taler sammen.
    Jinglen overlapper starten af første replik let. Returnerer (lyd, starttider)."""
    rng = np.random.default_rng(seed)
    tekster = tekster or [""] * len(linjer)
    stykker, starter, varigheder = [], [], []
    pos = 0.0
    if forjingle:
        j = jingle(forjingle)
        stykker.append(j)
        pos = len(j) / SR
    tale = []
    forrige = None
    for p, v, t in zip(linjer, vaerter, tekster):
        x = _trim(laes(p))
        varigheder.append(round(len(x) / SR, 2))
        if forrige is None:
            pause = 0.0
        elif v == forrige:
            pause = 0.32 + rng.uniform(-0.05, 0.08)
        elif len(t.split()) <= 4:
            pause = rng.uniform(-0.12, 0.02)          # hurtig reaktion, let overlap
        else:
            pause = 0.16 + rng.uniform(-0.06, 0.10)
        tale.append((pause, x))
        forrige = v
    out_len = int(pos * SR) + sum(max(0, int(pa * SR)) + len(x) for pa, x in tale) + SR
    if efterjingle:
        ej = jingle(efterjingle)
        out_len += len(ej)
    y = np.zeros(out_len, dtype=np.float32)
    if forjingle:
        y[: len(stykker[0])] += stykker[0] * 0.8
        cur = int(max(0, pos - 0.9) * SR)  # tale starter ~0,9 s før jinglen er helt færdig
        y[cur:int(pos * SR)] *= np.linspace(1, 0.35, int(pos * SR) - cur)
    else:
        cur = int(0.15 * SR)
    tale_start = cur
    for pa, x in tale:
        cur = max(0, cur + int(pa * SR))
        starter.append(round(cur / SR, 2))
        y[cur:cur + len(x)] += x
        cur += len(x)
    tale_slut = cur
    if bund and tale_slut > tale_start:
        b = musikbund((tale_slut - tale_start) / SR + 2.0) * 0.045
        y[tale_start:tale_start + len(b)] += b[: len(y) - tale_start]
    if efterjingle:
        cur += int(0.25 * SR)
        y[cur:cur + len(ej)] += ej * 0.8
        cur += len(ej)
    return y[: cur + int(0.1 * SR)], starter, varigheder
