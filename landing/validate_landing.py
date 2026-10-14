"""Fail-closed, local structural gate for the Pin landing publication inputs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlparse


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.targets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.targets.append(value)


def validate(config_path: Path, site: Path) -> list[str]:
    issues: list[str] = []
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["cannot read valid JSON configuration"]
    if not isinstance(config, dict) or set(config) != {"telegram_url", "support_url"}:
        return ["configuration must contain exactly telegram_url and support_url"]

    telegram = validate_telegram(config["telegram_url"])
    support = validate_support(config["support_url"])
    if telegram:
        issues.append(f"telegram_url: {telegram}")
    if support:
        issues.append(f"support_url: {support}")

    required_pages = {"index.html", "privacy.html", "terms.html"}
    for page in sorted(required_pages):
        if not (site / page).is_file():
            issues.append(f"required page missing: {page}")
    for page in sorted(required_pages & {p.name for p in site.glob("*.html")}):
        parser = Links()
        try:
            parser.feed((site / page).read_text(encoding="utf-8"))
        except OSError:
            issues.append(f"cannot read page: {page}")
            continue
        for target in parser.targets:
            if target.startswith(("https://", "http://", "mailto:", "tel:")) or target.startswith("#"):
                continue
            local_path = target.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
            if local_path and not (site / local_path).is_file():
                issues.append(f"broken local link in {page}: {local_path}")
    if issues:
        return issues
    return []


def validate_telegram(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "required; configure the intended public bot URL"
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "t.me" or parsed.username or parsed.password:
        return "must be an HTTPS Telegram t.me bot link"
    if not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_]{4,31}/?", parsed.path):
        return "must identify a bot username without a token or placeholder"
    if parsed.fragment or any(key.lower() in {"token", "key", "api_key", "bot_token"} for key, _ in parse_qsl(parsed.query)):
        return "must not contain a credential or fragment"
    if any(word in parsed.path.lower() for word in ("placeholder", "yourbot", "example")):
        return "placeholder bot links cannot be published"
    return None


def validate_support(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "required; configure the real support destination"
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return "must be an HTTPS support URL"
    if parsed.hostname.lower() in {"example.com", "example.org", "example.net", "localhost"}:
        return "placeholder support destinations cannot be published"
    if any(word in value.lower() for word in ("placeholder", "your-support", "changeme")):
        return "placeholder support destinations cannot be published"
    if any(key.lower() in {"token", "key", "api_key", "bot_token"} for key, _ in parse_qsl(parsed.query)):
        return "must not contain a credential"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("site-config.json"))
    parser.add_argument("--site", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    issues = validate(args.config, args.site)
    if issues:
        print("PUBLICATION BLOCKED")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Configuration and local page links are structurally valid.")
    print("This check does not verify provider eligibility, live reachability, legal compliance, or staging navigation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
