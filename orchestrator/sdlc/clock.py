"""The orchestrator's one source of "now": naive UTC, to the second.

Every time the orchestrator stores is naive UTC, and times parsed from GitHub already are, so
comparisons between them hold whatever the server's local time zone is.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """The current UTC time, naive, with no microseconds."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)
