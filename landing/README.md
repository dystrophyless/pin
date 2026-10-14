# Pin landing preview

This is a standalone static RU/EN presentation. It is a local preparation slice for issue 21; issue 21 remains blocked until its runtime and payment prerequisites, offer verification, and real publication targets are accepted.

## Local preview

From the repository root, run:

```powershell
python -m http.server 4173 --directory landing
```

Open `http://127.0.0.1:4173/`. Choose RU or EN with the language selector. The checked-in `site-config.json` deliberately has empty links: Telegram and support actions stay disabled and no placeholder destination appears. The banner and offer note state that the local page does not establish service, provider, or offer availability. Use a local HTTP server because the page loads its JSON configuration with `fetch`.

## Before publication

Set the exact public bot URL and actual support destination in `site-config.json`, without a bot token. Preserve the two-key JSON shape (`telegram_url`, `support_url`). Then run:

```powershell
python landing/validate_landing.py
```

This fails when either target is blank, malformed, or a placeholder and checks local page links. It only checks URL shape and local files; it does not prove the destination is reachable or belongs to Pin. The required staging check remains real navigation from the deployed RU/EN pages to the intended bot entry and configured support destination. Do not publish until that check, actual offer/provider qualification, privacy/legal review, and deployment authorization are separately complete.

## Checks

Run the focused config and link checks with:

```powershell
python -m unittest discover -s landing -p 'test_*.py' -v
python landing/validate_landing.py
```

The second command is expected to fail against the checked-in empty local preview config. This is a publication block, not a preview failure.
