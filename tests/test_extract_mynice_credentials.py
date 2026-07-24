"""Tests for the MyNice credential extractor."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_mynice_credentials.py"


def _create_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db, db:
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


def _create_android_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE nhk_credentials (
            device_id TEXT NOT NULL,
            nhk_username TEXT,
            nhk_password TEXT,
            nhk_controller_id TEXT,
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL
        )
        """
    )


def _insert_android_credentials(db: sqlite3.Connection) -> None:
    db.executemany(
        """
        INSERT INTO nhk_credentials (
            device_id,
            nhk_username,
            nhk_password,
            nhk_controller_id
        ) VALUES (?, ?, ?, ?)
        """,
        [
            ("AA:BB:CC:DD:EE:FF", "old-user", "AA" * 32, "old-source"),
            ("11:22:33:44:55:66", "other-user", "BB" * 32, "other-source"),
            ("AA:BB:CC:DD:EE:FF", "new-user", "CC" * 32, "new-source"),
        ],
    )


def _create_android_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        _create_android_schema(db)
        _insert_android_credentials(db)


def _open_android_wal_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    db.execute("PRAGMA wal_autocheckpoint=0")
    _create_android_schema(db)
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _insert_android_credentials(db)
    db.commit()
    assert Path(f"{path}-wal").stat().st_size > 0
    return db


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


def test_extractor_accepts_extracted_app_container_directory(tmp_path: Path) -> None:
    """Test the extractor can find the credential database in an app export folder."""
    db_path = tmp_path / "Container" / "Library" / "Application Support" / "CachedData.sqlite"
    db_path.parent.mkdir(parents=True)
    _create_db(db_path)

    credentials = _run_extractor(tmp_path)

    assert credentials["target_mac"] == "AA:BB:CC:DD:EE:FF"
    assert credentials["username"] == "new-user"


def test_extractor_accepts_zip_like_imazing_export(tmp_path: Path) -> None:
    """Test the extractor can read a zip-like iMazing app-data export directly."""
    source = tmp_path / "source"
    db_path = source / "Container" / "Library" / "Application Support" / "CachedData.sqlite"
    db_path.parent.mkdir(parents=True)
    _create_db(db_path)

    export_path = tmp_path / "MyNice.imazingapp"
    with zipfile.ZipFile(export_path, "w") as export:
        export.write(db_path, "Container/Library/Application Support/CachedData.sqlite")

    credentials = _run_extractor(export_path)

    assert credentials["target_mac"] == "AA:BB:CC:DD:EE:FF"
    assert credentials["username"] == "new-user"


def test_extractor_accepts_android_nhk_extra_database(tmp_path: Path) -> None:
    """Test normalized extraction from the Android Room credential table."""
    db_path = tmp_path / "nhk_extra"
    _create_android_db(db_path)

    credentials = _run_extractor(
        db_path,
        "--mac",
        "aa:bb:cc:dd:ee:ff",
    )

    assert credentials == {
        "maintenance_state": None,
        "password": "CC" * 32,
        "permission": None,
        "source_id": "new-source",
        "target_mac": "AA:BB:CC:DD:EE:FF",
        "username": "new-user",
    }


def test_extractor_reads_android_credentials_from_wal(tmp_path: Path) -> None:
    """Test a credential row that exists only in the Android WAL is visible."""
    db_path = tmp_path / "app_data" / "databases" / "nhk_extra"
    db_path.parent.mkdir(parents=True)
    with closing(_open_android_wal_db(db_path)):
        credentials = _run_extractor(tmp_path)

    assert credentials["target_mac"] == "AA:BB:CC:DD:EE:FF"
    assert credentials["username"] == "new-user"


def test_extractor_preserves_android_wal_sidecars_from_archive(
    tmp_path: Path,
) -> None:
    """Test Android app-data archives extract the database and WAL together."""
    source_path = tmp_path / "source" / "databases" / "nhk_extra"
    source_path.parent.mkdir(parents=True)
    with closing(_open_android_wal_db(source_path)):
        export_path = tmp_path / "android_app_data.zip"
        with zipfile.ZipFile(export_path, "w") as export:
            for suffix in ("", "-wal", "-shm"):
                export.write(
                    Path(f"{source_path}{suffix}"),
                    f"databases/nhk_extra{suffix}",
                )
        credentials = _run_extractor(export_path)

    assert credentials["target_mac"] == "AA:BB:CC:DD:EE:FF"
    assert credentials["username"] == "new-user"


def test_ios_and_android_backups_normalize_local_credentials(
    tmp_path: Path,
) -> None:
    """Synthetic platform backups produce the same local credential fields."""
    ios_path = tmp_path / "CachedData.sqlite"
    android_path = tmp_path / "nhk_extra"
    _create_db(ios_path)
    _create_android_db(android_path)

    ios = _run_extractor(ios_path)
    android = _run_extractor(android_path)
    local_fields = ("target_mac", "username", "password", "source_id")

    assert {field: ios[field] for field in local_fields} == {
        field: android[field] for field in local_fields
    }


def test_extractor_reports_missing_database(tmp_path: Path) -> None:
    """Test a folder without a credential database returns a clear error."""
    (tmp_path / "nice.log").write_text("no sqlite here", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "No local credential row found" in result.stderr
