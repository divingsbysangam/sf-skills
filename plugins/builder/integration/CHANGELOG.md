# Changelog

All notable changes to this plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this plugin adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-10-30

### Changed

- Updated skills: `integration-connectivity-generate`, `platform-salesforce-connect-adapter-generate`.

## [0.2.0] — 2026-09-21

### Added

- Generate a custom Apex adapter for Salesforce Connect that exposes an external REST API as live,
  queryable External Objects (`platform-salesforce-connect-adapter-generate`).

### Fixed

- Broken links between skills, including cross-plugin links from
  `integration-connectivity-connected-app-configure`.
- Hardened the skills' helper scripts against command injection through user-supplied values.

## [0.1.0] — 2026-08-24

### Added

- Initial release. Generate connectivity such as Named Credentials, External Services, and
  REST/SOAP callouts (`integration-connectivity-generate`); configure a Connected App or External
  Client App for OAuth (`integration-connectivity-connected-app-configure`); configure Change Data
  Capture (`integration-eventing-cdc-configure`); and configure platform event subscriptions
  (`integration-eventing-subscription-configure`).
