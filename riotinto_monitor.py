"""
Monitor de ofertas Rio Tinto. Avisa por Telegram cuando aparece una oferta
NUEVA en la búsqueda configurada. No se postula: eso lo hacés vos.
"""
import json
import os
import sys
from pathlib import Path

import requests

# Portal Workday de Rio Tinto, filtrado por país (Argentina).
BASE = "https://wd3.myworkdaysite.com"
SITE = "riotinto/RioTinto_Careers"
API_URL = f"{BASE}/wday/cxs/{SITE}/jobs"
JOB_BASE = f"{BASE}/en-US/recruiting/{SITE}"
COUNTRY_FACET = "e42ad5eac46d4cc9b367ceaef42577c5"
PAGE_SIZE = 20  # máximo que acepta Workday

# True = te llega "sin novedades" en cada revisión (48 mensajes por día).
# Útil solo los primeros días para confirmar que el bot está vivo.
HEARTBEAT = False

SEEN_FILE = Path("seen.json")
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")


def notify(text: str) -> None:
    print(text)
    if TG_TOKEN and TG_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": text[:4000], "disable_web_page_preview": True},
            timeout=15,
        )


def fetch_jobs() -> dict:
    """Devuelve {url: titulo} de todas las ofertas de la búsqueda."""
    jobs = {}
    offset = 0
    total = None
    while True:
        r = requests.post(
            API_URL,
            json={
                "appliedFacets": {"locationCountry": [COUNTRY_FACET]},
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if total is None:  # Workday solo informa "total" en la primera página
            total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            jobs[JOB_BASE + j["externalPath"]] = j["title"]
        offset += PAGE_SIZE
        if offset >= total:
            break
    print(f"{len(jobs)} ofertas (total reportado: {total})")
    return jobs


def main() -> int:
    jobs = None
    error = None
    for _ in range(3):
        try:
            jobs = fetch_jobs()
            break
        except Exception as e:
            error = e
    if jobs is None:
        notify(f"Monitor Rio Tinto FALLÓ tras 3 intentos: {error}")
        return 1

    try:
        seen = set(json.loads(SEEN_FILE.read_text()))
        first_run = False
    except (FileNotFoundError, json.JSONDecodeError):
        seen = set()
        first_run = True

    new = {u: t for u, t in jobs.items() if u not in seen}

    if first_run:
        lista = "\n\n".join(f"{t}\n{u}" for u, t in jobs.items()) or "(ninguna)"
        notify(f"Monitor activo. Ofertas actuales en la búsqueda: {len(jobs)}\n{lista}")
    elif new:
        for u, t in new.items():
            notify(f"OFERTA NUEVA Rio Tinto: {t}\n{u}")
    elif HEARTBEAT:
        notify("Sin novedades en Rio Tinto.")

    SEEN_FILE.write_text(json.dumps(sorted(seen | set(jobs))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
