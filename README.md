# Class Substitution Planner

Run locally with `streamlit run main.py`.

The app prioritizes a free professor who teaches the same class/year, then the
lowest same-day total load, weekly extra-class count, and monthly extra-class
count. It never takes a professor above three total periods in the selected day.
If another active professor is already scheduled for the same class, subject,
day order, and period, the shared class is marked as requiring no substitute.

If an allocation would otherwise be unassigned only because all free standard
candidates have reached three periods, one explicitly configured junior lab
professor may receive a fourth period. This exception never permits a fifth
period and still excludes absent, HOD, restricted, or busy professors.

Confirmed allocations are appended to `allocation_history.csv`. Daily and
monthly reports can be downloaded as CSV or Excel files.

Co-taught detection accepts the timetable's equivalent undergraduate labels
such as `3rd Year` and `3rd Year UG`. Postgraduate (`PG`) classes remain
distinct. All workload, priority, fairness, and daily-cap rules are unchanged.

When an eligible free professor already teaches the exact same paper, that
professor is preferred before the existing same-class and workload tie-breaks.

The saved allocation history can be viewed directly without a login.

Saving confirmed allocations is protected by the same login. Clicking the save
button opens an authorization form, and no history row is written unless the
credentials are correct.

Same-paper priority requires both the same subject and the same class. For a
core paper, faculty teaching a major paper to that class are preferred over
faculty who only handle service papers marked `(AO)`, `(AR)`, or `(CD)`.

On Streamlit Community Cloud, repository files are not durable storage across
all restarts. Download reports regularly or connect the history store to a
persistent database for production use.
