# Versioning and changelog guide

Pin has no established release pipeline or application version yet. This guide defines versioning and release-history conventions.

## Documents and product releases

Git revisions version repository documents. BR/spec IDs identify contracts; their metadata tracks status and approved revision. A revision number in a draft is not a product release.

When Pin defines a public compatibility contract, use `MAJOR.MINOR.PATCH` for releases: incompatible public-contract change, backward-compatible functionality, and backward-compatible fixes respectively. Decide how pre-1.0 releases are communicated before the first release. A database migration alone does not automatically determine a public version bump.

## Commit descriptions

Prefer Conventional Commit-style descriptions such as:

```text
docs(sdd): add GitHub issue workflow
feat(reminders): persist scheduled deliveries
fix(tasks): prevent duplicate task creation
test(reminders): cover restart recovery
```

Group changes by coherent outcome. A docs-only commit is valid for process/documentation work. Commit/push authorization remains governed by the user's request, not this example.

## Changelog

- Keep an `Unreleased` section.
- Record accepted meaningful changes under Added, Changed, Fixed, or Removed as appropriate.
- Link accepted issues/PRs once real URLs exist.
- Distinguish local preparation, merged code, published release, and verified deployment.
- Do not paste every review finding or execution log into the changelog.

For a release, collect accepted changes, verify agreed release checks, choose a version, and prepare release notes. Tagging, publishing, merging, and deployment require authorization for those actions. Conventional commit wording does not itself create releases, enforce branch protection, or configure automation.

External references: [Semantic Versioning](https://semver.org/), [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), and [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Consult current documentation before implementing release tooling.
