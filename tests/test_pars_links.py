"""Offline tests only. No browser is launched and no network requests are made."""

import contextlib
import csv
import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pars_links as pars


class LinkParserTests(unittest.TestCase):
    def test_user_example_and_whitespace(self):
        html = '''<div>Арт:&nbsp;143100025</div>
                  <span class="price-current"> \n 8 372,34 ₽ </span>
                  <div>Арт:&#160;00123</div>
                  <b class="other price-current active">1\u202f234,5 ₽</b>'''
        self.assertEqual(pars.extract_pairs(html), [("143100025", "8372,34"), ("00123", "1234,50")])

    def test_next_article_is_boundary(self):
        html = '<p>Арт:&nbsp;A</p><p>Арт:&nbsp;B</p><b class="price-current">10 ₽</b>'
        self.assertEqual(pars.extract_pairs(html), [("B", "10,00")])

    def test_nested_price(self):
        html = '<p>Арт: 001</p><div class="price-current"><span>8 372</span>,<b>34</b>&nbsp;₽</div>'
        self.assertEqual(pars.extract_pairs(html), [("001", "8372,34")])

    def test_first_price_only_and_exact_deduplication(self):
        html = '<p>Арт: A</p><b class="price-current">10 ₽</b><b class="price-current">20 ₽</b>'
        self.assertEqual(pars.extract_pairs(html * 2), [("A", "10,00")])

    def test_conflicting_prices_are_kept(self):
        html = '<p>Арт: A</p><b class="price-current">10 ₽</b><p>Арт: A</p><b class="price-current">20 ₽</b>'
        self.assertEqual(pars.extract_pairs(html), [("A", "10,00"), ("A", "20,00")])

    def test_scripts_comments_old_prices_and_no_article(self):
        html = '''<!-- Арт: bad --><script>Арт: bad</script>
          <b class="price-current">500 ₽</b><p>Арт: good</p>
          <s class="price-old">900 ₽</s><b class="price-current">0 ₽</b>'''
        self.assertEqual(pars.extract_pairs(html), [("good", "0,00")])

    def test_invalid_prices(self):
        for price in ("По запросу", "", "NaN", "-1 ₽", "12,345 ₽"):
            self.assertEqual(pars.extract_pairs(f'<p>Арт: A</p><b class="price-current">{price}</b>'), [])

    def test_input_formats(self):
        cases = [
            'https://proconsim.ru/a\nhttps://proconsim.ru/b\n',
            'link\n"https://proconsim.ru/a"\n"https://proconsim.ru/b"\n',
            'url;\nhttps://proconsim.ru/a;\nhttps://proconsim.ru/b;\n',
        ]
        for text in cases:
            with patch.object(Path, "read_text", return_value=text):
                self.assertEqual(pars.read_links(Path("link.csv")),
                                 ["https://proconsim.ru/a", "https://proconsim.ru/b"])

    def test_external_url_rejected(self):
        with patch.object(Path, "read_text", return_value="https://proconsim.ru.evil.org/a"):
            with self.assertRaises(pars.ParserError):
                pars.read_links(Path("link.csv"))

    def test_collect_rendered_page_without_network(self):
        page = Mock()
        page.goto.return_value.status = 200
        page.url = "https://proconsim.ru/a"
        page.content.return_value = '<p>Арт: 01</p><b class="price-current">12 ₽</b>'
        page.evaluate.return_value = True
        self.assertEqual(pars.collect_page(page, page.url, RuntimeError, 0, 5), [("01", "12,00")])
        self.assertEqual(page.content.call_count, 4)

    def test_complete_output_and_failed_page(self):
        module = types.ModuleType("playwright.sync_api")
        module.Error = RuntimeError
        module.sync_playwright = Mock()
        module.sync_playwright.return_value.__enter__ = Mock(return_value=Mock())
        module.sync_playwright.return_value.__exit__ = Mock(return_value=False)
        context, page = Mock(), Mock()
        context.pages = [page]
        page.url = "https://proconsim.ru/"
        output = io.StringIO()
        with patch.dict(sys.modules, {"playwright.sync_api": module}), \
                patch.object(pars, "read_links", return_value=["https://proconsim.ru/a", "https://proconsim.ru/b", "https://proconsim.ru/c"]), \
                patch.object(pars, "open_browser", return_value=context), \
                patch.object(pars, "collect_page", side_effect=[[("001", "12,00")], [("001", "12,00"), ("002", "0,00")], pars.ParserError("blocked")]), \
                patch.object(Path, "mkdir"), patch.object(Path, "open", return_value=contextlib.nullcontext(output)), \
                patch("builtins.input", return_value=""):
            self.assertEqual(pars.main([]), 1)
        rows = list(csv.reader(io.StringIO(output.getvalue()), delimiter=";"))
        self.assertEqual(rows, [["Артикул", "Цена"], ["001", "12,00"], ["002", "0,00"]])
        context.close.assert_called_once()
        self.assertEqual(context.storage_state.call_count, 2)


if __name__ == "__main__":
    unittest.main()
