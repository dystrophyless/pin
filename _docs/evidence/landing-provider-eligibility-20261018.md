# Landing provider eligibility — 2026-10-18

**Result: unresolved commercial-hosting decision; no provider eligibility pass.** This does not change the completed functional landing checks or authorize a purchase, plan upgrade, provider migration, or publication change.

## Confirmed observations

- The owner selected Vercel's temporary domain for the landing. The existing deployment uses Hobby; see [Vercel deployment evidence](vercel-landing-20261018.md) and [rendered landing observations](landing-browser-20261018.md).
- The landing advertises a paid subscription of 250 Stars per 30 days and links to the approved Telegram bot. The source offer is in `landing/index.html`; the rendered RU/EN offer was checked in the browser evidence above.
- Vercel's current [Fair Use Guidelines, Commercial usage](https://vercel.com/docs/limits/fair-use-guidelines#commercial-usage), retrieved on this date, restrict Hobby to non-commercial personal use and require Pro or Enterprise for commercial usage. Its examples expressly include advertising the sale of a product or service. This is broader than taking a payment directly on the landing.
- The current [Terms of Service](https://vercel.com/legal/terms) also restrict Hobby use to personal or non-commercial use. The documentation result was independently retrieved through Context7's `/websites/vercel` index; the official guidelines page, rather than an indexed snippet alone, was read directly.

## Application and remaining decision

**Inference:** the current paid-offer referral page matches Vercel's stated example of advertising a product or service. Preview wording and the absence of an actual payment on the page do not establish Hobby eligibility.

The owner must select an eligible hosting arrangement or obtain provider confirmation before this commercial-hosting gate can pass. In the dated follow-up, the owner chose **remain on Vercel; decide the plan separately**. This confirms the provider choice, not commercial eligibility or authorization to buy a plan. Do not silently remove the approved paid offer or declare the product non-commercial to bypass this decision. Functional HTTPS, referral links, Git-triggered deployment, and RU/EN rendering remain separate, previously observed facts.

This finding belongs to the existing deployment/paid/release gates. It does not reinstate actual Stars transaction testing or the removed quantitative model benchmark.
