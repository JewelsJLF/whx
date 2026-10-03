# Security policy

## Reporting a vulnerability

Please do not report suspected security vulnerabilities in a public issue or pull request. Use GitHub's private vulnerability reporting feature for this repository if it is enabled. Otherwise, contact the repository maintainers privately through a channel listed on the repository's GitHub page.

Include a description of the affected component, steps to reproduce, potential impact, and any suggested mitigation. Do not include WorkingHours backups, extracted databases, HoursTracker exports, credentials, or other private data. A minimal synthetic example is preferred.

The maintainers will review the report and coordinate any fix and disclosure with the reporter. Please allow time for investigation before sharing vulnerability details publicly.

## Scope and data handling

WHX processes local files that may contain sensitive work-hour, project, client, or personal information. Keep original backups unchanged, migrate into new copies, and do not share real exports when reporting issues.

The importer validates the schema signature observed in WorkingHours 2.17.9.0. SQLite integrity checks and the gzip container format do not guarantee restore compatibility across app versions. Preserve the original backup before any restore attempt.
