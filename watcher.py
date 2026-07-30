#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rental Watcher — Hollanda kiralik ev sitelerini izler, yeni ilanlari Telegram'a gonderir.

Kullanim:
  python watcher.py --once          # tek kontrol yap ve cik (launchd/cron icin)
  python watcher.py --loop          # surekli calis, 10 dakikada bir kontrol et
  python watcher.py --dry-run       # tum siteleri test et; bildirim gondermez, state'e yazmaz
  python watcher.py --dry-run --dump # ayrica render edilen HTML'leri logs/dump/ altina kaydet
  python watcher.py --test          # Telegram'a test mesaji gonder

Ilk basarili kontrolde o an yayinda olan ilanlar "gorulmus" sayilir ve bildirim
GONDERILMEZ (yuzlerce mesaj yagmasin diye). Sonraki kontrollerde yeni cikan her
ilan icin Telegram mesaji gider.
"""

import argparse
import json
import os
import re
import sys
import time
import html as html_mod
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "seen.json"
DUMP_DIR = BASE_DIR / "logs" / "dump"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}

HTTP_TIMEOUT = 35          # saniye

# Cerez/onay bildirimlerinde tiklanacak dugmeler — once "reddet" secenekleri denenir
CONSENT_SELECTORS = [
    "#CybotCookiebotDialogBodyButtonDecline",
    "#didomi-notice-disagree-button",
    "#onetrust-reject-all-handler",
    "#didomi-notice-agree-button",
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
]
# Bot dogrulama ekrani isaretleri (gorulurse ekstra beklenir)
CHALLENGE_MARKERS = ("Just a moment", "challenge-platform", "Je bent bijna op de pagina")
BROWSER_NAV_TIMEOUT = 45000  # ms
MAX_SEEN_PER_SITE = 4000   # site basina hatirlanan eski ilan sayisi
MAX_NOTIFY_PER_SITE = 30   # bir kontrolde site basina en fazla bu kadar tekil mesaj
FAILS_BEFORE_ALERT = 3     # arka arkaya bu kadar hata olursa uyari mesaji gonder

# ---------------------------------------------------------------------------
# Izlenen siteler.
#
#   method       : "zig"  -> Zig platformu JSON API'si (klikvoorkamers, plaza)
#                  "auto" -> once duz HTTP dene; ilan bulunamazsa headless
#                            tarayici (Playwright) ile render et
#   link_pattern : ilan detay linklerini yakalayan regex (auto icin)
#   exclude      : yakalanan linklerden elenecekler (regex listesi)
#   min_expected : sonuc bu sayinin altindaysa "hata" say (bot engeli tespiti
#                  icin; gercekten bos olabilen sitelerde 0 birakin)
#   cities       : sadece bu sehirlerdeki ilanlari bildir (zig icin; None = hepsi)
#   wait_selector: tarayici modunda bu CSS secicisi gorunene kadar bekle
# ---------------------------------------------------------------------------
SITES = [
    {
        "name": "Klik voor Kamers",
        "method": "zig",
        "api": "https://www.klikvoorkamers.nl/portal/object/frontend/getallobjects/format/json",
        "base": "https://www.klikvoorkamers.nl",
        "overview": "https://www.klikvoorkamers.nl/en/offerings/now-for-rent/",
        "detail": "https://www.klikvoorkamers.nl/en/offerings/now-for-rent/details/{key}",
        "cities": None,  # ornek: ["Tilburg", "Breda"]
        "min_expected": 1,
    },
    {
        "name": "Plaza (newnewnew.space)",
        "method": "zig",
        "api": "https://plaza.newnewnew.space/portal/object/frontend/getallobjects/format/json",
        "base": "https://plaza.newnewnew.space",
        "overview": "https://plaza.newnewnew.space/aanbod/wonen",
        "detail": "https://plaza.newnewnew.space/aanbod/wonen/details/{key}",
        "cities": None,  # ornek: ["Tilburg"]
        "min_expected": 1,
    },
    {
        "name": "Magis Real Estate",
        "method": "auto",
        "url": "https://magisrealestate.com/for-rent",
        "base": "https://magisrealestate.com",
        "link_pattern": r"/(?:for-rent|property|properties|listing|object)/[A-Za-z0-9][^\s\"'<>&?#\\]*",
        "exclude": [r"^/for-rent/?$"],
        "min_expected": 0,
        "wait_ms": 6000,
    },
    {
        "name": "Holland2Stay (Tilburg)",
        "method": "auto",
        "url": "https://www.holland2stay.com/residences?page=1&city%5Bfilter%5D=Tilburg%2C6093",
        "base": "https://www.holland2stay.com",
        "link_pattern": r"/residences/[A-Za-z0-9][^\s\"'<>&?#\\]*",
        "exclude": [r"^/residences/?$"],
        "min_expected": 0,
        "wait_ms": 9000,
    },
    {
        "name": "Holland2Stay (tumu)",
        "method": "auto",
        "url": "https://www.holland2stay.com/residences",
        "base": "https://www.holland2stay.com",
        "link_pattern": r"/residences/[A-Za-z0-9][^\s\"'<>&?#\\]*",
        "exclude": [r"^/residences/?$"],
        "min_expected": 1,
        "wait_ms": 9000,
    },
    {
        "name": "SSH Short Stay",
        "method": "auto",
        "url": "https://www.sshxl.nl/en/rental-offer/short-stay",
        "base": "https://www.sshxl.nl",
        "link_pattern": r"/en/rental-offer/[^\s\"'<>&?#\\]+",
        "exclude": [r"/rental-offer/(?:short-stay|long-stay)/?$", r"/rental-offer/?$"],
        "min_expected": 0,
        "wait_ms": 7000,
    },
    {
        "name": "SSH Long Stay",
        "method": "auto",
        "url": "https://www.sshxl.nl/en/rental-offer/long-stay",
        "base": "https://www.sshxl.nl",
        "link_pattern": r"/en/rental-offer/[^\s\"'<>&?#\\]+",
        "exclude": [r"/rental-offer/(?:short-stay|long-stay)/?$", r"/rental-offer/?$"],
        "min_expected": 0,
        "wait_ms": 7000,
    },
    {
        "name": "Pararius (Tilburg studio)",
        "method": "auto",
        "url": "https://www.pararius.com/apartments/tilburg/studio",
        "base": "https://www.pararius.com",
        "link_pattern": r"/(?:apartment|studio|room|house)-for-rent/[^\s\"'<>&?#\\]+",
        "exclude": [],
        "min_expected": 1,
        "wait_selector": ".search-list, .listing-search-item, .page__row--search-list",
        "wait_ms": 8000,
    },
    {
        "name": "Kamernet (Tilburg)",
        "method": "auto",
        "url": (
            "https://kamernet.nl/en/for-rent/properties-tilburg?pageNo=1&radius=7"
            "&minSize=0&maxRent=0&searchView=1&sort=1"
        ),
        "base": "https://kamernet.nl",
        "link_pattern": r"/en/for-rent/[^\s\"'<>&?#/\\]+/[^\s\"'<>&?#/\\]+/(?:room|apartment|studio|house|anti-squat)-\d+",
        "exclude": [],
        "min_expected": 1,
        "wait_ms": 6000,
    },
    {
        "name": "Funda (Tilburg, furnished, <1500)",
        "method": "auto",
        "url": "https://www.funda.nl/en/zoeken/huur?selected_area=tilburg&price=0-1500&renting_condition=furnished",
        "base": "https://www.funda.nl",
        "link_pattern": r"/(?:en/)?detail/huur/[^\s\"'<>&?#\\]+",
        "exclude": [],
        "min_expected": 1,
        "wait_selector": "[data-testid='searchResultItem'], .search-result",
        "wait_ms": 10000,
    },
    {
        "name": "Huurportaal (Tilburg)",
        "method": "auto",
        "url": "https://huurportaal.nl/en/for-rent/tilburg",
        "base": "https://huurportaal.nl",
        "link_pattern": r"/en/listings/[A-Za-z0-9][^\s\"'<>&?#\\]*",
        "exclude": [r"^/en/listings/?$"],
        "min_expected": 1,
        "wait_ms": 7000,
    },
    {
        # sshxl.nl uye girisi olmadan ilan gostermiyor; kisa donem konaklamalar
        # SSH'in herkese acik rezervasyon portalinda yayinlaniyor
        "name": "SSH Booking (short stay)",
        "method": "auto",
        "url": "https://booking.sshxl.nl/accommodations",
        "base": "https://booking.sshxl.nl",
        "link_pattern": r"/(?:accommodations?|units?|rooms?)/[A-Za-z0-9][^\s\"'<>&?#\\]*",
        "exclude": [r"^/accommodations/?$"],
        "min_expected": 0,
        "wait_ms": 8000,
    },
]

# ---------------------------------------------------------------------------


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), msg, flush=True)


def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token") or ""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id") or ""
    return {"token": token.strip(), "chat_id": str(chat_id).strip()}


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log("UYARI: seen.json okunamadi (%s), sifirdan baslaniyor" % e)
    return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def send_telegram(cfg, text, silent=False):
    if not cfg["token"] or not cfg["chat_id"]:
        log("UYARI: Telegram ayarli degil, mesaj gonderilemedi: %s" % text[:80])
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % cfg["token"]
    payload = {
        "chat_id": cfg["chat_id"],
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }
    for attempt in (1, 2):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                return True
            log("Telegram hata %s: %s" % (r.status_code, r.text[:200]))
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)) + 1)
                continue
            return False
        except requests.RequestException as e:
            log("Telegram istek hatasi (deneme %d): %s" % (attempt, e))
            time.sleep(2)
    return False


# --------------------------- veri cekme katmani ----------------------------


class BrowserPool:
    """Playwright chromium'u tembel baslatir; calisma sonunda kapatilir."""

    def __init__(self):
        self._pw = None
        self._browser = None

    def _ensure(self):
        if self._browser:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "playwright kurulu degil. Kurulum: "
                ".venv/bin/pip install playwright && .venv/bin/playwright install chromium"
            )
        self._pw = sync_playwright().start()
        try:
            kwargs = dict(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                # tam chromium ("yeni headless") bot korumalarini daha iyi gecer
                self._browser = self._pw.chromium.launch(channel="chromium", **kwargs)
            except Exception:
                self._browser = self._pw.chromium.launch(**kwargs)
        except Exception:
            # yarim kalan playwright durumu sonraki denemeleri de bozmasin
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = self._browser = None
            raise

    def get_html(self, site):
        self._ensure()
        ctx = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="Europe/Amsterdam",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9,nl;q=0.8"},
        )
        try:
            page = ctx.new_page()
            page.goto(site["url"], wait_until="domcontentloaded", timeout=BROWSER_NAV_TIMEOUT)
            page.wait_for_timeout(1500)
            for consent in CONSENT_SELECTORS:  # cerez bildirimi icerigi engellemesin
                try:
                    loc = page.locator(consent)
                    if loc.count():
                        loc.first.click(timeout=2000)
                        page.wait_for_timeout(1500)
                        break
                except Exception:
                    continue
            sel = site.get("wait_selector")
            if sel:
                try:
                    page.wait_for_selector(sel, timeout=20000)
                except Exception:
                    pass  # secici gelmediyse eldekiyle devam
            page.wait_for_timeout(site.get("wait_ms", 6000))
            for _ in range(4):  # cloudflare vb. dogrulama ekrani kendiliginden gecebilir
                if not any(m in page.content() for m in CHALLENGE_MARKERS):
                    break
                page.wait_for_timeout(6000)
            for _ in range(3):  # lazy-load listeleri tetikle
                try:
                    page.mouse.wheel(0, 2500)
                except Exception:
                    break
                page.wait_for_timeout(700)
            return page.content()
        finally:
            ctx.close()

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = self._pw = None


def normalize_url(base, href):
    absu = urljoin(base, href.strip())
    parts = urlsplit(absu)
    # bazi sitelerde href icinde bosluk olabiliyor; kodlanmazsa Telegram'da link kirilir
    path = parts.path.rstrip("/").replace(" ", "%20")
    return "%s://%s%s" % (parts.scheme, parts.netloc, path)


def extract_listings(site, html_text):
    """HTML'den link_pattern'e uyan ilan linklerini (url, baslik) olarak cikarir."""
    pat = re.compile(site["link_pattern"])
    excludes = [re.compile(x) for x in site.get("exclude", [])]
    items = {}  # url -> title

    def excluded(href):
        path = urlsplit(urljoin(site["base"], href)).path
        return any(x.search(path) for x in excludes)

    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not pat.search(href) or excluded(href):
            continue
        url = normalize_url(site["base"], href)
        title = " ".join(a.get_text(" ", strip=True).split())[:200]
        if url not in items or len(title) > len(items[url]):
            items[url] = title

    # Anchor bulunamadiysa (linkler JSON icinde gomulu olabilir) ham regex dene.
    # HTML entity'leri cozulur ki &quot; gibi kaliplarla regex JSON'un icine tasmasin.
    if not items:
        for m in pat.finditer(html_mod.unescape(html_text)):
            href = m.group(0).rstrip("\\")
            if excluded(href):
                continue
            url = normalize_url(site["base"], href)
            items.setdefault(url, "")

    result = []
    for url, title in items.items():
        # anchor metni bos ya da anlamsizsa (resim karuseli vb.) slug'dan baslik uret
        if sum(1 for c in title if c.isalpha()) < 5:
            slug = unquote(url).rstrip("/").rsplit("/", 1)[-1]
            title = slug.replace("-", " ")
        result.append({"id": url, "title": title, "url": url})
    return result


def fetch_zig(site, session):
    r = session.get(site["api"], headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    objects = r.json().get("result") or []
    items = []
    cities = site.get("cities")
    for o in objects:
        if not isinstance(o, dict) or not o.get("id"):
            continue
        city = o.get("city")
        city_name = city.get("name", "") if isinstance(city, dict) else (city or "")
        if cities and city_name and city_name.lower() not in [c.lower() for c in cities]:
            continue
        street = " ".join(
            str(x) for x in (o.get("street"), o.get("houseNumber"), o.get("houseNumberAddition")) if x
        ).strip()
        rent = o.get("totalRent") or o.get("netRent")
        bits = [b for b in (street, city_name) if b]
        title = ", ".join(bits) if bits else str(o.get("id"))
        if rent:
            title += " — €%s" % rent
        key = o.get("urlKey")
        url = site["detail"].format(key=key) if key else site["overview"]
        items.append({"id": str(o["id"]), "title": title, "url": url})
    return items


def fetch_auto(site, session, browser, dump=False):
    """Once duz HTTP; ilan cikmazsa headless tarayici. (kaynak, items) doner."""
    html_text, source = None, "http"
    try:
        r = session.get(site["url"], headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        if r.status_code < 400:
            html_text = r.text
    except requests.RequestException as e:
        log("  %s: HTTP istegi basarisiz (%s), tarayici denenecek" % (site["name"], e))

    items = extract_listings(site, html_text) if html_text else []
    if not items:
        source = "browser"
        html_text = browser.get_html(site)
        items = extract_listings(site, html_text)
        # Not: korumali sitelerin NORMAL sayfalari da "challenge-platform" scripti
        # icerebiliyor; o yuzden sadece hic ilan cikmadiysa engel sayilir
        if not items and any(m in html_text for m in CHALLENGE_MARKERS):
            raise RuntimeError("bot dogrulamasi gecilemedi (Cloudflare/Akamai)")

    if dump and html_text:
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9]+", "-", site["name"].lower()).strip("-")
        (DUMP_DIR / ("%s.html" % safe)).write_text(html_text, encoding="utf-8")
    return source, items


def fetch_site(site, session, browser, dump=False):
    if site["method"] == "zig":
        return "api", fetch_zig(site, session)
    return fetch_auto(site, session, browser, dump=dump)


# ------------------------------- ana akis ----------------------------------


def check_all(cfg, state, dry_run=False, dump=False):
    session = requests.Session()
    browser = BrowserPool()
    global_seen = set()
    for s in state.values():
        global_seen.update(s.get("seen", {}))

    try:
        for site in SITES:
            name = site["name"]
            st = state.setdefault(name, {"seen": {}, "fails": 0, "down_notified": False})
            first_run = not st["seen"]
            try:
                source, items = fetch_site(site, session, browser, dump=dump)
                if len(items) < site.get("min_expected", 0):
                    raise RuntimeError(
                        "beklenenden az ilan bulundu (%d) — bot engeli olabilir" % len(items)
                    )
            except Exception as e:
                st["fails"] = st.get("fails", 0) + 1
                log("HATA %s (%d. kez): %s" % (name, st["fails"], e))
                if (
                    not dry_run
                    and st["fails"] == FAILS_BEFORE_ALERT
                    and not st.get("down_notified")
                ):
                    send_telegram(
                        cfg,
                        "⚠️ <b>%s</b> son %d kontroldur okunamiyor:\n%s"
                        % (html_mod.escape(name), FAILS_BEFORE_ALERT, html_mod.escape(str(e)[:300])),
                        silent=True,
                    )
                    st["down_notified"] = True
                continue

            if st.get("down_notified") and not dry_run:
                send_telegram(cfg, "✅ <b>%s</b> tekrar okunabiliyor." % html_mod.escape(name), silent=True)
            st["fails"], st["down_notified"] = 0, False

            new_items = [i for i in items if i["id"] not in st["seen"]]
            now = time.strftime("%Y-%m-%dT%H:%M:%S")

            if dry_run:
                log(
                    "%-28s [%s] toplam %d ilan, %d yeni%s"
                    % (name, source, len(items), len(new_items), " (ilk calistirma)" if first_run else "")
                )
                for it in items[:3]:
                    log("    ornek: %s | %s" % (it["title"][:70], it["url"]))
                continue

            if first_run:
                for it in items:
                    st["seen"][it["id"]] = now
                log("%s: ilk calistirma, %d mevcut ilan kaydedildi (bildirim yok)" % (name, len(items)))
            elif new_items:
                fresh = [i for i in new_items if i["url"] not in global_seen]
                log("%s: %d yeni ilan" % (name, len(fresh)))
                if len(fresh) > MAX_NOTIFY_PER_SITE:
                    send_telegram(
                        cfg,
                        "🏠 <b>%s</b>: %d yeni ilan birden geldi — tek tek gondermek yerine listeye bakin:\n%s"
                        % (html_mod.escape(name), len(fresh), site.get("url") or site.get("overview", "")),
                    )
                else:
                    for it in fresh:
                        text = "🏠 <b>%s</b>\n%s\n%s" % (
                            html_mod.escape(name),
                            html_mod.escape(it["title"]),
                            it["url"],
                        )
                        send_telegram(cfg, text)
                        time.sleep(1)  # telegram rate limitine takilmamak icin
                for it in new_items:
                    st["seen"][it["id"]] = now
                    global_seen.add(it["url"])
            else:
                log("%s: yeni ilan yok (%d ilan)" % (name, len(items)))

            # cok eski kayitlari buda
            if len(st["seen"]) > MAX_SEEN_PER_SITE:
                for key in list(st["seen"])[: len(st["seen"]) - MAX_SEEN_PER_SITE]:
                    del st["seen"][key]

            if not dry_run:
                save_state(state)
    finally:
        browser.close()


def main():
    ap = argparse.ArgumentParser(description="Kiralik ev ilan takipcisi")
    ap.add_argument("--once", action="store_true", help="tek kontrol yap ve cik")
    ap.add_argument("--loop", action="store_true", help="surekli calis")
    ap.add_argument("--interval", type=int, default=10, help="dongu araligi (dakika)")
    ap.add_argument("--dry-run", action="store_true", help="test: bildirim yok, state yazilmaz")
    ap.add_argument("--dump", action="store_true", help="dry-run ile HTML ciktilarini kaydet")
    ap.add_argument("--test", action="store_true", help="Telegram test mesaji gonder")
    args = ap.parse_args()

    cfg = load_config()

    if args.test:
        ok = send_telegram(cfg, "✅ Rental Watcher test mesaji — baglanti calisiyor.")
        sys.exit(0 if ok else 1)

    if not args.dry_run and (not cfg["token"] or not cfg["chat_id"]):
        log("UYARI: config.json icinde telegram_bot_token / telegram_chat_id eksik.")
        log("Bildirimler gonderilemez. Kurulum icin README.md'ye bakin.")

    if args.loop:
        while True:
            started = time.time()
            try:
                check_all(cfg, load_state(), dry_run=False)
            except Exception as e:
                log("Kontrol turu hata verdi: %s" % e)
            wait = max(30, args.interval * 60 - (time.time() - started))
            log("Sonraki kontrol %d saniye sonra." % wait)
            time.sleep(wait)
    else:
        # --once ve --dry-run buraya duser
        check_all(cfg, load_state(), dry_run=args.dry_run, dump=args.dump)
        log("Bitti.")


if __name__ == "__main__":
    main()
