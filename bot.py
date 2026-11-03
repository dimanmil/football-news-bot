import os
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from html import unescape

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

FEEDS = [
    "РПЛ футбол трансферы новости",
    "Спартак ЦСКА Зенит Краснодар Динамо футбол новости",
    "АПЛ английская премьер лига футбол трансферы новости",
    "Арсенал Ливерпуль Манчестер Сити Манчестер Юнайтед Челси новости",
    "Барселона Реал Мадрид трансферы новости",
    "Лига чемпионов УЕФА футбол новости",
    "европейский футбол трансферы инсайды новости",
    "сборная России футбол новости"
]

STATE_FILE = "seen.json"
MAX_ITEMS_PER_FEED = 3

def get_feed(query):
    params = urllib.parse.urlencode({
        "q": query + " when:1d",
        "hl": "ru",
        "gl": "RU",
        "ceid": "RU:ru"
    })
    url = "https://news.google.com/rss/search?" + params

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return ET.fromstring(response.read())

def send_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(url, data=data)
    urllib.request.urlopen(request, timeout=20)

def main():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            seen = set(json.load(file))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = set()

    new_items = []

    for query in FEEDS:
        try:
            root = get_feed(query)

            for item in root.findall(".//item")[:MAX_ITEMS_PER_FEED]:
                title = unescape(item.findtext("title", "").strip())
                link = item.findtext("link", "").strip()
                source = item.findtext("source", "Источник").strip()

                if not title or not link or link in seen:
                    continue

                new_items.append((title, source, link))
                seen.add(link)

        except Exception as error:
            print(f"Ошибка при проверке {query}: {error}")

    for title, source, link in reversed(new_items):
        message = f"{title}\n\nИсточник: {source}\n{link}"
        try:
            send_message(message)
            print(f"Отправлено: {title}")
        except Exception as error:
            print(f"Ошибка отправки: {error}")

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(list(seen)[-5000:], file, ensure_ascii=False)

if __name__ == "__main__":
    main()
