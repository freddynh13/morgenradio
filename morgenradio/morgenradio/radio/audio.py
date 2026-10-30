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
                    "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample={SR}",
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


def jingle(navn: str) -> np.ndarray:
    """Bruger assets/jingle_<navn>.(mp3|wav) hvis den findes, ellers genereres den."""
    for ext in ("mp3", "wav"):
        p = ASSETS / f"jingle_{navn}.{ext}"
        if p.exists():
            return laes(p)
    x = _syntetiser(navn)
    ASSETS.mkdir(exist_ok=True)
    _skriv_wav(x, ASSETS / f"jingle_{navn}.wav")
    log.info("Genererede standard-jingle: assets/jingle_%s.wav", navn)
    return x


# ── Byg et klip ─────────────────────────────────────────────────────────────

def _trim(x: np.ndarray, thr: float = 0.004) -> np.ndarray:
    idx = np.where(np.abs(x) > thr)[0]
    return x[max(0, idx[0] - 400): idx[-1] + 1200] if len(idx) else x


def byg_klip(linjer: list[Path], vaerter: list[str], forjingle: str | None, efterjingle: str | None) -> tuple[np.ndarray, list[float]]:
    """Sætter replikker sammen med naturlige pauser. Jinglen overlapper starten
    af første replik let (som i rigtig radio). Returnerer (lyd, starttider pr. replik)."""
    stykker, starter = [], []
    pos = 0.0
    if forjingle:
        j = jingle(forjingle)
        stykker.append(j)
        pos = len(j) / SR
    tale = []
    forrige = None
    for p, v in zip(linjer, vaerter):
        x = _trim(laes(p))
        pause = 0.0 if forrige is None else (0.22 if v != forrige else 0.38)
        tale.append((pause, x))
        forrige = v
    out_len = int(pos * SR) + sum(int(pa * SR) + len(x) for pa, x in tale) + SR
    if efterjingle:
        ej = jingle(efterjingle)
        out_len += len(ej)
    y = np.zeros(out_len, dtype=np.float32)
    if forjingle:
        y[: len(stykker[0])] += stykker[0] * 0.8
        cur = int(max(0, pos - 0.9) * SR)  # tale starter ~0,9 s før jinglen er helt færdig
        # dæmp jinglens hale under talen
        y[cur:int(pos * SR)] *= np.linspace(1, 0.35, int(pos * SR) - cur)
    else:
        cur = int(0.15 * SR)
    for pa, x in tale:
        cur += int(pa * SR)
        starter.append(round(cur / SR, 2))
        y[cur:cur + len(x)] += x
        cur += len(x)
    if efterjingle:
        cur += int(0.25 * SR)
        y[cur:cur + len(ej)] += ej * 0.8
        cur += len(ej)
    return y[: cur + int(0.1 * SR)], starter
