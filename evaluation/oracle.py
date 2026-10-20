"""Fixed answers recorded before any candidate patch; independent of app.calc."""

# Literal answers prevent an application bug from changing its own acceptance criteria.
AVERAGE_CASES = (
    ((), 0.0),
    ((10, 20, 30), 20.0),
    ((7,), 7.0),
    ((0, 0), 0.0),
)


def expected_average(items: list[float]) -> float:
    """Return a recorded answer, refusing inputs outside this small acceptance set."""
    for values, answer in AVERAGE_CASES:
        if tuple(items) == values:
            return answer
    raise ValueError("No recorded acceptance answer for this input")
