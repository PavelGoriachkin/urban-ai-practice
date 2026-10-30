"""Offline regression checks; run: python -m unittest discover -s tests -p test_proconsim_parser.py"""

import contextlib
import csv
import json
import shutil
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pars


@contextlib.contextmanager
def test_directory():
    # Inherit Windows ACLs instead of tempfile's private-directory permissions.
    root = Path(__file__).resolve().parents[1]
    path = root / ("proconsim-test-" + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        resolved = path.resolve()
        if resolved.parent != root or not resolved.name.startswith("proconsim-test-"):
            raise RuntimeError("Unexpected test directory")
        shutil.rmtree(resolved)


class ParserChecks(unittest.TestCase):
    def test_csv_preserves_identifiers(self):
        with test_directory() as folder:
            path = folder / "input.csv"
            path.write_text("\ufeffАртикул;Имя\n00123;A\nNA;B\n;C\n", encoding="utf-8")
            self.assertEqual(pars.read_articles(path), ["00123", "NA", ""])

    def test_prices(self):
        cases = [("1\u00a0234,50", 1234.5), ("0", 0), (None, None),
                 ("NaN", None), ("inf", None), ("bad", None), (False, None)]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(pars.parse_price(raw), expected)

    def test_allowed_api_domains(self):
        self.assertTrue(pars.is_search_url("https://sort.diginetica.net/search?st=1"))
        self.assertFalse(pars.is_search_url("https://diginetica.net.evil.org/search"))
        self.assertFalse(pars.is_search_url("http://sort.diginetica.net/search"))

    def test_exact_match_and_scalar_attribute(self):
        product = {"attributes": {"артикул": "00123"}, "price": "15,4",
                   "available": "false", "name": "Test"}
        with patch.object(pars, "fetch_data", return_value={"products": [product]}):
            item = pars.get_price_for_article("00123", None, {})
            self.assertEqual(item["Цена"], 15.4)
            self.assertFalse(item["Доступно"])
            self.assertEqual(pars.get_price_for_article("123", None, {})["Статус"], "Не найден")

    def test_pagination(self):
        wrong = {"attributes": {"артикул": ["other"]}, "price": 1}
        right = {"attributes": {"артикул": ["X"]}, "price": 2}
        with patch.object(pars, "fetch_data", side_effect=[
            {"products": [wrong] * 100}, {"products": [right]},
        ]) as fetch:
            self.assertEqual(pars.get_price_for_article("X", None, {})["Цена"], 2)
            self.assertEqual(fetch.call_args.args[-1], 100)

    def test_rejected_key_is_error(self):
        session = Mock()
        session.get.return_value.status_code = 400
        with self.assertRaises(pars.ParserError):
            pars.fetch_data(session, {"endpoint": "https://sort.diginetica.net/search", "params": {}}, "001", 0)

    def test_retry_transient_error(self):
        session = Mock()
        ok = Mock(status_code=200)
        ok.json.return_value = {"products": []}
        session.get.side_effect = [Mock(status_code=503), ok]
        with patch.object(pars.time, "sleep"):
            self.assertEqual(pars.fetch_data(session, {"endpoint": "unused", "params": {}}, "X", 0), {"products": []})
        self.assertEqual(session.get.call_count, 2)

    def test_cookie_domains(self):
        with pars.make_session({"user_agent": "test", "cookies": [
            {"name": "shop", "value": "x", "domain": "proconsim.ru", "secure": True},
        ]}) as session:
            request = session.prepare_request(pars.requests.Request("GET", "https://sort.diginetica.net/search"))
            self.assertNotIn("Cookie", request.headers)

    def test_browser_stage_saves_current_key_region_and_cookies(self):
        module = types.ModuleType("playwright.sync_api")
        module.Error = RuntimeError
        pw = Mock()
        module.sync_playwright = Mock()
        module.sync_playwright.return_value.__enter__ = Mock(return_value=pw)
        module.sync_playwright.return_value.__exit__ = Mock(return_value=False)
        context, page, response = Mock(), Mock(), Mock()
        pw.chromium.launch_persistent_context.return_value = context
        context.pages = [page]
        context.cookies.return_value = [
            {"name": "city", "value": "Moscow", "domain": ".proconsim.ru"},
            {"name": "unrelated", "value": "secret", "domain": ".example.org"},
        ]
        page.evaluate.return_value = "Browser UA"
        page.locator.return_value.all.return_value = [Mock()]
        response.url = "https://sort.diginetica.net/search?st=00123&apiKey=current&regionId=77"
        response.status = 200
        response.json.return_value = {"products": []}
        callbacks = {}
        context.on.side_effect = lambda event, callback: callbacks.update({event: callback})
        page.wait_for_timeout.side_effect = lambda _: callbacks["response"](response)
        with test_directory() as folder, patch.dict(sys.modules, {"playwright.sync_api": module}), \
                patch("builtins.input", return_value=""):
            state_path = folder / "session.json"
            state = pars.refresh_browser("00123", state_path, "chrome")
            self.assertEqual(state["params"]["apiKey"], "current")
            self.assertEqual(state["params"]["regionId"], "77")
            self.assertEqual(len(state["cookies"]), 1)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), state)
        context.close.assert_called_once()

    def test_complete_csv_with_saved_session(self):
        state = {"endpoint": "https://sort.diginetica.net/search", "params": {"apiKey": "test"},
                 "user_agent": "test", "cookies": []}
        with test_directory() as folder:
            source, target = folder / "input.csv", folder / "output.csv"
            source.write_text("Артикул\n00123\n00123\nmissing\n", encoding="utf-8")
            (folder / "session.json").write_text(json.dumps(state), encoding="utf-8")
            product = {"attributes": {"артикул": ["00123"]}, "price": 25, "available": True}
            with patch.object(pars, "fetch_data", side_effect=[{"products": [product]}, {"products": []}]), \
                    patch.object(pars.time, "sleep"):
                self.assertEqual(pars.main(["--input", str(source), "--output", str(target),
                                            "--state-dir", str(folder), "--skip-browser"]), 0)
            with target.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter=";"))
            self.assertEqual([row["Артикул"] for row in rows], ["00123", "00123", "missing"])
            self.assertEqual(rows[-1]["Статус"], "Не найден")

    def test_preflight_preserves_output(self):
        with test_directory() as folder:
            source, target = folder / "input.csv", folder / "output.csv"
            source.write_text("Артикул\nX\n", encoding="utf-8")
            target.write_text("previous", encoding="utf-8")
            with patch.object(pars, "refresh_browser", return_value={"user_agent": "test"}), \
                    patch.object(pars, "get_price_for_article", side_effect=pars.ParserError("test")):
                with self.assertRaises(pars.ParserError):
                    pars.main(["--input", str(source), "--output", str(target), "--state-dir", str(folder)])
            self.assertEqual(target.read_text(), "previous")


if __name__ == "__main__":
    unittest.main()
