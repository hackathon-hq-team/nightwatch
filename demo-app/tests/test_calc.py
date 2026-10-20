from app.calc import average, total


# These are the original tests: the missing empty-average case is added later.
def test_average_multiple_measurements():
    assert average([10, 20, 30]) == 20


def test_average_single_measurement():
    assert average([7]) == 7


def test_average_zero_measurements():
    assert average([0, 0]) == 0


def test_total():
    assert total([10, 20, 30]) == 60
    assert total([]) == 0
