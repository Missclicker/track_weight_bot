"""`python -m bot.init_sheets` - create the spreadsheet tabs and headers (idempotent)."""

from __future__ import annotations

import asyncio
import logging

from bot.__main__ import load_settings
from bot.sheets import SheetsRepo


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    repo = SheetsRepo(settings)
    counts = await repo.ensure_schema()
    print(f"Spreadsheet: {repo.url}")
    for tab, rows in counts.items():
        print(f"  {tab:8s} {rows} rows")


if __name__ == "__main__":
    asyncio.run(main())
