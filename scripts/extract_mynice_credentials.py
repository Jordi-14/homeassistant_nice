#!/usr/bin/env python3
"""Extract local NHK credentials from a MyNice or MyNice Pro SQLite backup."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite", type=Path, help="Path to MyNice/MyNice Pro credential SQLite database")
    parser.add_argument("--mac", help="Optional BiDi MAC address to select")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with sqlite3.connect(args.sqlite) as db:
        db.row_factory = sqlite3.Row
        if args.mac:
            row = db.execute(
                """
                SELECT ZACCESSORYMACADDRESS, ZACCESSORYUSER, ZACCESSORYPASSWORD,
                       ZCONTROLLERID, ZPERMISSIONLEVEL, ZMAINTENANCESTATE
                FROM ZACCESSORYCREDENTIALENTITY
                WHERE ZACCESSORYMACADDRESS = ?
                  AND ZACCESSORYUSER IS NOT NULL
                  AND ZACCESSORYPASSWORD IS NOT NULL
                ORDER BY Z_PK DESC
                LIMIT 1
                """,
                (args.mac.upper(),),
            ).fetchone()
        else:
            row = db.execute(
                """
                SELECT ZACCESSORYMACADDRESS, ZACCESSORYUSER, ZACCESSORYPASSWORD,
                       ZCONTROLLERID, ZPERMISSIONLEVEL, ZMAINTENANCESTATE
                FROM ZACCESSORYCREDENTIALENTITY
                WHERE ZACCESSORYUSER IS NOT NULL
                  AND ZACCESSORYPASSWORD IS NOT NULL
                ORDER BY Z_PK DESC
                LIMIT 1
                """
            ).fetchone()

    if row is None:
        raise SystemExit("No credential row found in ZACCESSORYCREDENTIALENTITY")

    print(
        json.dumps(
            {
                "target_mac": row["ZACCESSORYMACADDRESS"],
                "username": row["ZACCESSORYUSER"],
                "password": row["ZACCESSORYPASSWORD"],
                "source_id": row["ZCONTROLLERID"],
                "permission": row["ZPERMISSIONLEVEL"],
                "maintenance_state": row["ZMAINTENANCESTATE"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
