
import os
import re
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from difflib import SequenceMatcher


TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

STATE_FILE = "seen.json"

# Берём только свежие публикации.
MAX_AGE_MINUTES = 45
MAX_MESSAGES_PER_RUN = 20

FEEDS = [
    {
        "name": "Чемпионат",
        "url": "https://www.championat.com/rss/news/football/",
    },
    {
        "name": "Спорт-Экспресс",
        "url": "https://www.sport-express.ru/services/materials/news/football/se/",
    },
    {
        "name": "Sports.ru",
        "url": "https://www.sports.ru/rss/rubric.xml?s=208",
    },
    {
        "name": "Gazzetta",
        "url": "https://www.gazzetta.it/dynamic-feed/rss/section/Calcio.xml",
    },
    {
        "name": "AS",
        "url": "https://feeds.as.com/mrss-s/pages/as/site/as.com/section/futbol/portada/",
    },
    {
        "name": "Mundo Deportivo",
        "url": "https://www.mundodeportivo.com/feed/rss/futbol",
    },
    {
        "name": "kicker",
        "url": "https://newsfeed.kicker.de/news/fussball",
    },
    {
        "name": "RMC Sport",
        "url": "https://rmcsport.bfmtv.com/rss/football/",
    },
]

# Исключаем материалы, которые не нужны в новостной ленте.
EXCLUDE = [
    "прогноз", "ставка", "ставки", "коэффициент",
    "превью", "анонс матча", "где смотреть",
    "прямая трансляция", "текстовая трансляция",
    "обзор матча", "фоторепортаж", "подкаст",
    "колонка", "мнение", "разбор", "аналитика",
    "рейтинги", "топ-10", "топ 10",
    "вероятный состав", "составы на матч",
    "прогноз на матч", "ставка на матч",
]

STOP_WORDS = {
    "и", "в", "на", "по", "из", "за", "с", "о", "об",
    "а", "но", "для", "что", "как", "это", "его", "ее",
    "the", "a", "an", "of", "to", "in", "on", "for",
    "de", "la", "el", "y", "en", "der", "die", "das",
}


def local_name(tag):
    return tag.split("}")[-1].lower()


def get_child_text(element, names):
    for child in element:
        if local_name(child.tag) in names:
            if child.text:
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


def normalize_title(title):
    title = title.lower()
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def title_tokens(title):
    return {
        word for word in normalize_title(title).split()
        if len(word) > 2 and word not in STOP_WORDS
    }


def is_duplicate(title, previous_titles):
    current = normalize_title(title)

    for old in previous_titles:
        old_normalized = normalize_title(old)

        if current == old_normalized:
            return True

        if SequenceMatcher(
            None, current, old_normalized
        ).ratio() >= 0.88:
            return True

        a = title_tokens(title)
        b = title_tokens(old)

        if a and b:
            overlap = len(a & b) / len(a | b)
            if overlap >= 0.78:
                return True

    return False


def is_relevant(title):
    lowered = title.lower()

    if any(word in lowered for word in EXCLUDE):
        return False

    # Не отправляем совсем короткие или пустые заголовки.
    if len(title.strip()) < 15:
        return False

    return True


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        # Поддерживаем старый формат seen.json.
        if isinstance(data, list):
            return set(data), []

        return (
            set(data.get("urls", [])),
            data.get("titles", [])
        )

    except (FileNotFoundError, json.JSONDecodeError):
        return set(), []


def save_state(urls, titles):
    data = {
        "urls": list(urls)[-8000:],
        "titles": titles[-1000:]
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def read_feed(feed):
    request = urllib.request.Request(
        feed["url"],
        headers={"User-Agent": "Mozilla/5.0 FootballNewsBot"}
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        root = ET.fromstring(response.read())

    entries = []

    for element in root.iter():
        if local_name(element.tag) not in {"item", "entry"}:
            continue

        title = get_child_text(element, {"title"})
        link = get_child_text(element, {"link", "guid"})
        date_text = get_child_text(
            element,
            {"pubdate", "published", "updated", "date"}
        )

        published = parse_date(date_text)

        if title and link and published:
            entries.append({
                "title": title,
                "url": clean_url(link),
                "date": published,
                "source": feed["name"]
            })

    return entries


def send_message(item):
    date_text = item["date"].astimezone(
        timezone.utc
    ).strftime("%H:%M UTC")

    message = (
        f"{item['title']}\n\n"
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

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram не подтвердил отправку")


def main():
    seen_urls, seen_titles = load_state()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=MAX_AGE_MINUTES)

    candidates = []

    for feed in FEEDS:
        try:
            entries = read_feed(feed)

            print(
                f"{feed['name']}: "
                f"получено публикаций — {len(entries)}"
            )

            for item in entries:
                if item["date"] < cutoff:
                    continue

                if item["date"] > now + timedelta(minutes=5):
                    continue

                if item["url"] in seen_urls:
                    continue

                if not is_relevant(item["title"]):
                    continue

                candidates.append(item)

        except Exception as error:
            print(f"Ошибка {feed['name']}: {error}")

    # Сначала отправляем самые свежие новости.
    candidates.sort(
        key=lambda item: item["date"],
        reverse=True
    )

    sent = 0

    for item in candidates:
        if sent >= MAX_MESSAGES_PER_RUN:
            break

        if is_duplicate(item["title"], seen_titles):
            seen_urls.add(item["url"])
            continue

        try:
            send_message(item)

            seen_urls.add(item["url"])
            seen_titles.append(item["title"])

            sent += 1
            print(f"Отправлено: {item['title']}")

            time.sleep(1)

        except Exception as error:
            print(f"Ошибка отправки: {error}")

    save_state(seen_urls, seen_titles)

    print(f"Всего отправлено: {sent}")


if __name__ == "__main__":
    main()
