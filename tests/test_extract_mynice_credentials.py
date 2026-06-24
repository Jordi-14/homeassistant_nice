"""Tests for the MyNice credential extractor."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_mynice_credentials.py"


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE ZACCESSORYCREDENTIALENTITY (
                Z_PK INTEGER PRIMARY KEY,
                ZACCESSORYMACADDRESS TEXT,
                ZACCESSORYUSER TEXT,
                ZACCESSORYPASSWORD TEXT,
                ZCONTROLLERID TEXT,
                ZPERMISSIONLEVEL INTEGER,
                ZMAINTENANCESTATE INTEGER
            )
            """
        )
        db.executemany(
            """
            INSERT INTO ZACCESSORYCREDENTIALENTITY (
                Z_PK,
                ZACCESSORYMACADDRESS,
                ZACCESSORYUSER,
                ZACCESSORYPASSWORD,
                ZCONTROLLERID,
                ZPERMISSIONLEVEL,
                ZMAINTENANCESTATE
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "AA:BB:CC:DD:EE:FF", "old-user", "AA" * 32, "old-source", 1, 0),
                (2, "11:22:33:44:55:66", "other-user", "BB" * 32, "other-source", 1, 0),
                (3, "AA:BB:CC:DD:EE:FF", "new-user", "CC" * 32, "new-source", 1, 0),
            ],
        )


def _run_extractor(path: Path, *args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_extractor_without_mac_returns_newest_credential(tmp_path: Path) -> None:
    """Test the default extractor path returns the newest usable credential."""
    db_path = tmp_path / "CachedData.sqlite"
    _create_db(db_path)

    credentials = _run_extractor(db_path)

    assert credentials["target_mac"] == "AA:BB:CC:DD:EE:FF"
    assert credentials["username"] == "new-user"
    assert credentials["password"] == "CC" * 32
    assert credentials["source_id"] == "new-source"


def test_extractor_with_mac_returns_newest_matching_credential(tmp_path: Path) -> None:
    """Test the MAC-filtered extractor path ignores stale matching rows."""
    db_path = tmp_path / "CachedData.sqlite"
    _create_db(db_path)

    credentials = _run_extractor(db_path, "--mac", "aa:bb:cc:dd:ee:ff")

    assert credentials["target_mac"] == "AA:BB:CC:DD:EE:FF"
    assert credentials["username"] == "new-user"
    assert credentials["password"] == "CC" * 32
    assert credentials["source_id"] == "new-source"
