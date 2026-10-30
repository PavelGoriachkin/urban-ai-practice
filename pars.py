"""Цены proconsim.ru по артикулам. Python 3.10+.

Установка: python -m pip install requests playwright
Запуск: python pars.py
"""

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests


FOLDER = Path(__file__).resolve().parent
SITE = "https://proconsim.ru/"
FIELDS = ["Артикул", "Цена", "Название", "Доступно", "Статус"]
DEFAULT_PARAMS = {
    "strategy": "advanced_xname,zero_queries",
    "fullData": "true", "withCorrection": "true", "withFacets": "false",
    "treeFacets": "false", "regionId": "global",
    "useCategoryPrediction": "false", "size": "100", "offset": "0",
    "showUnavailable": "true", "unavailableMultiplier": "0.2",
    "preview": "false", "withSku": "false", "sort": "DEFAULT",
}


class ParserError(RuntimeError):
    pass


def is_search_url(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (parts.scheme == "https" and
            (host == "diginetica.net" or host.endswith(".diginetica.net")) and
            parts.path.rstrip("/") == "/search" and parts.port in (None, 443))


def read_articles(path):
    # Стандартный CSV reader сохраняет ведущие нули, пустые строки и слово NA.
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=";")
        names = reader.fieldnames or []
        column = next((c for c in names if c.strip().casefold() == "артикул"), None)
        column = column or next((c for c in names if "артикул" in c.casefold()), None)
        if column is None:
            raise ParserError("В CSV не найдена колонка «Артикул».")
        return [(row.get(column) or "").strip() for row in reader]


def refresh_browser(sample, state_path, browser):
    try:
        from playwright.sync_api import sync_playwright, Error as BrowserError
    except ImportError as exc:
        raise ParserError("Установите зависимости: python -m pip install requests playwright") from exc

    print("Этап 0: открываю браузер с отдельным профилем магазина.")
    captured = {}

    def on_response(response):
        if not is_search_url(response.url) or response.status != 200:
            return
        query = parse_qs(urlsplit(response.url).query)
        if query.get("st", [""])[0].strip().casefold() != sample.casefold():
            return
        key = query.get("apiKey", [""])[0]
        if not key:
            return
        try:
            data = response.json()
        except Exception:
            return
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            return
        if data.get("message"):
            return
        params = dict(DEFAULT_PARAMS)
        for name in DEFAULT_PARAMS:
            if name in query:
                params[name] = query[name][0]
        params["apiKey"] = key
        captured.update(endpoint=response.url.split("?", 1)[0], params=params)

    with sync_playwright() as pw:
        context = None
        channels = [browser] if browser != "auto" else ["chrome", "msedge", "chromium"]
        for channel in channels:
            try:
                context = pw.chromium.launch_persistent_context(
                    str(state_path.parent / ("browser-" + channel)),
                    channel=None if channel == "chromium" else channel,
                    headless=False, locale="ru-RU",
                )
                break
            except BrowserError:
                continue
        if context is None:
            raise ParserError("Не удалось открыть браузер. Закройте окно этого парсера, если оно уже открыто. "
                              "Либо установите Chromium: python -m playwright install chromium")
        try:
            context.on("response", on_response)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(SITE, wait_until="domcontentloaded", timeout=60000)
            except BrowserError:
                print("Сайт загружается медленно. Дождитесь его открытия в браузере.")
            input("В браузере выберите нужный город, при необходимости пройдите проверку сайта. "
                  "После загрузки страницы нажмите Enter здесь: ")
            # После ручного выбора города запускаем поиск первого артикула.
            page.wait_for_timeout(500)
            captured.clear()
            selector = ("input[type='search'], input[name='q'], input[name='search'], "
                        "input[placeholder*='оиск'], input[placeholder*='айти']")
            for field in page.locator(selector).all():
                if field.is_visible():
                    try:
                        field.fill(sample, timeout=3000)
                        field.press("Enter", timeout=3000)
                        break
                    except BrowserError:
                        continue
            deadline = time.monotonic() + 12
            while not captured and time.monotonic() < deadline:
                page.wait_for_timeout(250)
            if not captured:
                print(f"В строке поиска магазина вручную найдите артикул {sample!r}.")
                input("Дождитесь результатов и нажмите Enter здесь (браузер не закрывайте): ")
                deadline = time.monotonic() + 5
                while not captured and time.monotonic() < deadline:
                    page.wait_for_timeout(250)
            if not captured:
                raise ParserError("Не получен успешный ответ поиска Diginetica. "
                                  "Возможно, магазин сменил поисковый сервис; одних куки недостаточно.")
            # Ограничиваем сохранение куки доменами магазина и поискового сервиса.
            cookies = [c for c in context.cookies() if any(
                c["domain"].lstrip(".") == domain or
                c["domain"].lstrip(".").endswith("." + domain)
                for domain in ("proconsim.ru", "diginetica.net")
            )]
            state = dict(captured, cookies=cookies,
                         user_agent=page.evaluate("navigator.userAgent"))
            temp = state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(state_path)
            print("Куки и актуальные настройки поиска сохранены.")
            return state
        finally:
            context.close()


def load_state(path):
    if not path.exists():
        raise ParserError("Нет сохранённых настроек. Запустите скрипт без --skip-browser.")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not is_search_url(state.get("endpoint", "")) or not state.get("params", {}).get("apiKey"):
        raise ParserError("Настройки поиска повреждены. Запустите без --skip-browser.")
    return state


def make_session(state):
    session = requests.Session()
    session.headers.update({"User-Agent": state["user_agent"], "Referer": SITE,
                            "Origin": SITE.rstrip("/"), "Accept": "application/json"})
    for cookie in state.get("cookies", []):
        expiry = cookie.get("expires", -1)
        if expiry > 0 and expiry <= time.time():
            continue
        session.cookies.set(cookie["name"], cookie["value"],
                            domain=cookie["domain"], path=cookie.get("path", "/"),
                            secure=cookie.get("secure", False),
                            expires=int(expiry) if expiry > 0 else None)
    return session


def fetch_data(session, state, article, offset):
    params = dict(state["params"], st=article, offset=str(offset), size="100", fullData="true")
    for attempt in range(3):
        try:
            response = session.get(state["endpoint"], params=params, timeout=30)
        except requests.RequestException:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            # Не печатаем URL исключения: он содержит ключ API.
            raise ParserError("Сетевая ошибка после трёх попыток.") from None
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
        if response.status_code in (400, 401, 403):
            raise ParserError(f"HTTP {response.status_code}: поисковый сервис отклонил запрос. "
                              "Повторите этап 0 (запуск без --skip-browser).")
        if response.status_code != 200:
            raise ParserError(f"Ошибка поиска: HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError:
            raise ParserError("Вместо JSON получена страница сайта/защиты. Повторите этап 0.") from None
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            raise ParserError("Изменился формат ответа поискового сервиса.")
        if data.get("message"):
            raise ParserError("Поиск вернул ошибку. Повторите этап 0 для обновления настроек.")
        return data


def parse_price(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        price = float(str(value).replace("\xa0", "").replace("\u202f", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price >= 0 else None


def get_price_for_article(article, session, state):
    # Проверяем точное совпадение и следующие страницы, а не первый похожий товар.
    for offset in range(0, 1000, 100):
        data = fetch_data(session, state, article, offset)
        products = data["products"]
        for product in products:
            attrs = product.get("attributes") or {}
            values = attrs.get("артикул", [])
            if not isinstance(values, list):
                values = [values]
            if any(str(value).strip() == article for value in values):
                price = parse_price(product.get("price"))
                available = product.get("available", False)
                if isinstance(available, str):
                    available = available.strip().lower() in ("true", "1", "yes")
                return {"Артикул": article, "Цена": price, "Название": product.get("name", ""),
                        "Доступно": bool(available), "Статус": "Найден" if price is not None else "Нет цены"}
        if len(products) < 100:
            break
    else:
        raise ParserError("Проверено 1000 результатов без точного совпадения; уточните артикул.")
    return {"Артикул": article, "Цена": None, "Название": "", "Доступно": False, "Статус": "Не найден"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=FOLDER / "Артикулы — копия.csv")
    parser.add_argument("--output", type=Path, default=FOLDER / "price.csv")
    parser.add_argument("--state-dir", type=Path, default=FOLDER / ".proconsim")
    parser.add_argument("--browser", choices=("auto", "chrome", "msedge", "chromium"), default="auto")
    parser.add_argument("--skip-browser", action="store_true", help="Использовать ранее сохранённые настройки")
    parser.add_argument("--refresh-only", action="store_true", help="Только этап 0")
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        raise ParserError("Входной и выходной CSV должны быть разными файлами.")
    articles = read_articles(args.input)
    sample = next((a for a in articles if a), None)
    if not sample:
        raise ParserError("В файле нет непустых артикулов.")
    args.state_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.state_dir / "session.json"
    state = load_state(state_path) if args.skip_browser else refresh_browser(sample, state_path, args.browser)
    if args.refresh_only:
        return 0
    errors = 0
    cache = {}
    with make_session(state) as session:
        # До перезаписи результата проверяем, что доступ к поиску работает.
        cache[sample] = get_price_for_article(sample, session, state)
        with args.output.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, FIELDS, delimiter=";")
            writer.writeheader()
            for index, article in enumerate(articles, 1):
                if article and article not in cache:
                    time.sleep(1.5)
                    try:
                        cache[article] = get_price_for_article(article, session, state)
                    except ParserError as exc:
                        errors += 1
                        cache[article] = {"Артикул": article, "Статус": "Ошибка: " + str(exc)}
                writer.writerow(cache[article] if article else {"Артикул": "", "Статус": "Пустой артикул"})
                target.flush()
                print(f"[{index}/{len(articles)}] {article or '(пусто)'}")
    print(f"Готово: {args.output}. Артикулов с ошибками: {errors}.")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ParserError, OSError, ValueError, EOFError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        sys.exit(130)
