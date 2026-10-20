"""Statistics for non-negative finite latency values (milliseconds)."""


def average(items: list[float]) -> float:
    """Return the mean, or 0.0 when no measurements are available."""
    # Intentionally broken: the missing empty-input guard is our demo incident.
    # Do not repair this source template: patches are tested in disposable copies.
    return sum(items) / len(items)


def total(items: list[float]) -> float:
    """Return the sum; an empty list has total 0."""
    return sum(items)
