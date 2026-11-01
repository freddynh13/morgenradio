"""Morgenradio — dagens udsendelse fra ende til anden.

    python -m radio.main               # rigtig kørsel (kræver API-nøgler)
    python -m radio.main --dry-run     # test uden nøgler/netværk (falske data, testlyd)
    python -m radio.main --tjek-kilder # tjek kun at alle RSS-kilder svarer

Resultat i ./site (det publiceres til GitHub Pages):
    site/episodes/<dato>/show.json     rundown: klip og sange i rækkefølge + manuskript
    site/episodes/<dato>/clip_XX.mp3   værternes indslag
    site/episodes/<dato>/podcast.mp3   hele udsendelsen uden sange (til podcast-app)
    site/episodes/index.json           liste over udsendelser
    site/history.json                  spillede sange (undgår gentagelser)
    site/feed.xml                      podcast-RSS
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from . import audio, collect
from .util import ROOT, dansk_dato, env, load_config, log, now, read_json, setup_logging, write_json

SITE = ROOT / "site"
WEB = ROOT / "web"


# ── Dry-run: falske data så hele kæden kan testes uden nøgler ──────────────

def _fake_plan(cfg, kand):
    seg, brugt = [], set()
    for s in cfg["programflade"]:
        if s["type"] == "tale" and s.get("kategori"):
            ids = [k["id"] for k in kand.get(s["kategori"], []) if k["id"] not in brugt][: s["antal"]]
            brugt.update(ids)
            seg.append({"segment_id": s["id"], "historie_ids": ids, "begrundelse": "test"})
    sange = [{"segment_id": s["id"], "kobling": "",
              "forslag": [dict(zip(("kunstner", "titel"), r.split(" - ", 1))) for r in cfg["musik"]["reservesange"][i:i + 3]]}
             for i, s in enumerate(x for x in cfg["programflade"] if x["type"] == "sang")]
    return {"segmenter": seg, "sange": sange, "dagens_tema": "Testudsendelse"}


def _fake_sange(plan):
    return {s["segment_id"]: {"uri": f"spotify:track:TEST{i}", "name": s["forslag"][0]["titel"], "artist": s["forslag"][0]["kunstner"],
                              "album": "Test", "image": "", "duration_ms": 12000, "url": "", "kobling": ""}
            for i, s in enumerate(plan["sange"])}


def _fake_script(cfg, valgte, sange):
    v = [x["navn"] for x in cfg["vaerter"]]
    seg = []
    for s in cfg["programflade"]:
        if s["type"] != "tale":
            continue
        rep = [{"vaert": v[0], "tone": "glad", "tekst": f"Det her er segmentet {s['titel']}."}]
        for i, k in enumerate(valgte.get(s["id"], [])):
            rep.append({"vaert": v[(i + 1) % 2], "tone": "neutral", "tekst": k["hoved"]["titel"]})
        seg.append({"segment_id": s["id"], "replikker": rep})
    return {"titel": "Testudsendelse", "segmenter": seg}


def _fake_tts(replikker, mappe: Path):
    mappe.mkdir(parents=True, exist_ok=True)
    ud = []
    for i, r in enumerate(replikker):
        n = int(audio.SR * min(6, 0.3 * len(r["tekst"].split()) + 0.5))
        t = np.arange(n) / audio.SR
        f0 = 140 if i % 2 else 210
        x = 0.25 * np.sin(2 * np.pi * f0 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) ** 2
        p = mappe / f"l{i:03d}.wav"
        audio._skriv_wav(x.astype(np.float32), p)
        ud.append(p)
    return ud


# ── Podcast-RSS ─────────────────────────────────────────────────────────────

def skriv_feed(cfg, index, base_url: str):
    st = cfg["station"]
    items = []
    for ep in index[:30]:
        show = read_json(SITE / ep["path"] / "show.json", None)
        if not show or not show.get("podcast"):
            continue
        mp3 = SITE / ep["path"] / show["podcast"]
        sange = "\n".join(f"♪ {x['artist']} – {x['name']}" for x in show["items"] if x["type"] == "track")
        dt = datetime.fromisoformat(show["generated_at"])
        items.append(f"""<item><title>{escape(show['dato_tekst'].capitalize() + ': ' + show['title'])}</title>
<guid isPermaLink="false">{escape(ep['date'])}</guid><pubDate>{format_datetime(dt)}</pubDate>
<description>{escape('Dagens sange (spil dem på Spotify eller i radio-afspilleren):' + chr(10) + sange)}</description>
<enclosure url="{escape(f'{base_url}/{ep["path"]}/{show["podcast"]}')}" length="{mp3.stat().st_size if mp3.exists() else 0}" type="audio/mpeg"/>
<itunes:duration>{int(show.get('podcast_duration', 0))}</itunes:duration></item>""")
    (SITE / "feed.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>{escape(st['navn'])}</title><link>{escape(base_url)}/</link><language>da</language>
<description>{escape(st['slogan'])} — personlig AI-morgenradio.</description>
<itunes:block>Yes</itunes:block><itunes:explicit>false</itunes:explicit>
{''.join(items)}
</channel></rss>""", encoding="utf-8")


# ── Hovedforløb ─────────────────────────────────────────────────────────────

def koer(dry: bool = False, spring_over_hvis_findes: bool = False) -> Path | None:
    cfg = load_config()
    t0 = now(cfg)
    dato_iso, dato = t0.strftime("%Y-%m-%d"), dansk_dato(t0)
    log.info("=== %s — %s ===", cfg["station"]["navn"], dato)
    eksisterende = read_json(SITE / "episodes" / dato_iso / "show.json", None)
    if spring_over_hvis_findes and eksisterende and not eksisterende.get("test"):
        log.info("Dagens udsendelse findes allerede — intet at lave (sikkerhedskørsel).")
        return None

    historik = read_json(SITE / "history.json", [])
    graense = (t0 - timedelta(days=cfg["musik"]["undgaa_gentagelse_dage"])).strftime("%Y-%m-%d")
    nylig = [h for h in historik if h["date"] >= graense]
    spillet_uri = [h["uri"] for h in nylig]
    spillet_navne = [h["label"] for h in nylig]

    # 1. Indsaml
    if dry:
        artikler = read_json(ROOT / "tests" / "sample_articles.json", [])
        for a in artikler:
            a["tid"] = t0.isoformat()
        kildestatus = {"test": "ok"}
    else:
        artikler, kildestatus = collect.fetch_all(cfg)
    artikler = collect.filtrer(artikler, cfg)
    klynger = collect.klyng(artikler)
    kand = collect.kandidater(klynger, cfg)
    log.info("%d artikler → %d klynger → %s", len(artikler), len(klynger),
             ", ".join(f"{k}: {len(v)}" for k, v in kand.items()))
    if not any(kand.values()):
        raise SystemExit("Ingen nyheder fundet — tjek kilderne (python -m radio.main --tjek-kilder).")

    # 2. Vejr + plan
    if dry:
        vejr = "Vejret i testby: 10 grader og overskyet."
        plan = _fake_plan(cfg, kand)
    else:
        from .claude import lav_plan
        from .weather import hent_vejr
        vejr = hent_vejr(cfg)
        claude_key = env("ANTHROPIC_API_KEY")
        log.info("Claude planlægger udsendelsen …")
        plan = lav_plan(cfg, claude_key, kand, vejr, dato, spillet_navne)

    efter_id = {k["id"]: k for liste in kand.values() for k in liste}
    valgte: dict[str, list] = {}
    for s in plan["segmenter"]:
        valgte[s["segment_id"]] = [efter_id[i] for i in s["historie_ids"] if i in efter_id]
    alle_valgte = [k for v in valgte.values() for k in v]
    log.info("Plan: %s", "; ".join(f"{k}={len(v)}" for k, v in valgte.items()))

    # 3. Sange → Spotify
    if dry:
        sange = _fake_sange(plan)
    else:
        from .spotify import Spotify, vaelg_sange
        log.info("Slår sange op på Spotify …")
        sp = Spotify(env("SPOTIFY_CLIENT_ID"), env("SPOTIFY_CLIENT_SECRET"))
        sange = vaelg_sange(sp, plan, cfg, spillet_uri)

    # 4. Fuld artikeltekst + manuskript
    if dry:
        script = _fake_script(cfg, valgte, sange)
    else:
        collect.berig_med_fulltekst(alle_valgte, cfg["indsamling"]["fulltekst_tegn"])
        from .claude import skriv_manuskript
        log.info("Claude skriver manuskriptet …")
        script = skriv_manuskript(cfg, claude_key, valgte, sange, vejr, dato, plan.get("dagens_tema", ""))
    replikker_pr_seg = {s["segment_id"]: s["replikker"] for s in script["segmenter"]}
    vaert_navne = {v["navn"] for v in cfg["vaerter"]}
    for reps in replikker_pr_seg.values():
        for r in reps:
            if r["vaert"] not in vaert_navne:
                r["vaert"] = cfg["vaerter"][0]["navn"]
    ord_ = sum(len(r["tekst"].split()) for reps in replikker_pr_seg.values() for r in reps)
    log.info("Manuskript: %d replikker, %d ord (≈ %.1f min)", sum(map(len, replikker_pr_seg.values())), ord_, ord_ / 145)

    # 5. Tale → lyd (blokke pr. segment: én replik, eller flere i ét dialog-kald)
    segmenter = [(s["id"], replikker_pr_seg.get(s["id"], [])) for s in cfg["programflade"] if s["type"] == "tale"]
    tmp = Path(tempfile.mkdtemp(prefix="radio_"))
    if dry:
        alle = [r for _, reps in segmenter for r in reps]
        fil_for = {id(r): f for r, f in zip(alle, _fake_tts(alle, tmp))}
        blokke = {sid: [{"fil": fil_for[id(r)], "reps": [r], "vaert": r["vaert"]} for r in reps] for sid, reps in segmenter}
    else:
        from .tts import TTS
        log.info("Indtaler %d replikker …", sum(len(r) for _, r in segmenter))
        blokke = TTS(cfg).lav_blokke(segmenter, tmp)

    # 6. Byg rundown: sammenhængende talesegmenter bliver ét klip
    ep_dir = SITE / "episodes" / dato_iso
    if ep_dir.exists():
        shutil.rmtree(ep_dir)
    ep_dir.mkdir(parents=True)

    items, gruppe = [], []
    podcast_dele: list[np.ndarray] = []

    def luk_gruppe(sidste: bool):
        if not gruppe:
            return
        lyd, transcript, labels = [], [], []
        offset = 0.0
        for j, s in enumerate(gruppe):
            reps = replikker_pr_seg.get(s["id"], [])
            if not reps:
                continue
            forj = "intro" if (not items and j == 0) else "sweeper"
            efterj = "outro" if (sidste and j == len(gruppe) - 1) else None
            bl = blokke.get(s["id"], [])
            x, starter, varig = audio.byg_klip([b["fil"] for b in bl], [b["vaert"] for b in bl], forj, efterj,
                                               tekster=[b["reps"][0]["tekst"] if len(b["reps"]) == 1 else "" for b in bl],
                                               bund=cfg.get("musikbund", True) and s["id"] in ("intro", "outro"),
                                               seed=len(items) * 10 + j)
            for b, st, du in zip(bl, starter, varig):
                # i en dialog-blok fordeles starttiderne efter tekstlængde
                tegn = [len(r["tekst"]) for r in b["reps"]]
                acc = 0
                for r, t in zip(b["reps"], tegn):
                    transcript.append({"vaert": r["vaert"], "tekst": r["tekst"], "segment": s["id"],
                                       "start": round(offset + st + du * acc / max(1, sum(tegn)), 2)})
                    acc += t
            lyd.append(x)
            offset += len(x) / audio.SR
            labels.append(s["titel"])
        gruppe.clear()
        if not lyd:
            return
        x = np.concatenate(lyd)
        navn = f"clip_{sum(1 for i in items if i['type'] == 'clip'):02d}.mp3"
        dur = audio.skriv_mp3(x, ep_dir / navn)
        podcast_dele.append(x)
        items.append({"type": "clip", "src": navn, "duration": dur, "label": " · ".join(labels), "transcript": transcript})

    flade = cfg["programflade"]
    for idx, s in enumerate(flade):
        if s["type"] == "tale":
            gruppe.append(s)
        else:
            luk_gruppe(not any(x["type"] == "tale" for x in flade[idx + 1:]))
            if s["id"] in sange:
                t = sange[s["id"]]
                items.append({"type": "track", **{k: t[k] for k in ("uri", "name", "artist", "album", "image", "duration_ms", "url", "kobling")}})
    luk_gruppe(True)

    # Podcast-version: klip med en sweeper hvor sangene var
    sw = audio.jingle("sweeper") * 0.7
    pod = np.concatenate([d for x in podcast_dele for d in (x, np.zeros(int(0.3 * audio.SR)), sw, np.zeros(int(0.3 * audio.SR)))][:-3])
    pod_dur = audio.skriv_mp3(pod, ep_dir / "podcast.mp3", kbps=96)
    shutil.rmtree(tmp, ignore_errors=True)

    show = {
        "date": dato_iso, "dato_tekst": dato, "title": script["titel"], "station": cfg["station"]["navn"],
        "slogan": cfg["station"]["slogan"], "generated_at": t0.isoformat(timespec="seconds"),
        "tema": plan.get("dagens_tema", ""), "items": items, "podcast": "podcast.mp3", "podcast_duration": pod_dur,
        "sources": [{"segment": seg, "titel": k["hoved"]["titel"], "kilde": k["hoved"]["kilde"], "link": k["hoved"]["link"]}
                    for seg, liste in valgte.items() for k in liste],
        "kildestatus": kildestatus, "test": dry,
    }
    write_json(ep_dir / "show.json", show)

    # 7. Indeks, historik, oprydning, feed, afspiller
    index = [e for e in read_json(SITE / "episodes" / "index.json", []) if e["date"] != dato_iso]
    index.insert(0, {"date": dato_iso, "dato_tekst": dato, "title": script["titel"], "path": f"episodes/{dato_iso}"})
    behold = (t0 - timedelta(days=cfg["arkiv"]["behold_dage"])).strftime("%Y-%m-%d")
    for e in [e for e in index if e["date"] < behold]:
        shutil.rmtree(SITE / e["path"], ignore_errors=True)
    index = [e for e in index if e["date"] >= behold]
    write_json(SITE / "episodes" / "index.json", index)

    if not dry:
        historik = [h for h in historik if h["date"] >= (t0 - timedelta(days=365)).strftime("%Y-%m-%d") and h["date"] != dato_iso]
        historik += [{"date": dato_iso, "uri": t["uri"], "label": f"{t['artist']} - {t['name']}"} for t in sange.values()]
        write_json(SITE / "history.json", historik)

    for f in WEB.iterdir():
        if f.is_file():
            shutil.copy2(f, SITE / f.name)
    cid = env("SPOTIFY_CLIENT_ID", required=False)
    (SITE / "config.js").write_text(f'window.RADIO_CONFIG = {{ spotifyClientId: "{cid}" }};\n', encoding="utf-8")
    (SITE / ".nojekyll").write_text("")
    base = env("SITE_URL", required=False).rstrip("/") or "https://example.github.io/morgenradio"
    skriv_feed(cfg, index, base)

    total = sum(i["duration"] for i in items if i["type"] == "clip") + sum(i["duration_ms"] / 1000 for i in items if i["type"] == "track")
    log.info("Færdig: %d klip, %d sange, i alt ≈ %.1f min → %s", sum(i["type"] == "clip" for i in items),
             sum(i["type"] == "track" for i in items), total / 60, ep_dir)
    return ep_dir


def stemmeprove() -> None:
    """Kort prøve på værternes stemmer → site/stemmeprove.mp3 (rører ikke udsendelserne)."""
    cfg = load_config()
    a, b = (v["navn"] for v in cfg["vaerter"][:2])
    navn = (cfg.get("lytter") or {}).get("navn", "")
    by = cfg["station"]["by"]
    reps = [
        {"vaert": a, "tone": "glad", "regi": "", "tekst": f"God morgen, {navn}! Klokken er lidt over syv, og du lytter til {cfg['station']['navn']}."},
        {"vaert": b, "tone": "glad", "regi": "chuckles", "tekst": "Og ja, det er stadig mørkt udenfor. Jeg har allerede drukket to kopper kaffe."},
        {"vaert": a, "tone": "neutral", "regi": "playfully", "tekst": "To? Det er da ingenting. Hvordan ser vejret ud?"},
        {"vaert": b, "tone": "neutral", "regi": "", "tekst": f"Altså, i {by} bliver det gråt og omkring otte grader. Men det klarer op i eftermiddag."},
        {"vaert": a, "tone": "neutral", "regi": "surprised", "tekst": "Nej, seriøst? Sol i november?"},
        {"vaert": b, "tone": "energisk", "regi": "laughs", "tekst": "Jeg lover ingenting. Men vi starter med dagens nyheder, og så får du lidt musik bagefter."},
    ]
    from .tts import TTS
    tmp = Path(tempfile.mkdtemp(prefix="proeve_"))
    bl = TTS(cfg).lav_blokke([("proeve", reps)], tmp)["proeve"]
    x, _, _ = audio.byg_klip([b_["fil"] for b_ in bl], [b_["vaert"] for b_ in bl], "intro", None,
                             tekster=[b_["reps"][0]["tekst"] if len(b_["reps"]) == 1 else "" for b_ in bl], bund=True)
    SITE.mkdir(exist_ok=True)
    dur = audio.skriv_mp3(x, SITE / "stemmeprove.mp3")
    log.info("Stemmeprøve klar (%.0f sek.): %s/stemmeprove.mp3", dur, env("SITE_URL", required=False) or "site")


def main():
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tjek-kilder", action="store_true")
    ap.add_argument("--stemmeprove", action="store_true", help="Lav kun en kort prøve på stemmerne")
    ap.add_argument("--spring-over-hvis-findes", action="store_true",
                    help="Bruges af de planlagte kørsler: lav kun en udsendelse hvis dagens mangler")
    a = ap.parse_args()
    if a.tjek_kilder:
        _, status = collect.fetch_all(load_config())
        fejl = [k for k, v in status.items() if not v.startswith("ok") or v == "ok (0)"]
        print(f"\n{len(status) - len(fejl)} af {len(status)} kilder virker." + (f" Problemer: {', '.join(fejl)}" if fejl else ""))
        return
    if a.stemmeprove:
        return stemmeprove()
    koer(dry=a.dry_run, spring_over_hvis_findes=a.spring_over_hvis_findes)


if __name__ == "__main__":
    main()
