# Pin landing preview

This is a standalone static RU/EN presentation for issue 21. Runtime and local payment foundations are accepted. Local checks and public Telegram destination resolution are recorded in the issue evidence; deployment and overall release acceptance remain separate.

## Vercel static hosting

Create a Vercel project from the repository and set its **Root Directory** to `landing`. Leave the framework as Other/None; `vercel.json` stages only `index.html`, `privacy.html`, `terms.html`, `app.js`, `styles.css`, and `site-config.json` into `public/` for static output. The README and Python validation/test files are not published. No Vercel runtime environment variables or secrets are needed; the bot and database remain on AWS.

Link the project to the intended GitHub repository and branch to enable Vercel's normal preview and production deployments. The `*.vercel.app` HTTPS address becomes available after the first successful deployment. Deployment has not been run or verified yet.

## Local preview

From the repository root, run:

```powershell
python -m http.server 4173 --directory landing
```

Open `http://127.0.0.1:4173/`. Choose RU or EN with the language selector. The owner-selected `site-config.json` points Telegram and support actions to `https://t.me/pin_appbot`; support instructions identify `/support` and `/paysupport`. Blank destinations still disable their actions. The banner and offer note state that the local page does not establish service, provider, or offer availability. Use a local HTTP server because the page loads its JSON configuration with `fetch`.

## Before publication

Set the exact public bot URL and actual support destination in `site-config.json`, without a bot token. Preserve the two-key JSON shape (`telegram_url`, `support_url`). Then run:

```powershell
python landing/validate_landing.py
```

This fails when either target is blank, malformed, or a placeholder and checks local page links. It only checks URL shape and local files; it does not prove the destination is reachable or belongs to Pin. Actual local navigation resolved the configured public Telegram page, as recorded in [the destination evidence](../_docs/evidence/issue-21-bot-target-local.md). Deployed-page navigation, applicable account eligibility, privacy/legal review, and deployment authorization remain separate publication requirements. Actual Stars payment testing was removed from required acceptance by the owner (SPEC-001 section 11).

## Checks

Run the focused config and link checks with:

```powershell
python -m unittest discover -s landing -p 'test_*.py' -v
python landing/validate_landing.py
```

Both commands pass against the configured local preview. Passing checks do not authorize publication or establish release readiness.
