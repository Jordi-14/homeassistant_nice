# Contributing

Thanks for improving Nice BiDi-WiFi for Home Assistant.

## Safety and Privacy

This integration controls a physical gate. Test changes while the gate is
visible and keep the original Nice remote or app available.

Do not commit or attach:

- MyNice or MyNice Pro app-data backups.
- SQLite databases extracted from an iPhone backup.
- Packet captures.
- NHK usernames, passwords, source IDs, MAC addresses, serial numbers, or local
  IP addresses from a private installation.

Use fake values in examples and redact logs before opening issues.

## Development Checks

Before opening a pull request, run:

```bash
python3 -m json.tool custom_components/nice_bidiwifi/manifest.json > /dev/null
python3 -m json.tool custom_components/nice_bidiwifi/strings.json > /dev/null
python3 -m json.tool custom_components/nice_bidiwifi/translations/en.json > /dev/null
python3 -m json.tool custom_components/nice_bidiwifi/translations/ca.json > /dev/null
python3 -m pytest tests -q
python3 -m ruff check custom_components tests
python3 -m compileall -q custom_components tests scripts
```

Install test dependencies with:

```bash
python3 -m pip install -r requirements_test.txt
```

When changing UI text, update `strings.json` and all translation files. Logs,
diagnostics internals, and developer-only messages can stay in English.

GitHub Actions run tests, linting, HACS validation, and Hassfest for
repository-level checks.
