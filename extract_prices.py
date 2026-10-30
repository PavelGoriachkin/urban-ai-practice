"""Извлечение цены из HTML-файлов с одной карточкой товара. Python 3.10+."""

import argparse
import csv
import re
from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter


class PriceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.span_depth = 0
        self.parts = []
        self.price = None

    def handle_starttag(self, tag, attrs):
        if self.price is not None or tag != "span":
            return
        if self.span_depth:
            self.span_depth += 1
        elif "price-box__val" in (dict(attrs).get("class") or "").split():
            self.span_depth = 1

    def handle_data(self, data):
        if self.span_depth:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self.span_depth:
            self.span_depth -= 1
            if not self.span_depth:
                # HTML-сущности (например, &#8381; и &nbsp;) уже расшифрованы.
                self.price = re.sub(r"[^0-9.,]", "", "".join(self.parts))


def extract_price_from_html(file_path: Path) -> str | None:
    parser = PriceParser()
    parser.feed(file_path.read_text(encoding="utf-8-sig"))
    parser.close()
    return parser.price or None


def main():
    script_folder = Path(__file__).resolve().parent
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument(
        "folder", nargs="?", type=Path, default=script_folder / "html_files",
        help="Папка с HTML (по умолчанию html_files рядом со скриптом)",
    )
    arguments.add_argument(
        "--output", type=Path, default=script_folder / "prices.csv",
        help="Выходной CSV (по умолчанию prices.csv рядом со скриптом)",
    )
    args = arguments.parse_args()
    if not args.folder.is_dir():
        arguments.error(f"Папка не найдена: {args.folder}")

    files = sorted(
        path for path in args.folder.iterdir()
        if path.is_file() and path.suffix.lower() in {".html", ".htm"}
    )
    if not files:
        print(f"В папке нет HTML-файлов: {args.folder}", flush=True)
        return

    print(f"Найдено файлов: {len(files)}", flush=True)
    started = perf_counter()
    found = 0
    with args.output.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Артикул", "Цена"])
        for index, path in enumerate(files, 1):
            try:
                price = extract_price_from_html(path)
            except (OSError, UnicodeError) as error:
                print(f"Ошибка: {path.name}: {error}", flush=True)
                price = None
            if price is not None:
                found += 1
            else:
                print(f"Цена не найдена: {path.name}", flush=True)
            # Пустая цена сохраняется, чтобы ни один файл не потерялся из отчёта.
            writer.writerow([path.stem, price or ""])
            if index % 50 == 0 or index == len(files):
                print(f"Обработано {index}/{len(files)}, цен найдено: {found}", flush=True)

    print(f"Готово за {perf_counter() - started:.1f} с. Результат: {args.output.resolve()}")


if __name__ == "__main__":
    main()
