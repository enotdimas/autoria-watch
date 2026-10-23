"""
Стежить за фільтрами auto.ria і надсилає нові оголошення в Telegram.

Налаштування через змінні середовища (у GitHub — Settings → Secrets):
  TG_TOKEN    — токен бота від @BotFather
  TG_CHAT_ID  — ваш chat id
Фільтри — у списку SEARCH_URLS нижче (сортування "Дата додавання", order=7).
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

SEARCH_URLS = [
    # Hyundai Elantra, Київська обл., до 6500$, автомат
    "https://auto.ria.com/uk/search/?search_type=2&category=1&all[0].any[0].brand=29&all[0].any[0].any[0].model=3086&all[1].any[0].state=10&price[0]=1&price[2]=6500&gearbox[0]=2&abroad=0&customs_cleared=1&order=7&page=0&limit=20",
    # Honda Civic, Київська обл., до 6500$, автомат
    "https://auto.ria.com/uk/search/?search_type=2&category=1&all[0].any[0].brand=28&all[0].any[0].any[0].model=265&all[1].any[0].state=10&price[0]=1&price[2]=6500&gearbox[0]=2&abroad=0&customs_cleared=1&order=7&page=0&limit=20"
    # Hyundai Accent, Київська обл., до 6500$, автомат
    "https://auto.ria.com/uk/search/?search_type=2&category=1&all[0].any[0].brand=29&all[0].any[0].any[0].model=1258&all[1].any[0].state=10&price[0]=1&price[2]=6500&gearbox[0]=2&abroad=0&customs_cleared=1&order=7&page=0&limit=20"
  # Додайте сюди інші фільтри — кожен у лапках, через кому
]

STATE_FILE = "seen.json"
MAX_SEEN = 3000
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
            return json.load(f)
    except FileNotFoundError:
        return None


def main():
    state = load_state()
    first_run = state is None
    seen = state or []
    seen_set = set(seen)
    new_items, total = [], 0

    for url in SEARCH_URLS:
        found = listings(fetch(url))
        total += len(found)
        for lid, link in found.items():
            if lid not in seen_set:
                seen_set.add(lid)
                seen.append(lid)
                new_items.append(link)
        time.sleep(2)

    if first_run:
        send_telegram(
            f"✅ Моніторинг auto.ria запущено. Зараз у фільтрах {total} оголошень — "
            "надсилатиму лише нові."
        )
    else:
        for link in new_items:
            send_telegram(f"🚗 Нове оголошення:\n{link}")
            time.sleep(1)

    print(f"Знайдено на сторінках: {total}, нових: {0 if first_run else len(new_items)}")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-MAX_SEEN:], f)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Помилка: {e}", file=sys.stderr)
        sys.exit(1)
