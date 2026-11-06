"""
Monitor de ofertas Rio Tinto. Avisa por Telegram cuando aparece una oferta
NUEVA en la búsqueda configurada. No se postula: eso lo hacés vos.
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# URL de la búsqueda (con el filtro de Argentina ya aplicado, sin &pg=).
SEARCH_URL = "https://jobs.riotinto.com/es/job-search-results/?location=Argentina&country=AR&radius=200&units=km"

# True = te llega "sin novedades" en cada revisión (48 mensajes por día).
# Útil solo los primeros días para confirmar que el bot está vivo.
HEARTBEAT = False

# Máximo de páginas de resultados que recorre por revisión.
MAX_PAGES = 10

JOB_LINK_SELECTOR = "a[href*='/job/']"
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


def page_url(n: int) -> str:
    """URL de la página n de resultados (la 1 es la URL original)."""
    if n == 1:
        return SEARCH_URL
    parts = urlparse(SEARCH_URL)
    q = parse_qs(parts.query)
    q["pg"] = [str(n)]
    return urlunparse(parts._replace(query=urlencode(q, doseq=True)))


def collect(page) -> dict:
    """Lee las ofertas visibles en la página actual: {url: titulo}."""
    found = {}
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
        found[urljoin(SEARCH_URL, href)] = title
    return found


def fetch_jobs() -> dict:
    jobs = {}
    prev = set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for n in range(1, MAX_PAGES + 1):
            page.goto(page_url(n), wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_selector(JOB_LINK_SELECTOR, timeout=30_000)
            except PWTimeout:
                print(f"página {n}: sin ofertas")
                break

            found = collect(page)
            if n > 1:
                # Esperar hasta 20 s a que los resultados cambien respecto de
                # la página anterior (el portal puede mostrar primero los viejos).
                for _ in range(20):
                    if set(found) != prev:
                        break
                    page.wait_for_timeout(1000)
                    found = collect(page)
            page.wait_for_timeout(1500)  # dejar terminar de dibujarse la lista
            found = collect(page)

            print(f"página {n}: {len(found)} ofertas, {len(set(found) - set(jobs))} nuevas")
            if not (set(found) - set(jobs)):
                break  # nada nuevo: era la última página o se repitió
            jobs.update(found)
            prev = set(found)
        browser.close()
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
