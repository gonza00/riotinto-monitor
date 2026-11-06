"""
Monitor de ofertas Rio Tinto. Avisa por Telegram cuando aparece una oferta
NUEVA en la búsqueda configurada. No se postula: eso lo hacés vos.
"""
import json
import os
import re
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

# Posibles botones de "página siguiente" (se prueban en orden).
NEXT_SELECTORS = [
    "a[rel='next']",
    "[aria-label*='iguiente' i]",
    "[aria-label*='next' i]",
    "a:has-text('Siguiente')",
    "button:has-text('Siguiente')",
    "a:has-text('Next')",
    "button:has-text('Next')",
]
PAGINATION_ITEMS = "[class*='pagin' i] a, [class*='pagin' i] button, nav a, nav button"


def notify(text: str) -> None:
    print(text)
    if TG_TOKEN and TG_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": text[:4000], "disable_web_page_preview": True},
            timeout=15,
        )


def page_url(n: int) -> str:
    """URL de la página n de resultados por carga directa (respaldo)."""
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


def wait_change(page, prev: set, secs: int = 20) -> dict:
    """Espera a que la lista de ofertas cambie respecto de `prev` y se asiente."""
    found = collect(page)
    for _ in range(secs):
        if found and set(found) != prev:
            break
        page.wait_for_timeout(1000)
        found = collect(page)
    page.wait_for_timeout(1500)
    return collect(page)


def find_next_control(page, n: int):
    """Busca el botón o enlace que lleva a la página n."""
    for a in page.query_selector_all("a[href*='pg=']"):
        href = a.get_attribute("href") or ""
        if re.search(rf"[?&]pg={n}(&|#|$)", href) and a.is_visible():
            return a
    for sel in NEXT_SELECTORS:
        for el in page.query_selector_all(sel):
            if el.is_visible():
                return el
    for el in page.query_selector_all(PAGINATION_ITEMS):
        if el.is_visible() and (el.inner_text() or "").strip() == str(n):
            return el
    return None


def debug_pagination(page) -> None:
    """Deja en el registro cómo está armada la paginación (para diagnosticar)."""
    hrefs = [a.get_attribute("href") for a in page.query_selector_all("a[href*='pg=']")][:5]
    print("enlaces con pg=:", hrefs)
    for el in page.query_selector_all("[class*='pagin' i], nav[aria-label]")[:2]:
        print("paginación HTML:", (el.evaluate("e => e.outerHTML") or "")[:1200])


def debug_page(page, n: int) -> None:
    body = (page.inner_text("body") or "")[:300].replace("\n", " ")
    print(f"página {n}: sin ofertas. url={page.url} título={page.title()!r} texto={body!r}")


def fetch_jobs() -> dict:
    jobs = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_selector(JOB_LINK_SELECTOR, timeout=30_000)
        except PWTimeout:
            print("página 1: sin ofertas")
            browser.close()
            return jobs
        page.wait_for_timeout(1500)
        found = collect(page)
        print(f"página 1: {len(found)} ofertas")
        debug_pagination(page)
        jobs.update(found)
        prev = set(found)

        for n in range(2, MAX_PAGES + 1):
            found = {}
            ctrl = find_next_control(page, n)
            if ctrl:
                try:
                    ctrl.click(timeout=10_000)
                    found = wait_change(page, prev)
                except Exception as e:
                    print(f"página {n}: falló el clic ({e})")
            else:
                print(f"página {n}: no encontré botón de paginación")

            if not found or set(found) == prev:
                # Respaldo: carga directa de la URL con &pg=n
                page.goto(page_url(n), wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_selector(JOB_LINK_SELECTOR, timeout=15_000)
                except PWTimeout:
                    debug_page(page, n)
                    break
                found = wait_change(page, prev)

            nuevas = set(found) - set(jobs)
            print(f"página {n}: {len(found)} ofertas, {len(nuevas)} nuevas")
            if not nuevas:
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
