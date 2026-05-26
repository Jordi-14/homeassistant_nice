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
python3 -m compileall -q custom_components scripts
```

GitHub Actions run HACS validation and Hassfest for repository-level checks.
