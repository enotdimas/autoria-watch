"""
Стежить за фільтрами auto.ria і надсилає нові оголошення в Telegram.

Налаштування через змінні середовища (у GitHub — Settings → Secrets):
  TG_TOKEN    — токен бота від @BotFather
  TG_CHAT_ID  — ваш chat id
Фільтри — у списку SEARCH_URLS нижче (сортування "Дата додавання", order=7).
Новий фільтр можна додати будь-коли: при першій перевірці його поточні
оголошення тихо запам'ятовуються, а надсилаються лише ті, що з'являться потім.
"""
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
LISTING_RE = re.compile(r"auto_[a-z0-9_-]+?_(\d{6,})\.html")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "uk-UA,uk;q=0.9",
}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def listings(html):
    """Повертає {id: посилання} у порядку появи на сторінці."""
    found = {}
    for m in LISTING_RE.finditer(html):
        found.setdefault(m.group(1), "https://auto.ria.com/uk/" + m.group(0))
    return found


def send_telegram(text):
    token, chat = os.environ["TG_TOKEN"], os.environ["TG_CHAT_ID"]
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30
    )


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
        for lid, link in found.items():
            if lid not in seen_set:
                seen_set.add(lid)
                seen.append(lid)
                if not is_new_filter:
                    new_items.append(link)
        if is_new_filter:
            baselined.append(len(found))
        time.sleep(2)

    if baselined:
        send_telegram(
            f"✅ Додано фільтрів до моніторингу: {len(baselined)} "
            f"(зараз у них {sum(baselined)} оголошень). Надсилатиму лише нові."
        )
    for link in new_items:
        send_telegram(f"🚗 Нове оголошення:\n{link}")
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
