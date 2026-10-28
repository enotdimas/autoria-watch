"""
Стежить за фільтрами auto.ria і надсилає нові оголошення в Telegram.

Налаштування через змінні середовища (у GitHub — Settings → Secrets):
  TG_TOKEN        — токен бота сповіщень від @BotFather
  TG_CHAT_ID      — ваш chat id
  TG_PHOTO_TOKEN  — токен окремого бота для фото: перешліть йому сповіщення,
                    і він відповість zip з усіма фото (необов'язково)
  TEST_MODE=1 — надіслати найсвіжіше оголошення з кожного фільтра (для перевірки
                вигляду), нічого не запам'ятовуючи
Фільтри — у списку SEARCH_URLS нижче (сортування "Дата додавання", order=7).
Новий фільтр можна додати будь-коли: при першій перевірці його поточні
оголошення тихо запам'ятовуються, а надсилаються лише ті, що з'являться потім.
"""
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

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


# ---------- zip з усіма фото на запит ----------
# Перешліть сповіщення (або посилання на оголошення) окремому боту для фото —
# при наступній перевірці він відповість zip з фото та info.txt.

PHOTO_RE = re.compile(r"photosnew/auto/photo/([a-z0-9_-]+?)__(\d{6,})", re.I)
CDN = "https://cdn0.riastatic.com/photosnew/auto/photo/"
SIZES = ("hd.jpg", "f.jpg", "hd.webp", "fx.jpg", "bx.jpg")
MAX_PART = 45 * 1024 * 1024  # ліміт Telegram на файл від бота — 50 МБ
HELP = ("📦 Перешліть мені сповіщення про авто або надішліть посилання на оголошення "
        "auto.ria — я відповім zip з усіма фото (протягом ~15 хвилин).")


def photo_token():
    return os.environ.get("TG_PHOTO_TOKEN", "").strip()


def page_photo_ids(page):
    """(slug, [photo_id], очікувана кількість) зі сторінки оголошення; головне фото — першим.

    У HTML є лише перші ~7 фото галереї. Решта лежить у даних сторінки як числа-id,
    поруч із фото інших оголошень (схожі авто, інші авто продавця). Тому шукаємо
    в даних компактний список рівно з N фото (N — лічильник "1 з N"), який містить
    усі вже відомі фото галереї, і беремо тільки його.
    """
    page = page.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    m = re.search(r"og:image[^>]*?photosnew/auto/photo/([a-z0-9_-]+?)__(\d{6,})", page, re.I) \
        or re.search(r"photosnew/auto/photo/([a-z0-9_-]+?)__(\d{6,})[^\"'\s]*?(?:hd|fx)\.", page, re.I) \
        or PHOTO_RE.search(page)
    if not m:
        return None, [], None
    slug, main_id = m.group(1), int(m.group(2))
    near = lambda n: abs(n - main_id) <= 5000

    # 1) фото галереї з явними посиланнями в розмітці (не в даних скриптів, де
    #    бувають інші авто продавця): та сама модель і близький номер
    markup = re.sub(r"<script\b.*?</script>", " ", page, flags=re.I | re.S)
    known = [main_id]
    for pm in PHOTO_RE.finditer(markup):
        pid = int(pm.group(2))
        if pm.group(1) == slug and near(pid) and pid not in known:
            known.append(pid)

    cm = re.search(r"\b1\s+з\s+(\d+)\b", page)
    expected = int(cm.group(1)) if cm else None
    if not expected or expected <= len(known):
        return slug, known[:expected] if expected else known, expected

    # 2) шукаємо в даних сторінки найкоротший фрагмент рівно з N різних близьких
    #    номерів, що містить усі відомі фото галереї — це і є список фото оголошення
    data = " ".join(re.findall(r"<script\b[^>]*>(.*?)</script>", page, flags=re.I | re.S))
    digits = len(str(main_id))
    seq = [(t.start(), int(t.group())) for t in re.finditer(r"(?<!\d)\d{%d}(?!\d)" % digits, data)
           if near(int(t.group()))]
    need, best = set(known), None
    for i in range(len(seq)):
        seen = []
        for j in range(i, len(seq)):
            if seq[j][1] not in seen:
                seen.append(seq[j][1])
                if len(seen) > expected:
                    break
            if len(seen) == expected and need <= set(seen):
                span = seq[j][0] - seq[i][0]
                if best is None or span < best[0]:
                    best = (span, list(seen))
                break
    if best:
        ids = best[1]
        ids.sort(key=lambda pid: pid != main_id)  # головне фото — першим
        return slug, ids, expected
    return slug, known, expected


def download_photo(urls):
    for u in urls:
        try:
            req = urllib.request.Request(u, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                ctype, body = r.headers.get("Content-Type", ""), r.read()
        except Exception:
            continue
        if ctype.startswith("image/") and len(body) > 3000:
            return body, ("webp" if "webp" in ctype else "png" if "png" in ctype else "jpg")
    return None


def build_zips(link, slug):
    """Повертає ([(ім'я файлу, байти)], підпис) або (None, текст помилки)."""
    from concurrent.futures import ThreadPoolExecutor
    try:
        page = fetch(link)
    except Exception as err:
        return None, f"😕 Не вдалося відкрити оголошення ({err}). Можливо, його вже видалено."
    info = parse_listing(page, slug)
    photo_slug, ids, expected = page_photo_ids(page)
    if not ids:
        return None, "😕 Не знайшов фото в оголошенні."
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda pid: download_photo([f"{CDN}{photo_slug}__{pid}{s}" for s in SIZES]), ids))
    files = [(f"{i:02d}.{r[1]}", r[0]) for i, r in enumerate((r for r in results if r), 1)]
    if not files:
        return None, "😕 Не вдалося завантажити фото."

    title = f"{info.get('name') or ''} {info.get('year') or ''}".strip()
    rows = [("Ціна", info.get("price")), ("Рік", info.get("year")),
            ("Пробіг", f"{info['mileage']} км" if info.get("mileage") else None),
            ("Двигун", f"{info['engine']} л" if info.get("engine") else None),
            ("Паливо", info.get("fuel")), ("Коробка", info.get("gearbox")),
            ("Кузов", ", ".join(x for x in (info.get("body"), info.get("color")) if x)),
            ("Місто", info.get("city")), ("Фото в архіві", str(len(files)))]
    info_txt = "\n".join([title, f"Посилання: {link}", ""] + [f"{k}: {v}" for k, v in rows if v]) + "\n"

    parts, cur, size = [], [], 0
    for item in files:
        if cur and size + len(item[1]) > MAX_PART:
            parts.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += len(item[1])
    parts.append(cur)
    base = re.sub(r"[^\w]+", "_", f"{title} {LISTING_RE.search(link).group(2)}").strip("_")
    zips = []
    for i, part in enumerate(parts, 1):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("info.txt", info_txt, compress_type=zipfile.ZIP_DEFLATED)
            for name, data in part:
                z.writestr(name, data, compress_type=zipfile.ZIP_STORED)
        zips.append((f"{base}{f'_part{i}' if len(parts) > 1 else ''}.zip", buf.getvalue()))

    caption = [f"📦 <b>{html.escape(title)}</b> — {len(files)} фото"]
    if expected and len(files) < expected:
        caption.append(f"⚠️ Зібрано {len(files)} з {expected} фото (решту сайт не віддає).")
    caption.append(f'👉 <a href="{html.escape(link)}">Оголошення</a>')
    return zips, "\n".join(caption)


def send_document(filename, content, caption, reply_to):
    boundary = uuid.uuid4().hex
    fields = {"chat_id": os.environ["TG_CHAT_ID"], "caption": caption, "parse_mode": "HTML",
              "reply_parameters": json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})}
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
               f'filename="{filename}"\r\nContent-Type: application/zip\r\n\r\n'.encode())
    body.write(content)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{photo_token()}/sendDocument", data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req, timeout=180)


def reply(text, reply_to):
    params = {"chat_id": os.environ["TG_CHAT_ID"], "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": "true",
              "reply_parameters": json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})}
    urllib.request.urlopen(f"https://api.telegram.org/bot{photo_token()}/sendMessage",
                           data=urllib.parse.urlencode(params).encode(), timeout=30)


def find_link(msg):
    """Посилання на оголошення в повідомленні або в сповіщенні, на яке відповіли."""
    for m in (msg, msg.get("reply_to_message") or {}):
        parts = [m.get("text"), m.get("caption")]
        parts += [e.get("url") for e in (m.get("entities") or []) + (m.get("caption_entities") or [])]
        found = LISTING_RE.search(" ".join(p for p in parts if p))
        if found:
            return "https://auto.ria.com/uk/" + found.group(0), found.group(1)
    return None


def process_requests(state):
    """Обробляє повідомлення, які ви надіслали боту для фото з минулої перевірки."""
    token = photo_token()
    if not token:
        return 0
    offset = state.get("tg_offset", 0)
    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=0&offset={offset}&allowed_updates=%5B%22message%22%5D"
    with urllib.request.urlopen(url, timeout=30) as r:
        updates = json.load(r).get("result", [])
    handled = 0
    for upd in updates:
        state["tg_offset"] = upd["update_id"] + 1
        msg = upd.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(os.environ["TG_CHAT_ID"]).strip():
            continue  # чужі повідомлення ігноруємо
        found = find_link(msg)
        if not found:
            reply(HELP, msg["message_id"])
            continue
        try:
            zips, caption = build_zips(*found)
            if zips is None:
                reply(caption, msg["message_id"])
                continue
            for i, (fname, data) in enumerate(zips):
                send_document(fname, data, caption if i == 0 else f"📦 Частина {i + 1} з {len(zips)}",
                              msg["message_id"])
            handled += 1
        except Exception as err:
            print(f"Zip для {found[0]}: {err}", file=sys.stderr)
            reply(f"❌ Не вдалося зібрати фото: {html.escape(str(err))}", msg["message_id"])
    return handled


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

    zips_sent = 0
    try:
        zips_sent = process_requests(state)
    except Exception as err:
        print(f"Запити на фото: {err}", file=sys.stderr)

    print(f"Фільтрів: {len(SEARCH_URLS)}, нових фільтрів: {len(baselined)}, "
          f"нових оголошень: {len(new_items)}, zip надіслано: {zips_sent}")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen": seen[-MAX_SEEN:], "urls": SEARCH_URLS,
                   "tg_offset": state.get("tg_offset", 0)}, f)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Помилка: {e}", file=sys.stderr)
        sys.exit(1)
