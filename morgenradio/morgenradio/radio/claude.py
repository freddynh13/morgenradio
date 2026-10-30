"""Claude som redaktør (trin 1: plan) og tekstforfatter (trin 2: manuskript).

To kald, ligesom dit nyhedsbrev v2 (udkast → endeligt brev):
  1. PLAN: vælg historier pr. segment + foreslå sange (med alternativer).
     Sangene slås op på Spotify MELLEM de to kald, så værterne aldrig
     præsenterer en sang, der ikke findes eller ikke bliver spillet.
  2. MANUSKRIPT: skriv det talte manuskript med de bekræftede sange.
Begge bruger structured outputs (json_schema), så svaret altid kan parses.
"""
from __future__ import annotations

import json
import time

import requests

from .collect import som_tekst
from .util import log


def _lytter(cfg: dict) -> str:
    l = cfg.get("lytter") or {}
    if not l:
        return f"Lytteren bor i {cfg['station']['by']}."
    return (f"Lytteren hedder {l.get('navn', '')}, bor i {l.get('bor', cfg['station']['by'])} og arbejder med "
            f"{l.get('arbejde', '')}. {str(l.get('om_mig', '')).strip()}")

API = "https://api.anthropic.com/v1/messages"


def _kald(api_key: str, model: str, max_tokens: int, system: str, prompt: str, schema: dict) -> dict:
    body = {
        "model": model, "max_tokens": max_tokens, "system": system,
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    for forsøg in range(4):
        r = requests.post(API, headers=headers, json=body, timeout=600)
        if r.status_code in (429, 500, 502, 503, 529):
            vent = 15 * (forsøg + 1)
            log.warning("Claude svarede %s — prøver igen om %ss", r.status_code, vent)
            time.sleep(vent)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Claude-fejl {r.status_code}: {r.text[:500]}")
        data = r.json()
        tekst = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if data.get("stop_reason") == "max_tokens":
            raise RuntimeError("Claude løb tør for tokens — hæv claude.max_tokens i config.yaml")
        u = data.get("usage", {})
        log.info("Claude: %s input / %s output tokens", u.get("input_tokens"), u.get("output_tokens"))
        return json.loads(tekst)
    raise RuntimeError("Claude svarede ikke efter 4 forsøg")


# ── Trin 1: plan ───────────────────────────────────────────────────────────

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["segmenter", "sange", "dagens_tema"],
    "properties": {
        "dagens_tema": {"type": "string", "description": "Én kort sætning om hvad der fylder mest i dag"},
        "segmenter": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["segment_id", "historie_ids", "begrundelse"],
                "properties": {
                    "segment_id": {"type": "string"},
                    "historie_ids": {"type": "array", "items": {"type": "string"}},
                    "begrundelse": {"type": "string"},
                },
            },
        },
        "sange": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["segment_id", "forslag", "kobling"],
                "properties": {
                    "segment_id": {"type": "string"},
                    "forslag": {
                        "type": "array", "description": "3 forslag i prioriteret rækkefølge",
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["kunstner", "titel"],
                            "properties": {"kunstner": {"type": "string"}, "titel": {"type": "string"}},
                        },
                    },
                    "kobling": {"type": "string", "description": "Hvorfor netop denne sang her — fx kobling til en musiknyhed, vejret eller stemningen. Tom streng hvis ingen."},
                },
            },
        },
    },
}


def lav_plan(cfg: dict, api_key: str, kand: dict, vejr: str, dato: str, spillet: list[str]) -> dict:
    tale = [s for s in cfg["programflade"] if s["type"] == "tale" and s.get("kategori")]
    sange = [s for s in cfg["programflade"] if s["type"] == "sang"]
    blokke = []
    for kat, liste in kand.items():
        blokke.append(f"=== KANDIDATER: {kat.upper()} ({len(liste)}) ===\n" + "\n".join(som_tekst(k) for k in liste))

    i = cfg["interesser"]
    prompt = f"""Du er redaktør på en personlig dansk morgenradio, "{cfg['station']['navn']}". I dag er det {dato}.
Radioen laves til én bestemt person. {_lytter(cfg)}

LYTTERENS INTERESSER
- Nyheder: {', '.join(i['nyheder_fokus'])}
- Sport: {', '.join(i['sport_fokus'])}
- Musiksmag: {cfg['musik']['smag'].strip()}

OPGAVE 1 — VÆLG HISTORIER til hvert talesegment:
{chr(10).join(f"- {s['id']} ({s['titel']}): højst {s['antal']} historier fra kategorien '{s['kategori']}'" for s in tale)}
Regler:
- Vælg det vigtigste og mest interessante for en dansker i dag. Flere uafhængige kilder = vigtigere.
- Nyheder: bland store danske og internationale historier. Ingen historie må optræde to gange.
- Segmentet 'aarhus' skal være lokale nyheder fra Aarhus/Østjylland. Er der intet relevant lokalt, så vælg 0.
- Har lytterens fag (digital marketing/AI) en vigtig nyhed i dag, så tag den gerne med i 'nyheder'.
- Segmentet 'let' skal være en positiv, sjov eller kuriøs historie (ikke en af dem i 'nyheder').
- Sport: resultater og kampe fra det seneste døgn + vigtige kampe i dag. Prioriter lytterens fokus, men den største sportshistorie kommer med uanset.
- Musik: nye udgivelser, koncerter, danske musiknyheder.
- Hellere færre gode historier end fyld. Vælg kun id'er fra listerne.

OPGAVE 2 — FORESLÅ SANGE til sangpladserne: {', '.join(s['id'] for s in sange)}
- 3 forslag pr. plads (kunstner + præcis titel som på Spotify). Kun rigtige, udgivne numre.
- Ca. {int(cfg['musik']['andel_danske'] * 100)} % danske numre over hele udsendelsen. Varier tempo og genre; første sang skal vække.
- {', '.join(f"Plads {s['id']} skal gerne kobles til segmentet '{s['gerne_koblet_til']}' (fx en kunstner der er nævnt i musiknyt)" for s in sange if s.get('gerne_koblet_til')) or 'Ingen faste koblinger.'}
- Spil IKKE disse, de er spillet for nylig: {', '.join(spillet[-80:]) or 'ingen'}
- Passer det med dagens vejr eller en nyhed, må du gerne koble — men kun hvis det er naturligt, aldrig ved alvorlige/tragiske nyheder.

VEJRET: {vejr}

{chr(10).join(blokke)}
"""
    return _kald(api_key, cfg["claude"]["model"], cfg["claude"]["max_tokens"],
                 "Du er en skarp, erfaren dansk radioredaktør. Du svarer kun med JSON efter skemaet.",
                 prompt, PLAN_SCHEMA)


# ── Trin 2: manuskript ─────────────────────────────────────────────────────

SCRIPT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["titel", "segmenter"],
    "properties": {
        "titel": {"type": "string", "description": "Kort titel på dagens udsendelse, fx 'Folketingsvalg, FCK-sejr og ny Medina'"},
        "segmenter": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["segment_id", "replikker"],
                "properties": {
                    "segment_id": {"type": "string"},
                    "replikker": {
                        "type": "array",
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["vaert", "tekst", "tone"],
                            "properties": {
                                "vaert": {"type": "string"},
                                "tekst": {"type": "string"},
                                "tone": {"type": "string", "enum": ["neutral", "glad", "alvorlig", "energisk", "rolig"]},
                            },
                        },
                    },
                },
            },
        },
    },
}


def skriv_manuskript(cfg: dict, api_key: str, valgte: dict, sange: dict, vejr: str, dato: str, tema: str) -> dict:
    v = cfg["vaerter"]
    flade = cfg["programflade"]
    ord_i_alt = int(cfg["tale_minutter"] * 145)  # ~145 ord/min i radiotempo

    gang = []
    for idx, s in enumerate(flade):
        if s["type"] == "sang":
            sang = sange.get(s["id"])
            gang.append(f"  ♪ {s['id']}: SANG — {sang['artist']} – \"{sang['name']}\"" + (f" (kobling: {sang['kobling']})" if sang.get("kobling") else "") if sang else f"  ♪ {s['id']}: (ingen sang)")
            continue
        næste = next((x for x in flade[idx + 1:] if x["type"] == "sang"), None)
        nste_sang = sange.get(næste["id"]) if næste and flade[idx + 1]["type"] == "sang" else None
        linje = f"  🎙 {s['id']}: {s['titel']}"
        if nste_sang:
            linje += f"  → slut med at præsentere næste sang: {nste_sang['artist']} – \"{nste_sang['name']}\""
        gang.append(linje)
        for k in valgte.get(s["id"], []):
            gang.append("      " + som_tekst(k, med_fulltekst=True).replace("\n", "\n      "))

    prompt = f"""Skriv det talte manuskript til dagens udsendelse af "{cfg['station']['navn']}" ({cfg['station']['slogan']}).
I dag er det {dato}. Dagens tema: {tema}

LYTTEREN
{_lytter(cfg)}
Udsendelsen er lavet KUN til denne person, og det skal kunne mærkes: værterne hilser på {(cfg.get('lytter') or {}).get('navn', 'lytteren')} ved navn i intro og outro og højst 1-2 gange undervejs. De kobler gerne en historie til lytterens hverdag (fx "det her kan du nok bruge på arbejdet", eller vejret i Aarhus til cykelturen), men kun når det er naturligt og ikke ved alvorlige nyheder. Det må ikke blive klæbrigt.

VÆRTER
- {v[0]['navn']}: {v[0]['personlighed']}
- {v[1]['navn']}: {v[1]['personlighed']}

PROGRAMMET (rækkefølgen er fast; skriv replikker for hvert 🎙-segment, IKKE for ♪-sange):
{chr(10).join(gang)}

VEJRET (bruges i intro): {vejr}

SÅDAN SKAL DET LYDE
- Ægte dansk morgenradio: to værter der taler med hinanden, korte replikker (1-3 sætninger), naturlige overgange, lidt humor og personlighed. Ingen oplæsning af lister.
- Samlet ca. {ord_i_alt} ord tale (± 15 %). Fordel efter vigtighed; intro og outro er korte.
- Intro: hils på lytteren ved navn, sig dato og stationsnavn, kort teaser for dagens største historier, så vejret.
- Skriv til ØRET: ingen URL'er, ingen parenteser, ingen forkortelser (skriv "for eksempel", "procent", "millioner kroner"), ingen emojis, ingen overskrifter. Datoer og tal skrives så de læses naturligt op.
- Nævn kilden mundret når det er relevant ("ifølge DR").
- FAKTA: brug KUN oplysninger fra materialet ovenfor. Find aldrig på tal, citater, resultater eller navne. Er noget uklart, så sig det forsigtigt eller udelad det. Sportsresultater skal stå præcist som i kilden.
- Alvorlige nyheder (død, krig, ulykker): alvorlig tone, ingen jokes, ingen sangkobling.
- Før en sang: præsenter den kort og naturligt, præcis med den kunstner og titel der står ovenfor. Efter en sang må næste segment gerne starte med en kort kommentar til sangen.
- Outro: tak for i dag, ønsk en god {dato.split()[0]}, og præsenter sidste sang.
- tone-feltet styrer stemmen: brug alvorlig ved tunge nyheder, glad/energisk ved lette emner.
- vaert skal være præcis "{v[0]['navn']}" eller "{v[1]['navn']}".
"""
    return _kald(api_key, cfg["claude"]["model"], cfg["claude"]["max_tokens"],
                 "Du er en prisvindende dansk radiotekstforfatter. Du svarer kun med JSON efter skemaet.",
                 prompt, SCRIPT_SCHEMA)
