"""Session-relative timestamps, including training resumed on a later day."""

MAX_SAFE_INTEGER = 2**53 - 1
CLOCK_TOLERANCE_MS = 5 * 60_000


def valid_offset(session, value, now):
    # Never truncate/rebase stored timestamps. Reject epoch timestamps, invalid
    # numbers and implausible future offsets while accepting an old session's
    # actual age. Completed sessions may receive delayed outbox uploads too.
    age = max(0, now - session['started'], (session.get('ended') or session['started']) - session['started'])
    return type(value) is int and 0 <= value <= min(MAX_SAFE_INTEGER, age + CLOCK_TOLERANCE_MS)
