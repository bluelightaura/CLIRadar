# Contributing

## Development environment

Keep runtime and development dependencies separate:

```bash
python -m pip install -e '.[dev]'
```

## Quality gate

Run before submitting a change:

```bash
ruff check .
pytest
bandit -q -r src
pip-audit
python -m build --no-isolation
```

Changes to parsing or crawling require a regression test. Vendor-specific
behavior belongs in an explicit profile and must not weaken the secure defaults.

Do not commit:

- real device addresses or credentials;
- `config.yml`, `.env`, raw logs, or generated catalogs;
- private keys or known-host files;
- documentation copied without redistribution permission.

Use focused commits and Conventional Commit messages such as:

```text
feat: add a vendor parameter resolver
fix: handle paginated contextual help
test: cover vendor prompt variation
docs: describe lab validation workflow
```
