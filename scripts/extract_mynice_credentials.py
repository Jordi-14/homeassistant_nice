#!/usr/bin/env python3
"""Extract local NHK credentials from a MyNice or MyNice Pro app-data backup."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

_KNOWN_SQLITE_NAMES = {
    "cacheddata.sqlite": 0,
    "nhk_extra": 1,
    "mynicepro.sqlite": 2,
}
_SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

_IOS_CREDENTIAL_COLUMNS = """
    ZACCESSORYMACADDRESS, ZACCESSORYUSER, ZACCESSORYPASSWORD,
    ZCONTROLLERID, ZPERMISSIONLEVEL, ZMAINTENANCESTATE
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "backup",
        type=Path,
        help=(
            "Path to a MyNice/MyNice Pro iOS or Android credential database, "
            "an extracted app-data folder, or a zip-like app-data export"
        ),
    )
    parser.add_argument("--mac", help="Optional BiDi MAC address to select")
    return parser.parse_args()


def _candidate_priority(path: Path | PurePosixPath | str) -> tuple[int, int, str]:
    path_text = str(path)
    name = PurePosixPath(path_text).name.lower()
    return (_KNOWN_SQLITE_NAMES.get(name, 100), len(path_text), path_text.lower())


def _is_sqlite_candidate(path: Path | PurePosixPath | str) -> bool:
    name = PurePosixPath(str(path)).name.lower()
    return name in _KNOWN_SQLITE_NAMES or name.endswith(_SQLITE_SUFFIXES)


def _find_sqlite_candidates(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and _is_sqlite_candidate(path)),
        key=_candidate_priority,
    )


def _ios_credentials(row: sqlite3.Row) -> dict[str, object]:
    """Normalize one iOS Core Data credential row."""
    return {
        "target_mac": row["ZACCESSORYMACADDRESS"],
        "username": row["ZACCESSORYUSER"],
        "password": row["ZACCESSORYPASSWORD"],
        "source_id": row["ZCONTROLLERID"],
        "permission": row["ZPERMISSIONLEVEL"],
        "maintenance_state": row["ZMAINTENANCESTATE"],
    }


def _android_credentials(row: sqlite3.Row) -> dict[str, object]:
    """Normalize one Android Room credential row."""
    return {
        "target_mac": row["device_id"],
        "username": row["nhk_username"],
        "password": row["nhk_password"],
        "source_id": row["nhk_controller_id"],
        "permission": None,
        "maintenance_state": None,
    }


def _read_ios_credentials(
    db: sqlite3.Connection,
    mac: str | None,
) -> dict[str, object] | None:
    parameters: tuple[str, ...] = ()
    mac_filter = ""
    if mac:
        mac_filter = "AND UPPER(ZACCESSORYMACADDRESS) = ?"
        parameters = (mac.upper(),)
    row = db.execute(
        f"""
        SELECT {_IOS_CREDENTIAL_COLUMNS}
        FROM ZACCESSORYCREDENTIALENTITY
        WHERE ZACCESSORYUSER IS NOT NULL
          AND ZACCESSORYPASSWORD IS NOT NULL
          {mac_filter}
        ORDER BY Z_PK DESC
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    return _ios_credentials(row) if row is not None else None


def _read_android_credentials(
    db: sqlite3.Connection,
    mac: str | None,
) -> dict[str, object] | None:
    parameters: tuple[str, ...] = ()
    mac_filter = ""
    if mac:
        mac_filter = "AND UPPER(device_id) = ?"
        parameters = (mac.upper(),)
    row = db.execute(
        f"""
        SELECT device_id, nhk_username, nhk_password, nhk_controller_id
        FROM nhk_credentials
        WHERE nhk_username IS NOT NULL
          AND nhk_password IS NOT NULL
          {mac_filter}
        ORDER BY id DESC
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    return _android_credentials(row) if row is not None else None


def _read_credentials_from_database(path: Path, mac: str | None) -> dict[str, object] | None:
    with closing(
        sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    ) as db:
        db.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "ZACCESSORYCREDENTIALENTITY" in tables:
            credentials = _read_ios_credentials(db, mac)
            if credentials is not None:
                return credentials
        if "nhk_credentials" in tables:
            return _read_android_credentials(db, mac)
    return None


def _try_read_credentials_from_database(path: Path, mac: str | None) -> dict[str, object] | None:
    try:
        return _read_credentials_from_database(path, mac)
    except sqlite3.DatabaseError:
        return None


def _read_credentials_from_directory(path: Path, mac: str | None) -> dict[str, object] | None:
    for candidate in _find_sqlite_candidates(path):
        credentials = _try_read_credentials_from_database(candidate, mac)
        if credentials is not None:
            return credentials
    return None


def _archive_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    return {
        info.filename: info
        for info in archive.infolist()
        if not info.is_dir() and _is_safe_archive_name(info.filename)
    }


def _is_safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def _write_archive_entry(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(archive.read(entry))


def _read_credentials_from_archive(path: Path, mac: str | None) -> dict[str, object] | None:
    with zipfile.ZipFile(path) as archive:
        entries = _archive_entries(archive)
        candidates = sorted(
            (entry for entry in entries.values() if _is_sqlite_candidate(entry.filename)),
            key=lambda entry: _candidate_priority(entry.filename),
        )

        for candidate in candidates:
            with tempfile.TemporaryDirectory(prefix="mynice_credentials_") as tmp:
                tmp_path = Path(tmp)
                database_name = PurePosixPath(candidate.filename).name
                database_path = tmp_path / database_name
                _write_archive_entry(archive, candidate, database_path)

                for suffix in _SQLITE_SIDECAR_SUFFIXES:
                    sidecar = entries.get(f"{candidate.filename}{suffix}")
                    if sidecar is not None:
                        _write_archive_entry(archive, sidecar, tmp_path / f"{database_name}{suffix}")

                credentials = _try_read_credentials_from_database(database_path, mac)
                if credentials is not None:
                    return credentials

    return None


def _read_credentials(path: Path, mac: str | None) -> dict[str, object] | None:
    if path.is_dir():
        return _read_credentials_from_directory(path, mac)

    if not path.exists():
        raise SystemExit(f"Backup path does not exist: {path}")

    if zipfile.is_zipfile(path):
        credentials = _read_credentials_from_archive(path, mac)
        if credentials is not None:
            return credentials

    return _try_read_credentials_from_database(path, mac)


def main() -> int:
    args = parse_args()
    credentials = _read_credentials(args.backup, args.mac)
    if credentials is None:
        raise SystemExit(
            "No local credential row found in the supported iOS or Android "
            "MyNice databases. Pass the extracted app-data folder, an app-data "
            "archive, CachedData.sqlite, or Android nhk_extra with its WAL sidecars."
        )

    print(json.dumps(credentials, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
