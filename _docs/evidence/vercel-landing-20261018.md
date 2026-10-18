# Vercel landing hosting — 2026-10-18

The owner selected Vercel's HTTPS subdomain for the landing while the bot and database remain on AWS. The existing Vercel account imported `dystrophyless/pin`, with project root `landing`, preset Other, and the committed static-output configuration. The owner added Pin to the existing GitHub app's selected repositories. No bot/database credentials were supplied to Vercel and no paid upgrade or custom domain was purchased.

Production URL: **https://pin-landing-xi.vercel.app/**. Vercel reported the initial production deployment Ready and the repository source as `main`, with the static-hosting configuration commit. A subsequent documentation push triggered another production deployment automatically; root observed it reach Ready for that new commit. This verifies the GitHub-to-Vercel update path, in addition to initial publication.

Root inspected the live Russian page, switched to English, and followed its primary CTA to the Telegram launch page for `@pin_appbot`. HTTPS requests returned 200 for the homepage, localized privacy/terms pages, JavaScript, CSS and public destination JSON. Requests for the Python validator, README and Vercel configuration returned 404. The build stages exactly six public files; the local landing validation and six focused tests passed.

The page retains accurate preview/offer-eligibility limitations. HTTPS hosting does not establish provider eligibility, financial-retention compliance, full bot integration or final owner acceptance. The earlier AWS-hosted responsive/browser checks remain separate compatible presentation evidence. AWS runtime and recovery infrastructure were not migrated.
