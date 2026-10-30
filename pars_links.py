"""Обход link.csv в браузере и сбор пар артикул–цена в all-price.csv.

Установка: python -m pip install playwright
Запуск: python pars_links.py
"""

import argparse
import csv
import re
import sys
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


FOLDER = Path(__file__).resolve().parent
SITE = "https://proconsim.ru/"
ARTICLE = re.compile(r"Арт\s*:\s*([^\s<>]+)", re.IGNORECASE)
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}


class ParserError(RuntimeError):
    pass


def clean_price(text):
    value = re.sub(r"(?:₽|руб\.?)", "", text, flags=re.IGNORECASE)
    value = re.sub(r"\s+", "", value)
    if not re.fullmatch(r"\d+(?:[.,]\d{1,2})?", value):
        return None
    return format(Decimal(value.replace(",", ".")), ".2f").replace(".", ",")


class ProductParser(HTMLParser):
    """Первый price-current после артикула, строго до следующего артикула."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.article = None
        self.price_depth = 0
        self.price_parts = []
        self.price_article = None
        self.ignored = 0
        self.pairs = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self.ignored += 1
        if self.ignored:
            return
        if self.price_depth:
            if tag not in VOID_TAGS:
                self.price_depth += 1
            if tag == "br":
                self.price_parts.append(" ")
        elif "price-current" in dict(attrs).get("class", "").split():
            if tag not in VOID_TAGS:
                self.price_depth = 1
                self.price_parts = []
                self.price_article = self.article

    def handle_startendtag(self, tag, attrs):
        if tag not in VOID_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in ("script", "style", "template"):
                self.ignored -= 1
            return
        if self.price_depth and tag not in VOID_TAGS:
            self.price_depth -= 1
            if not self.price_depth:
                price = clean_price("".join(self.price_parts))
                if self.price_article and self.price_article == self.article and price is not None:
                    self.pairs.append((self.article, price))
                    self.article = None
                self.price_parts = []

    def handle_data(self, data):
        if self.ignored:
            return
        matches = list(ARTICLE.finditer(data))
        if matches:
            self.article = matches[-1].group(1)
            # Если артикул встретился внутри цены, эту цену использовать нельзя.
            if self.price_depth:
                self.price_article = None
        if self.price_depth:
            self.price_parts.append(data)


def extract_pairs(html):
    parser = ProductParser()
    parser.feed(html)
    parser.close()
    return list(dict.fromkeys(parser.pairs))


def is_shop_url(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return (parts.scheme in ("http", "https") and
                (host == "proconsim.ru" or host.endswith(".proconsim.ru")) and
                not parts.username and not parts.password and parts.port in (None, 80, 443))
    except ValueError:
        return False


def read_links(path):
    # Поддерживаются один URL в строке, заголовок и таблицы с ; , или табуляцией.
    text = path.read_text(encoding="utf-8-sig")
    links = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        # У одноколоночного CSV запятая может быть частью самого URL.
        direct = line.strip().strip('"')
        if re.match(r"^https?://[^\s;]+$", direct):
            cells = [direct]
        else:
            delimiter = ";" if ";" in line else "\t" if "\t" in line else ","
            cells = next(csv.reader([line], delimiter=delimiter))
        found = [cell.strip() for cell in cells if cell.strip().startswith(("https://", "http://"))]
        if not found:
            if not links and all(cell.strip().casefold() in
                                 ("", "link", "links", "url", "ссылка", "ссылки", "адрес") for cell in cells):
                continue
            raise ParserError(f"Строка {line_number} в link.csv: не найдена полная ссылка.")
        for url in found:
            if not is_shop_url(url):
                raise ParserError(f"Строка {line_number}: ожидается ссылка на proconsim.ru: {url}")
            if url not in links:
                links.append(url)
    if not links:
        raise ParserError("В link.csv нет ссылок.")
    return links


def open_browser(pw, profile, channel, browser_error):
    channels = [channel] if channel != "auto" else ["chrome", "msedge", "chromium"]
    for name in channels:
        try:
            return pw.chromium.launch_persistent_context(
                str(profile / name), channel=None if name == "chromium" else name,
                headless=False, locale="ru-RU",
            )
        except browser_error:
            continue
    raise ParserError("Не удалось открыть браузер. Закройте ранее запущенное окно этого скрипта. "
                      "Если Chrome/Edge не установлен: python -m playwright install chromium")


def collect_page(page, url, browser_error, wait_ms, scroll_rounds):
    response = page.goto(url, wait_until="load", timeout=60000)
    if response is not None and response.status >= 400:
        raise ParserError(f"HTTP {response.status}")
    if not is_shop_url(page.url):
        raise ParserError("Страница перенаправлена за пределы магазина.")
    try:
        page.locator(".price-current").first.wait_for(state="attached", timeout=20000)
    except browser_error:
        # Далее проверяем HTML: тайм-аут сам по себе не означает отсутствие товара.
        pass
    page.wait_for_timeout(wait_ms)
    pairs = dict.fromkeys(extract_pairs(page.content()))
    # Снимки во время прокрутки учитывают также ленивую загрузку карточек.
    stable_bottom = 0
    for _ in range(scroll_rounds):
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.8, 400))")
        page.wait_for_timeout(500)
        pairs.update(dict.fromkeys(extract_pairs(page.content())))
        at_bottom = page.evaluate(
            "window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 5"
        )
        stable_bottom = stable_bottom + 1 if at_bottom else 0
        if stable_bottom >= 3:
            break
    else:
        if scroll_rounds:
            print("  Внимание: достигнут лимит прокрутки; при необходимости увеличьте --scroll-rounds.")
    if not pairs:
        raise ParserError("Не найдено пар артикул–цена. Проверьте загрузку, регион и защиту сайта.")
    return list(pairs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=FOLDER / "link.csv")
    parser.add_argument("--output", type=Path, default=FOLDER / "all-price.csv")
    parser.add_argument("--profile", type=Path, default=FOLDER / ".proconsim-links")
    parser.add_argument("--browser", choices=("auto", "chrome", "msedge", "chromium"), default="auto")
    parser.add_argument("--wait", type=float, default=2, help="Ожидание после загрузки, секунды")
    parser.add_argument("--scroll-rounds", type=int, default=60, help="Лимит прокруток; 0 — отключить")
    args = parser.parse_args(argv)
    if args.wait < 0 or args.scroll_rounds < 0:
        raise ParserError("Время ожидания и число прокруток не могут быть отрицательными.")
    if args.input.resolve() == args.output.resolve():
        raise ParserError("Входной и выходной файлы должны быть разными.")
    links = read_links(args.input)
    try:
        from playwright.sync_api import sync_playwright, Error as BrowserError
    except ImportError as exc:
        raise ParserError("Установите Playwright: python -m pip install playwright") from exc
    args.profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = open_browser(pw, args.profile, args.browser, BrowserError)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            print("Этап 1. Открываю магазин для выбора региона.")
            try:
                page.goto(SITE, wait_until="domcontentloaded", timeout=60000)
            except BrowserError:
                print("Дождитесь загрузки магазина в браузере; при необходимости обновите страницу.")
            input("Выберите регион в браузере. Когда сайт загрузится, нажмите Enter здесь: ")
            page.wait_for_timeout(1000)
            if not is_shop_url(page.url):
                raise ParserError("В браузере должна быть открыта страница proconsim.ru.")
            # Куки остаются в этом же контексте, а также сохраняются для повторного запуска.
            context.storage_state(path=str(args.profile / "session.json"))
            print("Регион и куки сохранены. Этап 2: обхожу ссылки.")
            seen = set()
            prices_by_article = {}
            failed = []
            with args.output.open("w", encoding="utf-8-sig", newline="") as target:
                writer = csv.writer(target, delimiter=";")
                writer.writerow(["Артикул", "Цена"])
                target.flush()
                for index, url in enumerate(links, 1):
                    print(f"[{index}/{len(links)}] {url}")
                    try:
                        pairs = collect_page(page, url, BrowserError, int(args.wait * 1000), args.scroll_rounds)
                    except (BrowserError, ParserError) as exc:
                        failed.append(url)
                        print(f"  Ошибка: {exc}")
                        continue
                    added = 0
                    for article, price in pairs:
                        if (article, price) in seen:
                            continue
                        previous = prices_by_article.get(article)
                        if previous is not None and previous != price:
                            print(f"  У артикула {article} разные цены: {previous} и {price}; сохранены обе.")
                        prices_by_article[article] = price
                        seen.add((article, price))
                        writer.writerow([article, price])
                        added += 1
                    target.flush()
                    print(f"  Пар на странице: {len(pairs)}; добавлено: {added}.")
            context.storage_state(path=str(args.profile / "session.json"))
            print(f"Готово: {args.output}. Пар: {len(seen)}. Страниц с ошибками: {len(failed)}.")
            for url in failed:
                print(f"  Не обработана: {url}")
            return 1 if failed else 0
        finally:
            context.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ParserError, OSError, ValueError, EOFError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Остановлено. Уже записанные пары сохранены.", file=sys.stderr)
        sys.exit(130)
