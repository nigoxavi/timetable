"""Substitution allocation, history persistence, and report exports."""

from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


HISTORY_COLUMNS = [
    "allocation_id", "date", "day_order", "period", "class_year", "subject",
    "absent_staff", "assigned_staff", "same_class_priority", "daily_load_before",
    "daily_load_after", "weekly_extra_before", "monthly_extra_before", "reason",
    "saved_at",
]


def _lesson(timetable: dict, staff: str, day_order: str, period: int) -> Any:
    return timetable.get(staff, {}).get(day_order, {}).get(period)


def _daily_base_load(timetable: dict, staff: str, day_order: str) -> int:
    return sum(value is not None for value in timetable.get(staff, {}).get(day_order, {}).values())


def _teaches_class(timetable: dict, staff: str, class_year: str) -> bool:
    return any(
        lesson and _normalized(lesson.get("year")) == _normalized(class_year)
        for day in timetable.get(staff, {}).values()
        for lesson in day.values()
    )


def _teaches_subject(timetable: dict, staff: str, subject: str) -> bool:
    """Return whether a professor already teaches the same paper."""
    return any(
        lesson and _normalized(lesson.get("subject")) == _normalized(subject)
        for day in timetable.get(staff, {}).values()
        for lesson in day.values()
    )


def _normalized(value: Any) -> str:
    """Normalize harmless spelling/spacing differences in timetable labels."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _normalized_co_taught_year(value: Any) -> str:
    """Normalize class labels only for co-teacher detection.

    In the supplied timetable, undergraduate classes are sometimes recorded as
    ``3rd Year`` and sometimes as ``3rd Year UG``.  They denote the same class.
    ``PG`` is deliberately retained so undergraduate and postgraduate classes
    can never be merged accidentally.  This narrow normalization does not alter
    candidate priority, workload, fairness, or daily-cap calculations.
    """
    normalized = _normalized(value)
    return re.sub(r"\s+ug$", "", normalized).strip()


def _same_class_and_subject(left: dict, right: dict) -> bool:
    return (
        _normalized(left.get("subject")) == _normalized(right.get("subject"))
        and _normalized_co_taught_year(left.get("year"))
        == _normalized_co_taught_year(right.get("year"))
    )


def _history_counts(history: pd.DataFrame, professor: str, selected_date: date) -> tuple[int, int]:
    if history.empty:
        return 0, 0
    dates = pd.to_datetime(history["date"], errors="coerce").dt.date
    assigned = history["assigned_staff"].eq(professor)
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_end = week_start + timedelta(days=6)
    weekly = int((assigned & dates.between(week_start, week_end)).sum())
    monthly = int((assigned & dates.map(lambda d: bool(d) and d.year == selected_date.year and d.month == selected_date.month)).sum())
    return weekly, monthly


def _co_teachers_present(
    timetable: dict, absent_staff: set[str], day_order: str, period: int, lesson: dict
) -> list[str]:
    """Return every active co-teacher in the exact class slot.

    This correctly handles each hour of a multi-period shared class. A teacher
    scheduled in the same subject/class but a different period is not counted.
    """
    present = []
    for staff in timetable:
        if staff in absent_staff:
            continue
        other = _lesson(timetable, staff, day_order, period)
        if other and _same_class_and_subject(other, lesson):
            present.append(staff)
    return present


def generate_substitution_plan(
    timetable: dict,
    selected_date: date,
    day_order: str,
    absent_staff: Iterable[str],
    history: pd.DataFrame | None = None,
    hod: str | None = None,
    restricted_staff: Iterable[str] = (),
    max_daily_periods: int = 3,
    fourth_period_staff: Iterable[str] = (),
) -> pd.DataFrame:
    """Generate a deterministic plan that applies every requested allocation rule."""
    history = history if history is not None else pd.DataFrame(columns=HISTORY_COLUMNS)
    absent = set(absent_staff)
    excluded = absent | set(restricted_staff)
    if hod:
        excluded.add(hod)
    assigned_today: Counter[str] = Counter()
    fourth_period_eligible = set(fourth_period_staff)
    rows: list[dict[str, Any]] = []

    for missing in sorted(absent):
        for period, lesson in sorted(timetable.get(missing, {}).get(day_order, {}).items()):
            if not lesson:
                continue
            co_teachers = _co_teachers_present(timetable, absent, day_order, period, lesson)
            if co_teachers:
                teacher_names = ", ".join(co_teachers)
                rows.append({
                    "date": selected_date.isoformat(), "day_order": day_order, "period": period,
                    "class_year": lesson.get("year", ""), "subject": lesson.get("subject", ""),
                    "absent_staff": missing, "assigned_staff": "", "status": "Skipped – co-teacher present",
                    "same_class_priority": False, "daily_load_before": "", "daily_load_after": "",
                    "weekly_extra_before": "", "monthly_extra_before": "",
                    "reason": f"No substitute required; {teacher_names} is already scheduled for this shared class and period.",
                })
                continue

            free_candidates = []
            for candidate in timetable:
                if candidate in excluded or _lesson(timetable, candidate, day_order, period) is not None:
                    continue
                daily_load = _daily_base_load(timetable, candidate, day_order) + assigned_today[candidate]
                weekly, monthly = _history_counts(history, candidate, selected_date)
                same_class = _teaches_class(timetable, candidate, lesson.get("year", ""))
                same_subject = _teaches_subject(timetable, candidate, lesson.get("subject", ""))
                free_candidates.append((candidate, same_subject, same_class, daily_load, weekly, monthly))

            standard_candidates = [item for item in free_candidates if item[3] < max_daily_periods]
            used_fourth_period_fallback = False
            candidates = standard_candidates
            if not candidates:
                # This exception is intentionally narrow: the professor must be
                # free, explicitly listed as junior lab staff, and currently on
                # exactly the normal cap. Nobody may exceed four total periods.
                candidates = [
                    item for item in free_candidates
                    if item[0] in fourth_period_eligible
                    and item[3] >= max_daily_periods
                    and item[3] < max_daily_periods + 1
                ]
                used_fourth_period_fallback = bool(candidates)

            if not candidates:
                rows.append({
                    "date": selected_date.isoformat(), "day_order": day_order, "period": period,
                    "class_year": lesson.get("year", ""), "subject": lesson.get("subject", ""),
                    "absent_staff": missing, "assigned_staff": "", "status": "Unassigned",
                    "same_class_priority": False, "daily_load_before": "", "daily_load_after": "",
                    "weekly_extra_before": "", "monthly_extra_before": "",
                    "reason": (
                        f"No eligible free professor can remain within the {max_daily_periods}-period daily cap, "
                        "and no eligible junior lab professor is available for the fourth-period exception."
                    ),
                })
                continue

            chosen, same_subject, same_class, daily_load, weekly, monthly = min(
                candidates,
                key=lambda item: (
                    not item[1],  # Exact same paper first (for example Python (AO)).
                    not item[2],  # Then preserve the existing same-class priority.
                    item[3], item[4], item[5], item[0].casefold(),
                ),
            )
            assigned_today[chosen] += 1
            rows.append({
                "date": selected_date.isoformat(), "day_order": day_order, "period": period,
                "class_year": lesson.get("year", ""), "subject": lesson.get("subject", ""),
                "absent_staff": missing, "assigned_staff": chosen,
                "status": "Proposed - fourth-period junior fallback" if used_fourth_period_fallback else "Proposed",
                "same_class_priority": same_class, "daily_load_before": daily_load,
                "daily_load_after": daily_load + 1, "weekly_extra_before": weekly,
                "monthly_extra_before": monthly,
                "reason": (
                    ("Fourth-period junior lab fallback; " if used_fourth_period_fallback else "")
                    + (
                        "same-paper professor; " if same_subject
                        else "same-class professor; " if same_class
                        else "free professor; "
                    )
                    + f"daily load {daily_load}→{daily_load + 1}, weekly extras {weekly}, monthly extras {monthly}."
                ),
            })
    return pd.DataFrame(rows)


class HistoryStore:
    """Small CSV-backed history store suitable for local and Streamlit use."""

    def __init__(self, path: str | Path = "allocation_history.csv") -> None:
        self.path = Path(path)

    def load(self) -> pd.DataFrame:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        frame = pd.read_csv(self.path, dtype=str).fillna("")
        for column in HISTORY_COLUMNS:
            if column not in frame:
                frame[column] = ""
        return frame[HISTORY_COLUMNS]

    def save_confirmed(self, plan: pd.DataFrame) -> int:
        if plan.empty:
            return 0
        confirmed = plan[(plan["status"].str.startswith("Proposed")) & plan["assigned_staff"].ne("")].copy()
        if confirmed.empty:
            return 0
        existing = self.load()
        existing_ids = set(existing["allocation_id"])
        saved_at = datetime.now().isoformat(timespec="seconds")
        records = []
        for row in confirmed.to_dict("records"):
            natural_key = "|".join(str(row.get(key, "")) for key in
                                   ("date", "day_order", "period", "absent_staff", "subject", "assigned_staff"))
            allocation_id = hashlib.sha256(natural_key.encode()).hexdigest()[:16]
            if allocation_id in existing_ids:
                continue
            record = {column: row.get(column, "") for column in HISTORY_COLUMNS}
            record.update({"allocation_id": allocation_id, "saved_at": saved_at})
            records.append(record)
            existing_ids.add(allocation_id)
        if not records:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerows(records)
        return len(records)


def monthly_report(history: pd.DataFrame, staff: Iterable[str], year: int, month: int) -> pd.DataFrame:
    report_staff = list(staff)
    if history.empty:
        counts = Counter()
    else:
        dates = pd.to_datetime(history["date"], errors="coerce")
        mask = dates.dt.year.eq(year) & dates.dt.month.eq(month)
        counts = Counter(history.loc[mask, "assigned_staff"])
    result = pd.DataFrame({"Professor": report_staff, "Extra classes": [counts[name] for name in report_staff]})
    return result.sort_values(["Extra classes", "Professor"], kind="stable").reset_index(drop=True)


def dataframe_csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8-sig")


def dataframe_excel_bytes(frame: pd.DataFrame, sheet_name: str = "Report") -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name=sheet_name[:31])
        sheet = writer.book[sheet_name[:31]]
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            width = min(max(len(str(cell.value or "")) for cell in column) + 2, 60)
            sheet.column_dimensions[column[0].column_letter].width = width
    return buffer.getvalue()
