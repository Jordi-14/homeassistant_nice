"""Tests for public documentation structure and local links."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    *(ROOT / "docs").glob("*.md"),
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_public_documentation_local_links_exist() -> None:
    """Relative links in public Markdown point to repository files."""
    missing: list[tuple[str, str]] = []
    for document in PUBLIC_DOCUMENTS:
        for target in MARKDOWN_LINK.findall(document.read_text()):
            path_text = target.split("#", 1)[0]
            if (
                not path_text
                or "://" in path_text
                or path_text.startswith(("mailto:", "/"))
            ):
                continue
            target_path = (document.parent / path_text).resolve()
            if not target_path.exists():
                missing.append(
                    (
                        str(document.relative_to(ROOT)),
                        target,
                    )
                )

    assert missing == []


def test_optional_future_additions_are_publicly_documented() -> None:
    """Request-driven Phases 7–9 remain visible in public documentation."""
    roadmap = (ROOT / "docs" / "future_additions.md").read_text()

    assert "## Phase 7 — CORE foundation and child devices" in roadmap
    assert "## Phase 8 — CORE scenes and specialist platforms" in roadmap
    assert "## Phase 9 — installer writes and firmware" in roadmap
    assert "[Optional Future Additions](docs/future_additions.md)" in (
        ROOT / "README.md"
    ).read_text()
