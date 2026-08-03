"""Substitution allocation, history persistence, and report exports."""

from __future__ import annotations

import csv
import hashlib
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
        lesson and lesson.get("year") == class_year
        for day in timetable.get(staff, {}).values()
        for lesson in day.values()
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


def _co_teacher_present(
    timetable: dict, absent_staff: set[str], day_order: str, period: int, lesson: dict
) -> str | None:
    for staff in timetable:
        if staff in absent_staff:
            continue
        other = _lesson(timetable, staff, day_order, period)
        if other and other.get("subject") == lesson.get("subject") and other.get("year") == lesson.get("year"):
            return staff
    return None


def generate_substitution_plan(
    timetable: dict,
    selected_date: date,
    day_order: str,
    absent_staff: Iterable[str],
    history: pd.DataFrame | None = None,
    hod: str | None = None,
    restricted_staff: Iterable[str] = (),
    max_daily_periods: int = 3,
) -> pd.DataFrame:
    """Generate a deterministic plan that applies every requested allocation rule."""
    history = history if history is not None else pd.DataFrame(columns=HISTORY_COLUMNS)
    absent = set(absent_staff)
    excluded = absent | set(restricted_staff)
    if hod:
        excluded.add(hod)
    assigned_today: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []

    for missing in sorted(absent):
        for period, lesson in sorted(timetable.get(missing, {}).get(day_order, {}).items()):
            if not lesson:
                continue
            co_teacher = _co_teacher_present(timetable, absent, day_order, period, lesson)
            if co_teacher:
                rows.append({
                    "date": selected_date.isoformat(), "day_order": day_order, "period": period,
                    "class_year": lesson.get("year", ""), "subject": lesson.get("subject", ""),
                    "absent_staff": missing, "assigned_staff": "", "status": "Skipped – co-teacher present",
                    "same_class_priority": False, "daily_load_before": "", "daily_load_after": "",
                    "weekly_extra_before": "", "monthly_extra_before": "",
                    "reason": f"No substitute required; {co_teacher} is already scheduled for this shared class.",
                })
                continue

            candidates = []
            for candidate in timetable:
                if candidate in excluded or _lesson(timetable, candidate, day_order, period) is not None:
                    continue
                daily_load = _daily_base_load(timetable, candidate, day_order) + assigned_today[candidate]
                if daily_load >= max_daily_periods:
                    continue
                weekly, monthly = _history_counts(history, candidate, selected_date)
                same_class = _teaches_class(timetable, candidate, lesson.get("year", ""))
                # Exact order implements: same class first, then today's load, then
                # weekly and monthly fairness, with name as a stable final tie-break.
                candidates.append(((not same_class, daily_load, weekly, monthly, candidate.casefold()),
                                   candidate, same_class, daily_load, weekly, monthly))

            if not candidates:
                rows.append({
                    "date": selected_date.isoformat(), "day_order": day_order, "period": period,
                    "class_year": lesson.get("year", ""), "subject": lesson.get("subject", ""),
                    "absent_staff": missing, "assigned_staff": "", "status": "Unassigned",
                    "same_class_priority": False, "daily_load_before": "", "daily_load_after": "",
                    "weekly_extra_before": "", "monthly_extra_before": "",
                    "reason": f"No eligible free professor can remain within the {max_daily_periods}-period daily cap.",
                })
                continue

            _, chosen, same_class, daily_load, weekly, monthly = min(candidates, key=lambda item: item[0])
            assigned_today[chosen] += 1
            rows.append({
                "date": selected_date.isoformat(), "day_order": day_order, "period": period,
                "class_year": lesson.get("year", ""), "subject": lesson.get("subject", ""),
                "absent_staff": missing, "assigned_staff": chosen, "status": "Proposed",
                "same_class_priority": same_class, "daily_load_before": daily_load,
                "daily_load_after": daily_load + 1, "weekly_extra_before": weekly,
                "monthly_extra_before": monthly,
                "reason": (
                    ("Same-class professor; " if same_class else "Free professor; ")
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
        confirmed = plan[(plan["status"].eq("Proposed")) & plan["assigned_staff"].ne("")].copy()
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

