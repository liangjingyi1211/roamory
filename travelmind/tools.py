"""Demo catalog tools. No live booking, inventory or price API is used."""
from datetime import date

CATALOG = {
    "Tokyo": {
        "flights": [{"name": "Sample economy flight", "estimated_sgd": 520, "note": "DEMO ESTIMATE; not a live quote"}],
        "hotels": [{"name": "Sample Ueno budget hotel", "estimated_sgd_per_night": 95, "note": "DEMO ESTIMATE"}, {"name": "Sample central hotel", "estimated_sgd_per_night": 150, "note": "DEMO ESTIMATE"}],
        "activities": [{"name": "Ueno Park", "estimated_sgd": 0}, {"name": "Akihabara exploration", "estimated_sgd": 25}, {"name": "Museum visit", "estimated_sgd": 20}],
    },
    "Kyoto": {
        "flights": [{"name": "Sample economy flight to Kansai area", "estimated_sgd": 560, "note": "DEMO ESTIMATE"}],
        "hotels": [{"name": "Sample Kyoto hotel", "estimated_sgd_per_night": 100, "note": "DEMO ESTIMATE"}],
        "activities": [{"name": "Fushimi Inari walk", "estimated_sgd": 0}, {"name": "Arashiyama walk", "estimated_sgd": 0}],
    },
    "Singapore": {
        "flights": [{"name": "Local trip: no flight required", "estimated_sgd": 0, "note": "DEMO"}],
        "hotels": [{"name": "Sample local hotel", "estimated_sgd_per_night": 130, "note": "DEMO ESTIMATE"}],
        "activities": [{"name": "Gardens by the Bay outdoor gardens", "estimated_sgd": 0}, {"name": "Museum visit", "estimated_sgd": 25}],
    },
}


def search_flights(destination: str, origin: str = "Singapore") -> dict:
    """Find illustrative flight options in a small demo catalog; NOT live prices or availability.

    Args:
        destination: Destination city (Tokyo, Kyoto, or Singapore).
        origin: Departure city.
    """
    rows = CATALOG.get(destination.strip().title(), {}).get("flights", [])
    return {"origin": origin, "destination": destination, "options": rows, "data_source": "demo_catalog", "live": False}


def search_hotels(destination: str, nights: int = 3) -> dict:
    """Return example hotel choices and computed estimated total costs; NOT live offers.

    Args:
        destination: Destination city (Tokyo, Kyoto, or Singapore).
        nights: Number of nights (1 through 30).
    """
    if not 1 <= nights <= 30:
        return {"error": "nights must be between 1 and 30"}
    rows = CATALOG.get(destination.strip().title(), {}).get("hotels", [])
    return {"destination": destination, "nights": nights, "options": [{**x, "estimated_total_sgd": x["estimated_sgd_per_night"] * nights} for x in rows], "data_source": "demo_catalog", "live": False}


def search_activities(destination: str) -> dict:
    """Return illustrative activities from a demo catalog; opening hours are not verified.

    Args:
        destination: Destination city (Tokyo, Kyoto, or Singapore).
    """
    return {"destination": destination, "options": CATALOG.get(destination.strip().title(), {}).get("activities", []), "data_source": "demo_catalog", "live": False}


def calculate_budget(flights_sgd: float, hotels_sgd: float, activities_sgd: float, transport_sgd: float, budget_sgd: float) -> dict:
    """Calculate travel costs deterministically and determine whether they fit the budget.

    Args:
        flights_sgd: Estimated total flight cost in Singapore dollars.
        hotels_sgd: Estimated total hotel cost in Singapore dollars.
        activities_sgd: Estimated total activity cost in Singapore dollars.
        transport_sgd: Estimated additional local or intercity transport cost in Singapore dollars.
        budget_sgd: User's maximum total budget in Singapore dollars.
    """
    values = [flights_sgd, hotels_sgd, activities_sgd, transport_sgd, budget_sgd]
    if any(x < 0 for x in values):
        return {"error": "Costs and budget must be non-negative"}
    total = sum(values[:4])
    return {"currency": "SGD", "estimated_total": round(total, 2), "budget": budget_sgd, "remaining": round(budget_sgd - total, 2), "within_budget": total <= budget_sgd}


def count_nights(start_date: str, end_date: str) -> dict:
    """Return the number of nights between two ISO dates (YYYY-MM-DD).

    Args:
        start_date: Arrival date YYYY-MM-DD.
        end_date: Departure date YYYY-MM-DD.
    """
    try:
        n = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
    except ValueError:
        return {"error": "Invalid date: use YYYY-MM-DD"}
    if n < 1 or n > 30:
        return {"error": "Trip length must be 1-30 nights"}
    return {"nights": n}
