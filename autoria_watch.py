"""
Стежить за фільтрами auto.ria і надсилає нові оголошення в Telegram.

Налаштування через змінні середовища (у GitHub — Settings → Secrets):
  TG_TOKEN    — токен бота від @BotFather
  TG_CHAT_ID  — ваш chat id
  TEST_MODE=1 — надіслати найсвіжіше оголошення з кожного фільтра (для перевірки
                вигляду), нічого не запам'ятовуючи
Фільтри — у списку SEARCH_URLS нижче (сортування "Дата додавання", order=7).
Новий фільтр можна додати будь-коли: при першій перевірці його поточні
оголошення тихо запам'ятовуються, а надсилаються лише ті, що з'являться потім.
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

SEARCH_URLS = [
    # Фільтр 1
    "https://auto.ria.com/uk/search/?search_type=2&category=1&all[0].any[0].brand=29&all[0].any[0].any[0].model=3086&all[1].any[0].state=10&price[0]=1&price[2]=6500&gearbox[0]=2&abroad=0&customs_cleared=1&order=7&page=0&limit=20",
    # Фільтр 2
    "https://auto.ria.com/uk/search/?search_type=2&category=1&all[0].any[0].brand=29&all[0].any[0].any[0].model=1258&all[1].any[0].state=10&price[0]=1&price[2]=6500&gearbox[0]=2&abroad=0&customs_cleared=1&order=7&page=0&limit=20",
    # Фільтр 3
    "https://auto.ria.com/uk/search/?search_type=2&category=1&all[0].any[0].brand=28&all[0].any[0].any[0].model=265&all[1].any[0].state=10&price[0]=1&price[2]=6500&gearbox[0]=2&abroad=0&customs_cleared=1&order=7&page=0&limit=20",
    # Додайте сюди інші фільтри — кожен у лапках, через кому
]

STATE_FILE = "seen.json"
MAX_SEEN = 5000
LISTING_RE = re.compile(r"auto_([a-z0-9_-]+?)_(\d{6,})\.html")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "uk-UA,uk;q=0.9",
}


# ---------- завантаження ----------

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def listings(page_html):
    """Повертає {id: (посилання, slug)} у порядку появи на сторінці."""
    found = {}
    for m in LISTING_RE.finditer(page_html):
        found.setdefault(m.group(2), ("https://auto.ria.com/uk/" + m.group(0), m.group(1)))
    return found


# ---------- розбір сторінки оголошення ----------

META_RE = re.compile(r"<meta\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def meta_tags(page_html):
    tags = {}
    for tag in META_RE.findall(page_html):
        attrs = {k.lower(): (a if a is not None else b) for k, a, b in ATTR_RE.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs and key not in tags:
            tags[key] = html.unescape(attrs["content"]).replace(" ", " ").strip()
    return tags


def _num(s):
    digits = re.sub(r"\D", "", s or "")
    return f"{int(digits):,}".replace(",", " ") if digits else None


def _search(pattern, text, group=1):
    m = re.search(pattern, text or "", re.I)
    return m.group(group).strip() if m else None


def _nominative(color):
    """'сірого' -> 'сірий', 'синього' -> 'синій'."""
    if not color:
        return None
    if color.endswith("ього"):
        return color[:-4] + "ій"
    if color.endswith("ого"):
        return color[:-3] + "ий"
    return color


def _cap(s):
    return s[:1].upper() + s[1:] if s else s


def parse_listing(page_html, slug):
    meta = meta_tags(page_html)
    desc = meta.get("og:description") or meta.get("description") or ""
    title = meta.get("og:title") or ""
    keywords = meta.get("keywords") or ""

    info = {}
    m = re.search(r"AUTO\.RIA\s+(.+?)\s+(\d{4})\s+року", desc)
    if m:
        info["name"], info["year"] = m.group(1).strip(), m.group(2)
    else:
        info["name"] = " ".join(w.capitalize() for w in re.split(r"[_-]+", slug))
        info["year"] = _search(r"\b((?:19|20)\d{2})\b", title)

    m = re.search(r"року,\s*(?:\([^)]*\)\s*)?(.+?)\s+(\S+)\s+кольору", desc)
    if m:
        info["body"], info["color"] = _cap(m.group(1)), _nominative(m.group(2))

    info["engine"] = _search(r"двигун\s+([\d.,]+)\s*л", desc)
    info["fuel"] = _cap(_search(r"двигун\s+[\d.,]+\s*л\.?\s*([^,]+?)\s*,\s*пробіг", desc)
                        or _search(r"двигун\s+([^,\d][^,]*?)\s*,\s*пробіг", desc))
    mileage = _search(r"пробіг\s+([\d\s]+)\s*тис", desc)
    info["mileage"] = _num(str(int(re.sub(r"\D", "", mileage)) * 1000)) if mileage and re.sub(r"\D", "", mileage) else None
    m = re.search(r"ціна\s+([\d\s]+)\s*(\$|€|грн)", desc) or re.search(r"ціна\s+([\d\s]+)\s*(\$|€|грн)", title)
    if m:
        info["price"] = f"{_num(m.group(1))} {m.group(2)}"
    info["gearbox"] = _cap(_search(
        r"коробка передач\s+(ручна\s*/\s*механіка|[^\s,]+)", keywords))
    # місто в називному відмінку: "місто Біла Церква Hyundai Elantra ..." у keywords
    city = None
    if info.get("name"):
        city = _search(r"місто\s+(.+?)\s+" + re.escape(info["name"]), keywords)
    region = _search(r"UA,\s*([^,<>]{2,40}?обл\.)", page_html)
    if not city:
        city = (_search(r"UA,\s*[^,<>]{2,40}?обл\.,\s*([^,<>]{2,40}?),\s*\d{5}", page_html)
                or _search(r"UA,\s*([^,<>]{2,40}?),\s*\d{5}", page_html))
    if city in ("Київ", "Kyiv"):
        region = None
    if city:
        info["city"] = f"{city}, {region}" if region and region not in city else city
    else:
        info["city"] = _search(r"\sбу\s+(в\s+[^,]+?),\s*ціна", title) or _search(r"\s(в\s+[^,]+?),\s*ціна", title)
    info["photo"] = meta.get("og:image")
    return info


def format_message(info, link):
    e = lambda s: html.escape(s or "")
    lines = [f"🚗 <b>{e(info.get('name'))} {e(info.get('year') or '')}</b>".replace(" </b>", "</b>")]
    if info.get("price"):
        lines.append(f"💰 <b>{e(info['price'])}</b>")
    if info.get("mileage"):
        lines.append(f"🛣 {e(info['mileage'])} км")
    if info.get("engine"):
        lines.append(f"🔧 {e(info['engine'])} л")
    if info.get("fuel"):
        lines.append(f"⛽ {e(info['fuel'])}")
    if info.get("gearbox"):
        lines.append(f"⚙️ {e(info['gearbox'])}")
    body = ", ".join(x for x in (info.get("body"), info.get("color")) if x)
    if body:
        lines.append(f"🚘 {e(body)}")
    if info.get("city"):
        lines.append(f"📍 {e(info['city'])}")
    lines.append("")
    lines.append(f'👉 <a href="{e(link)}">Відкрити оголошення</a>')
    return "\n".join(lines)


# ---------- Telegram ----------

def tg(method, params):
    token = os.environ["TG_TOKEN"]
    params = dict(params, chat_id=os.environ["TG_CHAT_ID"])
    data = urllib.parse.urlencode(params).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=30)


def send_telegram(text):
    tg("sendMessage", {"text": text})


def send_listing(link, slug):
    try:
        info = parse_listing(fetch(link), slug)
        text = format_message(info, link)
    except Exception as err:
        print(f"Не вдалося розібрати {link}: {err}", file=sys.stderr)
        send_telegram(f"🚗 Нове оголошення:\n{link}")
        return
    for photo in photo_candidates(info.get("photo")):
        try:
            tg("sendPhoto", {"photo": photo, "caption": text, "parse_mode": "HTML"})
            return
        except Exception as err:
            print(f"Фото {photo} не надіслалось ({err})", file=sys.stderr)
    tg("sendMessage", {"text": text, "parse_mode": "HTML"})


def photo_candidates(url):
    """Головне фото оголошення: спершу .jpg-версія (Telegram її любить), потім оригінал."""
    if not url:
        return []
    jpg = re.sub(r"\.webp(\?.*)?$", r".jpg\1", url)
    return [jpg, url] if jpg != url else [url]


# ---------- стан ----------

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": [], "urls": []}
    if isinstance(data, list):  # старий формат: лише список id
        return {"seen": data, "urls": None}
    return data


def main():
    if os.environ.get("TEST_MODE", "").lower() in ("1", "true", "yes"):
        send_telegram("🧪 Тест: найсвіжіше оголошення з кожного фільтра")
        for url in SEARCH_URLS:
            found = listings(fetch(url))
            if found:
                link, slug = next(iter(found.values()))
                send_listing(link, slug)
            time.sleep(2)
        return

    state = load_state()
    seen = state["seen"]
    seen_set = set(seen)
    # urls=None — старий формат: невідомо, які фільтри вже бачили, тож
    # тихо запам'ятовуємо всі, щоб не надіслати старі оголошення
    known_urls = set(state["urls"] or [])

    new_items, baselined = [], []
    for url in SEARCH_URLS:
        found = listings(fetch(url))
        is_new_filter = url not in known_urls
        for lid, (link, slug) in found.items():
            if lid not in seen_set:
                seen_set.add(lid)
                seen.append(lid)
                if not is_new_filter:
                    new_items.append((link, slug))
        if is_new_filter:
            baselined.append(len(found))
        time.sleep(2)

    if baselined:
        send_telegram(
            f"✅ Додано фільтрів до моніторингу: {len(baselined)} "
            f"(зараз у них {sum(baselined)} оголошень). Надсилатиму лише нові."
        )
    for link, slug in new_items:
        send_listing(link, slug)
        time.sleep(1)

    print(f"Фільтрів: {len(SEARCH_URLS)}, нових фільтрів: {len(baselined)}, "
          f"нових оголошень: {len(new_items)}")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen": seen[-MAX_SEEN:], "urls": SEARCH_URLS}, f)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Помилка: {e}", file=sys.stderr)
        sys.exit(1)
