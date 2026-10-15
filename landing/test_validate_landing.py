from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validate_landing import validate, validate_support, validate_telegram


class LandingValidationTests(unittest.TestCase):
    def test_offer_copy_localizes_preview_navigation_and_voice_units(self) -> None:
        root = Path(__file__).parent
        app = (root / "app.js").read_text(encoding="utf-8")
        page = (root / "index.html").read_text(encoding="utf-8")
        self.assertIn("navPlan: 'Тарифы'", app)
        self.assertIn("navPlan: 'Plans'", app)
        self.assertIn('data-copy="previewNote"', page)
        self.assertIn(
            "Обработка каждого голосового сообщения расходует одно AI-взаимодействие", app
        )
        self.assertIn("Processing each voice message uses one AI interaction", app)
        self.assertIn("не более 3 минут", app)
        self.assertIn("up to 3 minutes", app)
        self.assertIn("target.searchParams.set('lang', language)", app)
        self.assertIn("history.replaceState(null", app)

    def test_retention_copy_scopes_periods_to_pin_data(self) -> None:
        app = (Path(__file__).parent / "app.js").read_text(encoding="utf-8")
        self.assertIn("копиям Pin, а не к сообщениям, хранящимся в Telegram", app)
        self.assertIn("not Telegram-stored messages or provider copies", app)
        self.assertIn("Pin backups are kept for up to seven days", app)

    def test_skip_link_targets_are_focusable_on_every_page(self) -> None:
        root = Path(__file__).parent
        for page in ("index.html", "privacy.html", "terms.html"):
            with self.subTest(page=page):
                text = (root / page).read_text(encoding="utf-8")
                self.assertIn('<main id="main" tabindex="-1"', text)

    def test_missing_and_placeholder_targets_block_publication(self) -> None:
        self.assertIn("required", validate_telegram(""))
        self.assertIsNotNone(validate_telegram("https://t.me/yourbot"))
        self.assertIsNotNone(validate_support("https://example.com/support"))
        self.assertIsNotNone(validate_support(""))

    def test_credential_and_wrong_scheme_targets_block_publication(self) -> None:
        self.assertIsNotNone(validate_telegram("http://t.me/pinassistant"))
        self.assertIsNotNone(validate_telegram("https://t.me/pinassistant?bot_token=secret"))
        self.assertIsNotNone(validate_support("https://help.invalid/?api_key=secret"))

    def test_complete_fixture_checks_all_local_pages_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for page in ("index.html", "privacy.html", "terms.html"):
                (root / page).write_text('<a href="privacy.html">Privacy</a>', encoding="utf-8")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "telegram_url": "https://t.me/pinassistant",
                        "support_url": "https://support.pin.example.invalid/contact",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(validate(config, root), [])
            (root / "terms.html").write_text('<a href="missing.html">Broken</a>', encoding="utf-8")
            self.assertIn("broken local link in terms.html: missing.html", validate(config, root))


if __name__ == "__main__":
    unittest.main()
