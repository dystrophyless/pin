# Pin project instructions

## Agent skills

- Do not invoke or follow any Superpowers skill in this project. The user explicitly replaced that workflow with Ponytail.
- Use the project-local Ponytail skill at `.agents/skills/ponytail/SKILL.md` for coding, fixes, refactoring, reviews, and dependency decisions. Read it before applying it; use its default full mode unless the user selects another mode.
- Ponytail complements the project's DDD architecture. Preserve the agreed domain boundaries, invariants, validation, and reliability requirements; do not flatten them merely to reduce code.
- Follow direct user instructions before skill conventions. Do not add mandatory approval stages, subagents, or extra process unless requested or necessary for the task.

## Project references

- `PROJECT_BRAINSTORM.md` records product decisions and unresolved questions.
- `docs/ARCHITECTURE.md` defines the proposed DDD architecture and technical decisions.

The Superpowers plugin is disabled in `.codex/config.toml` for this project. Project-local Ponytail is installed from `DietrichGebert/ponytail`, path `skills/ponytail`, without global plugin hooks.
