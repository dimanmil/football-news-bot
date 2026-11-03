
import os
import re
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

STATE_FILE = "seen.json"


# translate=False — российские источники.
# translate=True — зарубежные источники.
FEEDS = [
    {
        "name": "Чемпионат",
        "url": "https://www.championat.com/rss/news/football/",
        "translate": False,
    },
    {
        "name": "Спорт-Экспресс",
        "url": "https://www.sport-express.ru/services/materials/news/football/se/",
        "translate": False,
    },
    {
        "name": "Sports.ru",
        "url": "https://www.sports.ru/rss/rubric.xml?s=208",
        "translate": False,
    },
    {
        "name": "Gazzetta",
        "url": "https://www.gazzetta.it/dynamic-feed/rss/section/Calcio.xml",
        "translate": True,
    },
    {
        "name": "AS",
        "url": "https://feeds.as.com/mrss-s/pages/as/site/as.com/section/futbol/portada/",
        "translate": True,
    },
    {
        "name": "Mundo Deportivo",
        "url": "https://www.mundodeportivo.com/feed/rss/futbol",
        "translate": True,
    },
    {
        "name": "kicker",
        "url": "https://newsfeed.kicker.de/news/fussball",
        "translate": True,
    },
    {
        "name": "RMC Sport",
        "url": "https://rmcsport.bfmtv.com/rss/football/",
        "translate": True,
    },
]


def local_name(tag):
    return tag.split("}")[-1].lower()


def get_child_text(element, names):
    for child in element:
        if local_name(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()

            href = child.attrib.get("href")
            if href:
                return href

    return ""


def parse_date(value):
    if not value:
        return None

    try:
        result = parsedate_to_datetime(value)
    except Exception:
        try:
            result = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except Exception:
            return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def clean_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query)

        query = [
            (key, value)
            for key, value in query
            if not key.lower().startswith("utm_")
            and key.lower() not in {
                "fbclid", "gclid", "yclid", "ref"
            }
        ]

        return urllib.parse.urlunsplit((
            parts.scheme,
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urllib.parse.urlencode(query),
            ""
        ))
    except Exception:
        return url


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

        return set(data.get("urls", []))

    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_state(seen):
    data = {
        "urls": sorted(seen)[-20000:]
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def read_feed(feed):
    request = urllib.request.Request(
        feed["url"],
        headers={"User-Agent": "Mozilla/5.0 FootballNewsBot"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())

    entries = []

    for element in root.iter():
        if local_name(element.tag) not in {"item", "entry"}:
            continue

        title = get_child_text(element, {"title"})

        link = get_child_text(element, {"link"})

        if not link:
            link = get_child_text(element, {"guid"})

        date_text = get_child_text(
            element,
            {"pubdate", "published", "updated", "date"}
        )

        published = parse_date(date_text)

        if not title or not link:
            continue

        url = clean_url(link)

        # Один источник + одна ссылка = одна публикация.
        unique_id = feed["name"] + "|" + url

        entries.append({
            "title": title.strip(),
            "url": url,
            "date": published,
            "sort_date": published or datetime.now(timezone.utc),
            "source": feed["name"],
            "id": unique_id,
            "translate": feed.get("translate", False)
        })

    return entries


def translate_title(title):
    api_url = "https://translate.googleapis.com/translate_a/single"

    params = urllib.parse.urlencode({
        "client": "gtx",
        "sl": "auto",
        "tl": "ru",
        "dt": "t",
        "q": title
    })

    request = urllib.request.Request(
        f"{api_url}?{params}",
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(
                request,
                timeout=15
            ) as response:
                result = json.loads(
                    response.read().decode("utf-8")
                )

            translated = "".join(
                part[0]
                for part in result[0]
                if part and part[0]
            ).strip()

            if translated:
                return translated

            print(f"Пустой перевод: {title}")

        except Exception as error:
            print(f"Ошибка перевода: {error}")

            if attempt == 0:
                time.sleep(3)

    return None


def send_message(item, title):
    if item["date"]:
        date_text = item["date"].strftime("%H:%M UTC")
    else:
        date_text = "время публикации не указано"

    message = (
        f"{title}\n\n"
        f"Источник: {item['source']}\n"
        f"Время: {date_text}\n"
        f"{item['url']}"
    )

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(
            response.read().decode("utf-8")
        )

    if not result.get("ok"):
        raise RuntimeError("Telegram не подтвердил отправку")


def main():
    seen = load_state()
    candidates = []

    for feed in FEEDS:
        try:
            entries = read_feed(feed)

            print(
                f"{feed['name']}: "
                f"получено публикаций — {len(entries)}"
            )

            for item in entries:
                if item["id"] in seen:
                    continue

                # Совместимость со старым seen.json.
                if item["url"] in seen:
                    continue

                candidates.append(item)

        except Exception as error:
            print(f"Ошибка {feed['name']}: {error}")

    # Отправляем публикации в хронологическом порядке.
    candidates.sort(
        key=lambda item: item["sort_date"]
    )

    sent = 0

    for item in candidates:
        try:
            title = item["title"]

            # Переводим только зарубежные источники.
            if item["translate"]:
                translated = translate_title(title)

                if not translated:
                    print(
                        f"Не удалось перевести, "
                        f"повторим позже: {title}"
                    )
                    continue

                title = translated

            send_message(item, title)

            # Отмечаем публикацию только после отправки.
            seen.add(item["id"])
            sent += 1

            print(
                f"Отправлено [{item['source']}]: {title}"
            )

            time.sleep(1)

        except Exception as error:
            print(
                f"Ошибка отправки "
                f"[{item['source']}]: {error}"
            )

    save_state(seen)

    print(f"Всего отправлено: {sent}")
    print(f"Всего новых публикаций найдено: {len(candidates)}")


if __name__ == "__main__":
    main()
