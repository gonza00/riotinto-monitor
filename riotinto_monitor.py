"""
Monitor de ofertas Rio Tinto. Avisa por Telegram cuando aparece una oferta
NUEVA en la búsqueda configurada. No se postula: eso lo hacés vos.
"""
import json
import os
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from urllib.parse import urljoin

# Pegá acá la URL de la búsqueda (con el filtro de Argentina ya aplicado).
SEARCH_URL = "https://jobs.riotinto.com/es/job-search-results/?location=Argentina&country=AR&radius=200&units=km"

# True = te llega "sin novedades" en cada revisión (48 mensajes por día).
# Útil solo los primeros días para confirmar que el bot está vivo.
HEARTBEAT = True

JOB_LINK_SELECTOR = "a[href*='/job/']"
SEEN_FILE = Path("seen.json")
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")


def notify(text: str) -> None:
    print(text)
    if TG_TOKEN and TG_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": text[:4000]},
            timeout=15,
        )


def fetch_jobs() -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(SEARCH_URL, wait_until="networkidle", timeout=60_000)
        try:
            page.wait_for_selector(JOB_LINK_SELECTOR, timeout=15_000)
        except PWTimeout:
            pass  # sin ofertas todavía: resultado válido
        jobs = {}
        for a in page.query_selector_all(JOB_LINK_SELECTOR):
            href = a.get_attribute("href")
            if not href:
                continue
            title = (a.inner_text() or "").strip()
            if not title:
                card = a.evaluate(
                    "el => { const c = el.closest('li, article, tr, div'); return c ? c.innerText : ''; }"
                )
                title = next((l.strip() for l in card.splitlines() if l.strip()), "")
            if not title:
                title = href.strip("/").split("/")[-1].replace("-", " ")
            jobs[urljoin(SEARCH_URL, href)] = title
        browser.close()
        return jobs


def main() -> int:
    try:
        jobs = fetch_jobs()
    except Exception as e:
        notify(f"Monitor Rio Tinto FALLÓ: {e}")
        return 1

    first_run = not SEEN_FILE.exists()
    seen = set(json.loads(SEEN_FILE.read_text())) if not first_run else set()
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
