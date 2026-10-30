# Changelog

All notable changes to this plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this plugin adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-10-30

### Changed

- Updated skills: `dx-code-analyzer-custom-rule-create`, `dx-code-analyzer-run`.

## [0.1.0] — 2026-09-02

### Added

- Initial release. Code-quality capabilities that previously shipped in `salesforce-development`
  now live in this standalone plugin:
  - Set up and troubleshoot Salesforce Code Analyzer (`dx-code-analyzer-configure`).
  - Run security, performance, best-practice, and style scans across the Code Analyzer engines
    (PMD, ESLint, CPD, RetireJS, Flow, SFGE, ApexGuru) (`dx-code-analyzer-run`).
  - Author custom Regex, PMD, and ESLint rules (`dx-code-analyzer-custom-rule-create`).
  - Run a multi-pillar Well-Architected architecture review with file:line evidence
    (`platform-architecture-analyze`), plus the read-only `architecture-review` agent.
