# Repository guidance

## Project scope

This is a Python migration tool for importing HoursTracker CSV time entries into WorkingHours app backups. It supports backup extraction, CSV import, backup compression, and combined migration. Treat database conversion as version-sensitive; do not infer a schema or silently discard unsupported data. The project is branded WHX (WorkingHours Exchange). The PyPI distribution, Python package, and console command are all named `whx`. CLI groups belong in `src/whx/commands/`, and reusable application logic belongs in `src/whx/services/`. Database operations, including extracting the database from a compressed backup, belong under the `db` command group. Tests belong in `tests/`.

## Safety and privacy

- Never inspect, copy into tests, or commit personal backups, CSV exports, or extracted databases. The `data/` directory is intentionally Git-ignored.
- Use synthetic fixtures for tests and examples.
- Preserve source backups and all existing databases. Never overwrite or modify an existing raw or extracted database. Extraction must choose a unique output filename when its selected destination exists; import must write to a new uniquely named database copy. Keep these behaviors and the actual output paths documented.
- Validate generated SQLite databases with `PRAGMA integrity_check`; report failures instead of treating them as success.
- Keep the original WorkingHours backup available before any restore attempt.

## Python conventions

- Support Python 3.14+ and prefer the standard library unless a dependency is clearly justified.
- Keep command-line tools small, explicit, and safe about input/output paths.
- Use Ruff for linting/formatting, mypy for type checking, and Bandit for security checks; keep the pre-commit hooks passing.
- Add focused tests for parsing, mapping, and database writes when those features are introduced. Tests must not depend on files under `data/`.
- Keep `README.md` current with installation, CLI usage, workflows, and important limitations. Keep relevant files under `docs/` current when architecture, format research, design decisions, or detailed procedures change; update documentation in the same change as the behavior it describes.
- Do not enforce a maximum line length or automatic wrapping for Markdown. Wrap prose manually only when it improves source readability; rendered text should reflow naturally across screen sizes. Keep line-length enforcement scoped to code.

## Git practices

- When committing is explicitly authorized, use Conventional Commits with a concise, imperative subject in the form `type(scope): subject`.
- Every commit must include a reasonably descriptive, concise body separated from the subject by a blank line. Explain what changed and why, including important behavior changes or limitations; do not merely repeat the subject.
- Commit completed, coherent units at reasonable intervals based on the change's scope. Keep unrelated changes in separate commits, and do not create commits without explicit user authorization.
