"""Поиск артикулов в Chrome и сохранение цен из HTML. Python 3.10+."""

import csv
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = "https://www.elcomspb.ru/"
FOLDER = Path(__file__).resolve().parent
INPUT_FILE = FOLDER / "Артикулы_3032.csv"
OUTPUT_FILE = FOLDER / "prices_elcom.csv"
WAIT_SECONDS = 20
SEARCH_SELECTOR = (
    "#title-search-input, #title-search-input-desktop, "
    "#title-search-input-mobile, input[type='search'], input[name='q'], "
    "input.search-input, input[placeholder*='иск']"
)


def clean_price(raw):
    # Сохраняем ваше правило: только целая часть цены.
    raw = unescape(raw or "")
    raw = re.split(r"[,.]", raw, maxsplit=1)[0]
    return re.sub(r"\D", "", raw) or None


class PriceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.primary = None
        self.fallback = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        price = clean_price(attrs.get("data-base-price"))
        if not price:
            return
        if self.fallback is None:
            self.fallback = price
        if (
            self.primary is None
            and tag == "strong"
            and "thisPrice" in (attrs.get("class") or "").split()
        ):
            self.primary = price


def extract_price(html):
    parser = PriceParser()
    parser.feed(html)
    parser.close()
    return parser.primary or parser.fallback


def visible_search_input(driver):
    for element in driver.find_elements(By.CSS_SELECTOR, SEARCH_SELECTOR):
        if element.is_displayed() and element.is_enabled():
            return element
    return False


def search_price(driver, article):
    driver.get(BASE_URL)
    wait = WebDriverWait(driver, WAIT_SECONDS)
    search_input = wait.until(visible_search_input)
    old_page = driver.find_element(By.TAG_NAME, "html")
    old_url = driver.current_url

    # Оставляем ввод через JavaScript из исходного рабочего поиска.
    driver.execute_script(
        "arguments[0].value = arguments[1];"
        "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
        search_input, article,
    )
    search_input.send_keys(Keys.ENTER)

    # Сначала ждём перехода, чтобы не взять цену с предыдущей страницы.
    wait.until(
        lambda browser: EC.staleness_of(old_page)(browser)
        or browser.current_url != old_url
    )
    wait.until(
        lambda browser: browser.execute_script("return document.readyState")
        == "complete"
    )
    # Цена может появиться позже основной страницы: перечитываем её HTML.
    try:
        return wait.until(lambda browser: extract_price(browser.page_source))
    except TimeoutException:
        return None


def main():
    with INPUT_FILE.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=";")
        if "Артикул" not in (reader.fieldnames or []):
            raise ValueError("Во входном CSV нужен столбец «Артикул»")
        articles = [
            article for row in reader
            if (article := (row.get("Артикул") or "").strip())
            and not article.startswith("#")
        ]

    driver = webdriver.Chrome()  # Обычный видимый браузер.
    driver.set_page_load_timeout(60)
    try:
        with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output, delimiter=";")
            writer.writerow(["Артикул", "Цена", "Статус"])
            for index, article in enumerate(articles, 1):
                price = None
                try:
                    price = search_price(driver, article)
                    status = "OK" if price is not None else "ЦЕНА НЕ НАЙДЕНА"
                except WebDriverException as error:
                    status = "ОШИБКА ПОИСКА ИЛИ ЗАГРУЗКИ"
                    print(f"{article}: {error.msg}", flush=True)
                writer.writerow([article, price or "", status])
                output.flush()
                print(
                    f"[{index}/{len(articles)}] {article}: {price or status}",
                    flush=True,
                )
    finally:
        driver.quit()
    print(f"Готово. Результат: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
