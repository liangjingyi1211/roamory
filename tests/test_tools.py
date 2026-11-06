from travelmind.tools import calculate_budget, count_nights, search_hotels

def test_budget():
    result = calculate_budget(500, 600, 100, 50, 1500)
    assert result["estimated_total"] == 1250
    assert result["within_budget"] is True

def test_nights():
    assert count_nights("2027-04-01", "2027-04-08")["nights"] == 7

def test_demo_source():
    assert search_hotels("Tokyo", 2)["live"] is False
