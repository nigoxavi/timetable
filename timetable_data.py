"""Timetable data loader and allocation policy defaults.

Keep ``Timetable.txt`` beside this file.  It contains the department's existing
``TIMETABLE = {...}`` dictionary and is parsed without executing arbitrary code.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


DATA_FILE = Path(__file__).with_name("Timetable.txt")
DAY_ORDERS = tuple(f"Day {number}" for number in range(1, 7))
PERIODS = (1, 2, 3, 4, 5)
MAX_DAILY_PERIODS = 3

# Change these values here, or override them from the Streamlit sidebar.
DEFAULT_HOD = "Dr. S. Xavier"
DEFAULT_RESTRICTED_STAFF: tuple[str, ...] = ()


def load_timetable(path: str | Path = DATA_FILE) -> dict[str, dict[str, dict[int, Any]]]:
    """Load the original timetable dictionary from ``Timetable.txt`` safely."""
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8-sig").strip()
    if "=" in text:
        variable, text = text.split("=", 1)
        if variable.strip() != "TIMETABLE":
            raise ValueError(f"Expected TIMETABLE assignment in {source_path.name}")
    timetable = ast.literal_eval(text)
    if not isinstance(timetable, dict) or not timetable:
        raise ValueError("TIMETABLE must be a non-empty dictionary")
    return timetable


TIMETABLE = load_timetable()
STAFF = tuple(TIMETABLE)

