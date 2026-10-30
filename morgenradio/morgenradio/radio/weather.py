"""Dagens vejr fra Open-Meteo (gratis, ingen nøgle)."""
from __future__ import annotations

import requests

from .util import log

WMO = {
    0: "skyfri himmel", 1: "mest klart", 2: "skiftende skydække", 3: "overskyet",
    45: "tåge", 48: "rimtåge", 51: "let støvregn", 53: "støvregn", 55: "kraftig støvregn",
    61: "let regn", 63: "regn", 65: "kraftig regn", 66: "isslag", 67: "kraftigt isslag",
    71: "let sne", 73: "sne", 75: "kraftigt snefald", 77: "snefnug",
    80: "regnbyger", 81: "kraftige regnbyger", 82: "voldsomme regnbyger",
    85: "snebyger", 86: "kraftige snebyger", 95: "torden", 96: "torden med hagl", 99: "kraftig torden med hagl",
}


def hent_vejr(cfg: dict) -> str:
    st = cfg["station"]
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
            "latitude": st["breddegrad"], "longitude": st["laengdegrad"], "timezone": st["tidszone"],
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max,sunrise,sunset",
            "current": "temperature_2m,weather_code", "wind_speed_unit": "ms", "forecast_days": 1,
        })
        r.raise_for_status()
        d, c = r.json()["daily"], r.json()["current"]
        return (f"Vejret i {st['by']} i dag: lige nu {round(c['temperature_2m'])} grader og {WMO.get(c['weather_code'], 'skiftende vejr')}. "
                f"I løbet af dagen {WMO.get(d['weather_code'][0], 'skiftende vejr')}, mellem {round(d['temperature_2m_min'][0])} og "
                f"{round(d['temperature_2m_max'][0])} grader, {d['precipitation_probability_max'][0]} procent risiko for nedbør, "
                f"vind op til {round(d['wind_speed_10m_max'][0])} meter i sekundet. "
                f"Solen står op {d['sunrise'][0][-5:]} og går ned {d['sunset'][0][-5:]}.")
    except Exception as e:
        log.warning("Vejret kunne ikke hentes: %s", e)
        return "Vejrdata er ikke tilgængelige i dag — nævn ikke konkrete tal om vejret."
