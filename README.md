# Class Substitution Planner

Run locally with `streamlit run main.py`.

The app prioritizes a free professor who teaches the same class/year, then the
lowest same-day total load, weekly extra-class count, and monthly extra-class
count. It never takes a professor above three total periods in the selected day.
If another active professor is already scheduled for the same class, subject,
day order, and period, the shared class is marked as requiring no substitute.

Confirmed allocations are appended to `allocation_history.csv`. Daily and
monthly reports can be downloaded as CSV or Excel files.

On Streamlit Community Cloud, repository files are not durable storage across
all restarts. Download reports regularly or connect the history store to a
persistent database for production use.
