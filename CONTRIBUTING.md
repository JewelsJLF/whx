# Contributing to WHX

Thank you for helping improve WHX (WorkingHours Exchange). Contributions can include bug reports, documentation improvements, tests, and code.

## Before you contribute

For substantial changes, open an issue or discussion first to agree on the approach. Keep pull requests focused and describe the problem, the change, and any relevant limitations.

## Development setup

WHX requires Python 3.14 or newer. From the repository root, create and activate a virtual environment, then install the project and development tools:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pre-commit install
```

Run the test suite and all configured checks before submitting:

```powershell
python -m pytest
pre-commit run --all-files
```

The pre-commit checks include Ruff linting and formatting, mypy type checking, Bandit security analysis, and basic whitespace and configuration-file checks.

## Code and test expectations

- Keep CLI commands in `src/whx/commands/` and reusable application logic in `src/whx/services/`.
- Add focused tests for behavior changes. Use synthetic fixtures; never use personal WorkingHours backups, extracted databases, or HoursTracker exports as test data.
- Preserve source backups and existing databases. Write new uniquely named database outputs instead of overwriting or modifying existing files, and document how output filenames are chosen.
- Treat the WorkingHours database format as version-sensitive. Avoid assuming that a schema or undocumented behavior is stable across app versions.
- Keep the README and relevant `docs/` pages in sync with changes to commands, workflows, architecture, and limitations.
- Do not impose line-length wrapping on Markdown. Wrap prose manually only when it improves readability; rendered paragraphs should flow naturally on different screen sizes.

## Pull requests and commits

Use a clear pull request description with the motivation, implementation summary, and validation performed. Call out any compatibility, data-loss, or migration risks.

Use Conventional Commit messages, such as `fix(db): preserve zero-duration entries` or `docs: clarify migration workflow`. Keep commits focused on coherent changes, and do not include private exports, databases, or unrelated generated files.

## Reporting bugs

For ordinary bugs and feature requests, open a GitHub issue if the project repository has issue tracking enabled. Include the WHX version, Python version, operating system, command used, and a minimal reproduction where possible. Do not attach personal app backups, CSV exports, or databases; sanitize examples or recreate the issue with synthetic data.
