# Pin

Pin's current product direction is a Telegram AI life organizer with reminders and gentle accountability. The MVP specification and task package have been reviewed, and implementation tasks are published in GitHub Issues. A local runtime/configuration foundation exists; business adapters and deployment infrastructure remain future work.

- [Product brainstorm](_docs/PROJECT_BRAINSTORM.md): existing product decisions, provisional recommendations, and open questions.
- [DDD architecture](_docs/ARCHITECTURE.md): proposed bounded contexts, technology choices, and reliability design.
- [Overall MVP specification](_docs/specifications/SPEC-001_pin_mvp.md): approved product baseline and adopted decisions, sourced directly from the brainstorm.
- [MVP issue index](_docs/specifications/SPEC-001_pin_mvp_tasks.md): 28 published implementation issues, proposed dependency order and final integration gates. Live scope, verification and workflow status belong to GitHub.
- [Project handoff](_docs/HANDOFF.md): GitHub connection, entry points, and session continuity.
- [Spec-driven development](_docs/sdd/README.md): English guides, templates, reviews, examples, and GitHub Issues workflow.
- [Development orchestration](_docs/orchestration.md): Plan/Act cycle, clean-context agents, model/effort routing, publication, verification, and closure gates.
- [Changelog](CHANGELOG.md): accepted repository changes.
- [Agent instructions](AGENTS.md): project-specific working conventions.

GitHub repository: [dystrophyless/pin](https://github.com/dystrophyless/pin). Task queue: [Issues](https://github.com/dystrophyless/pin/issues).

GitHub issue forms and the pull request template are prepared under `.github/`. They become available after these files are published on the remote default branch (`main`). This local Git checkout is connected to `origin`; the documentation changes have not been pushed. See HANDOFF for the current local/remote boundary.
# Runtime foundation

Pin targets CPython 3.12.12 (recorded in `.python-version`) and uses the committed `uv.lock` for
reproducible installs. Install the runtime, development, and speech dependencies with:

```sh
uv sync --locked --extra speech --dev
```

The speech extra is optional for gateway, application, and delivery processes.

Set role-specific settings in the environment with the `PIN_` prefix:

- All roles: `PIN_DATABASE_URL` (`postgresql://` or `postgresql+psycopg://`).
- Gateway and delivery worker: `PIN_BOT_TOKEN`.
- Gateway: `PIN_WEBHOOK_SECRET` (at least 32 characters).
- Application worker: `PIN_OPENROUTER_API_KEY`.

The commands below validate configuration and composition without connecting to external
services:

```sh
uv run pin-gateway --check
uv run pin-app-worker --check
uv run pin-delivery-worker --check
uv run pin-speech-worker --check
```

A normal invocation reports that role adapters are not yet implemented; each feature task owns
its adapter.

Run the local checks with:

```sh
uv run ruff check .
uv run mypy src
uv run pytest
uv run pin-architecture-check
```

A GitHub Actions workflow is configured to install the locked speech wheel and core dependencies
on Linux x86_64. Its execution is deferred; when run, it will verify installation and startup
without loading Whisper model weights or claiming speech accuracy/latency evidence.
