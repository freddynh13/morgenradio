"""Indsamling af nyheder: RSS → filtrering → klyngning → rangering.

Samme idé som i dit nyhedsbrev-script: mange kilder dækker samme historie, så
artiklerne samles i klynger, og en klynge med flere uafhængige kilder er
vigtigere end én enlig artikel. Vægten (weight) afgør kun hvilket link/hvilken
tekst vi foretrækker, når flere kilder har samme historie.
"""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from .util import log

UA = "Mozilla/5.0 (Morgenradio; personlig nyhedslæser) Gecko/20100101 Firefox/128.0"

STOPORD = set("""
og i at er en et den det de der til på med for af som har ikke om var men fra kan vil
skal efter nu nye ny over under ved så sig også han hun man efter mod bliver blev
efter mere meget end alle hvor hvad hvis eller to tre første siger får fik får sin sine
the a an of to in on for and or is are was with at by from as after new says will its
be has have it this that his her their over into up out about
""".split())


# ── Indsamling ──────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _fetch_feed(kategori: str, kilde: dict, max_items: int) -> tuple[list[dict], str]:
    try:
        r = requests.get(kilde["url"], headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as e:  # én kilde der fejler, må aldrig vælte udsendelsen
        return [], f"FEJL ({type(e).__name__}: {str(e)[:80]})"

    items = []
    for e in feed.entries[:max_items]:
        t = e.get("published_parsed") or e.get("updated_parsed")
        dt = datetime(*t[:6], tzinfo=timezone.utc) if t else None
        title = _clean(e.get("title", ""))
        # Google News sætter " - Mediets navn" bag på titlen
        medie = None
        if "news.google." in kilde["url"] and " - " in title:
            title, medie = title.rsplit(" - ", 1)
        if not title:
            continue
        items.append({
            "kategori": kategori,
            "kilde": medie or kilde["navn"],
            "feed": kilde["navn"],
            "weight": kilde.get("weight", 1),
            "fulltekst": kilde.get("fulltekst", False) and "news.google." not in kilde["url"],
            "titel": title,
            "resume": _clean(e.get("summary", ""))[:500],
            "link": e.get("link", ""),
            "tid": dt.isoformat() if dt else None,
        })
    return items, f"ok ({len(items)})"


def fetch_all(cfg: dict) -> tuple[list[dict], dict]:
    """Henter alle kilder parallelt. Returnerer (artikler, status pr. kilde)."""
    ind = cfg["indsamling"]
    jobs = [(kat, k) for kat, kilder in cfg["kilder"].items() for k in kilder]
    status, artikler = {}, []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for (kat, k), (items, st) in zip(jobs, pool.map(lambda j: _fetch_feed(j[0], j[1], ind["max_pr_kilde"]), jobs)):
            status[f"{kat}/{k['navn']}"] = st
            artikler.extend(items)
    for navn, st in status.items():
        (log.info if st.startswith("ok") else log.warning)("Kilde %-32s %s", navn, st)
    return artikler, status


# ── Filtrering ─────────────────────────────────────────────────────────────

def filtrer(artikler: list[dict], cfg: dict) -> list[dict]:
    graense = datetime.now(timezone.utc) - timedelta(hours=cfg["indsamling"]["timer_tilbage"])
    undgaa = [w.lower() for w in cfg["interesser"].get("undgaa_emner", []) if w.strip()]
    mønstre = [re.compile(r"(?<!\w)" + re.escape(w) + r"(?!\w)", re.I) for w in undgaa]
    ud, sete = [], set()
    for a in artikler:
        if a["tid"] and datetime.fromisoformat(a["tid"]) < graense:
            continue
        tekst = f"{a['titel']} {a['resume']}"
        if any(m.search(tekst) for m in mønstre):
            continue
        key = (a["kategori"], a["titel"].lower())
        if key in sete:
            continue
        sete.add(key)
        ud.append(a)
    return ud


# ── Klyngning ──────────────────────────────────────────────────────────────

def _tokens(text: str) -> set[str]:
    ord_ = re.findall(r"[a-zæøåäöüé0-9]+", text.lower())
    return {w[:7] for w in ord_ if len(w) > 2 and w not in STOPORD}  # [:7] ≈ simpel stemming


def klyng(artikler: list[dict], taerskel: float = 0.34) -> list[dict]:
    """Grådig klyngning på overlap i titel+resumé-ord (pr. kategori)."""
    klynger: list[dict] = []
    for a in sorted(artikler, key=lambda x: -x["weight"]):
        tok = _tokens(a["titel"] + " " + a["resume"][:200])
        if len(tok) < 2:
            continue
        bedst, bedst_score = None, 0.0
        for k in klynger:
            if k["kategori"] != a["kategori"]:
                continue
            fælles = len(tok & k["_tok"])
            score = fælles / max(1, min(len(tok), len(k["_tok"])))
            if fælles >= 2 and score > bedst_score:
                bedst, bedst_score = k, score
        if bedst and bedst_score >= taerskel:
            bedst["artikler"].append(a)
            bedst["_tok"] |= tok
        else:
            klynger.append({"kategori": a["kategori"], "artikler": [a], "_tok": set(tok)})

    nu = datetime.now(timezone.utc)
    for k in klynger:
        kilder = {a["kilde"] for a in k["artikler"]}
        nyeste = max((datetime.fromisoformat(a["tid"]) for a in k["artikler"] if a["tid"]), default=nu - timedelta(hours=12))
        timer = (nu - nyeste).total_seconds() / 3600
        k["uafhaengige_kilder"] = len(kilder)
        k["styrke"] = round(len(kilder) * 2 + max(a["weight"] for a in k["artikler"]) + max(0, 6 - timer / 4), 2)
        k["hoved"] = max(k["artikler"], key=lambda a: (a["weight"], len(a["resume"])))
        del k["_tok"]
    klynger.sort(key=lambda k: -k["styrke"])
    return klynger


def kandidater(klynger: list[dict], cfg: dict) -> dict[str, list[dict]]:
    """Top-N klynger pr. kategori med et kort id, klar til Claude."""
    n = cfg["indsamling"]["max_kandidater_pr_kategori"]
    ud: dict[str, list[dict]] = {}
    for kat in cfg["kilder"]:
        liste = [k for k in klynger if k["kategori"] == kat][:n]
        for i, k in enumerate(liste):
            k["id"] = f"{kat[:2].upper()}{i + 1}"
        ud[kat] = liste
    return ud


# ── Fuld artikeltekst til de valgte historier ──────────────────────────────

def _hent_tekst(url: str, max_tegn: int) -> str:
    try:
        import trafilatura
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
        txt = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
        return txt[:max_tegn]
    except Exception:
        return ""


def berig_med_fulltekst(klynger: list[dict], max_tegn: int) -> None:
    """Henter brødtekst for de valgte klynger (bedste kilde med fulltekst=true)."""
    def job(k):
        for a in sorted(k["artikler"], key=lambda a: -a["weight"]):
            if a["fulltekst"] and a["link"]:
                txt = _hent_tekst(a["link"], max_tegn)
                if len(txt) > 200:
                    return txt
        return ""
    with ThreadPoolExecutor(max_workers=8) as pool:
        for k, txt in zip(klynger, pool.map(job, klynger)):
            k["fulltekst"] = txt


def som_tekst(k: dict, med_fulltekst: bool = False) -> str:
    """Kompakt tekstblok om en klynge til prompten."""
    linjer = [f"[{k['id']}] {k['hoved']['titel']}  (kilder: {k['uafhaengige_kilder']}, styrke: {k['styrke']})"]
    for a in k["artikler"][:4]:
        linjer.append(f"   - {a['kilde']}: {a['titel']}" + (f" — {a['resume'][:220]}" if a["resume"] else ""))
    if med_fulltekst and k.get("fulltekst"):
        linjer.append("   ARTIKELTEKST: " + re.sub(r"\s+", " ", k["fulltekst"]))
    return "\n".join(linjer)
