# Contributing

Small project, small rules.

1. **Set up**: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.
2. **Before a PR**: `ruff check .` and `pytest` must pass. Keep new logic in pure functions
   (`bot/parsing.py`, `bot/met.py`, `bot/reports.py`) so it can be unit-tested without Telegram,
   Sheets or Gemini.
3. **Language**: code, comments and docs in English; every user-facing string goes to
   `bot/i18n.py` in Ukrainian. No emojis in bot replies.
4. **Sheet schema**: if you add a column, update `HEADERS` in `bot/sheets.py`, the table in
   `README.md` and `tests/conftest.py`. Append new columns at the end so existing sheets keep working.
5. **Dependencies**: pin exact versions in `pyproject.toml`; everything must install on
   `linux/arm64` (the target host is an Oracle Always Free ARM VM).
6. **Secrets**: never commit `.env` or anything under `secrets/`.

Open an issue first for anything bigger than a bug fix, so we can agree on the approach.
