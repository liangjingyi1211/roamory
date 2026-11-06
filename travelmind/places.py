"""Google Places API (New); no mock fallback or inferred attributes."""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from dotenv import load_dotenv
from .audit import audit

load_dotenv()

class PlacesError(RuntimeError):
    pass

class PlacesClient:
    def __init__(self):
        self._cache = {}
        self._destinations = {}

    def _request(self, payload, fields):
        key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not key:
            raise PlacesError("未找到 GOOGLE_MAPS_API_KEY，无法核实真实地点")
        request = Request("https://places.googleapis.com/v1/places:searchText",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": ",".join("places." + f for f in fields.split(","))}, method="POST")
        try:
            with urlopen(request, timeout=25) as response:
                return json.load(response).get("places", [])
        except HTTPError as exc:
            try:
                error = json.loads(exc.read()).get("error", {})
                detail = f'{error.get("status")}: {error.get("message", "请求失败")}'.replace(key, "[REDACTED]")
            except (ValueError, AttributeError):
                detail = "请求失败"
            raise PlacesError(f"Google Places HTTP {exc.code}: {detail}。请检查 Places API (New)、结算和密钥限制。") from None
        except (URLError, TimeoutError) as exc:
            raise PlacesError(f"Google Places 网络连接失败：{type(exc).__name__}") from None

    def resolve_destination(self, destination):
        cached = self._destinations.get(destination)
        if cached and time.monotonic() - cached[0] < 900:
            return cached[1]
        rows = self._request({"textQuery": destination, "pageSize": 5, "languageCode": "zh-CN"},
            "id,displayName,formattedAddress,location,viewport,types,addressComponents")
        geographic = {"country", "locality", "administrative_area_level_1", "administrative_area_level_2", "postal_town", "sublocality", "sublocality_level_1"}
        region = next((r for r in rows if geographic.intersection(r.get("types", []))
            and r.get("viewport") and country_code(r)), None)
        if region is None:
            raise PlacesError("无法确定目的地的地理边界及国家，请输入明确的城市及国家；不会使用无地域限制的搜索。")
        self._destinations[destination] = (time.monotonic(), region)
        audit("destination_resolved", destination=destination, name=region.get("displayName"),
            country=country_code(region), viewport=region["viewport"])
        return region

    def search(self, destination: str, kind: str, preferences: str = "") -> list[dict]:
        expected = {"restaurant": {"restaurant"}, "hotel": {"hotel", "lodging"},
            "tourist_attraction": {"tourist_attraction", "museum", "park", "historical_landmark"}}[kind]
        # Preferences (including negations like 'not spicy') belong to the food
        # specialist, not a geographic text query that can match unrelated places.
        query = f"{kind} in {destination}"
        cached = self._cache.get(query)
        if cached and time.monotonic() - cached[0] < 900:
            return cached[1]
        region = self.resolve_destination(destination)
        fields = "id,displayName,formattedAddress,location,googleMapsUri,primaryType,types,rating,priceLevel,regularOpeningHours,businessStatus,attributions,addressComponents"
        payload = {"textQuery": query, "pageSize": 20, "languageCode": "zh-CN",
            "locationRestriction": {"rectangle": region["viewport"]}}
        rows = self._request(payload, fields)
        rejected = [r for r in rows if not within_destination(r, region)]
        audit("places_search", destination=destination, kind=kind, query=query,
            country=country_code(region), received=len(rows), rejected=[
                {"id": r.get("id"), "address": r.get("formattedAddress"), "location": r.get("location")}
                for r in rejected])
        rows = [r for r in rows if r.get("id") and r.get("displayName", {}).get("text")
            and expected.intersection(r.get("types", []))
            and r.get("businessStatus") not in {"CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"}
            and within_destination(r, region)]
        audit("places_candidates", destination=destination, kind=kind, candidates=[
            {"id": r["id"], "name": r["displayName"]["text"], "address": r.get("formattedAddress")} for r in rows])
        self._cache[query] = (time.monotonic(), rows)
        return rows

client = PlacesClient()

def country_code(row):
    return next((c.get("shortText") for c in row.get("addressComponents", []) if "country" in c.get("types", [])), None)

def within_destination(row, region):
    if not country_code(row) or country_code(row) != country_code(region):
        return False
    location = row.get("location", {})
    latitude, longitude = location.get("latitude"), location.get("longitude")
    low, high = region["viewport"]["low"], region["viewport"]["high"]
    if latitude is None or longitude is None or not low["latitude"] <= latitude <= high["latitude"]:
        return False
    if low["longitude"] <= high["longitude"]:
        return low["longitude"] <= longitude <= high["longitude"]
    return longitude >= low["longitude"] or longitude <= high["longitude"]

def opening_hours(row: dict) -> list[dict]:
    result = []
    for period in row.get("regularOpeningHours", {}).get("periods", []):
        start, end = period.get("open"), period.get("close")
        if not start or not end or "day" not in start:
            continue
        result.append({"day_of_week": (start["day"] + 6) % 7 + 1,
            "open_time": f'{start.get("hour", 0):02}:{start.get("minute", 0):02}',
            "close_time": f'{end.get("hour", 0):02}:{end.get("minute", 0):02}'})
    return result

def place_fields(row: dict) -> dict:
    return dict(name=row["displayName"]["text"], place_id=row["id"],
        address=row.get("formattedAddress"), latitude=row.get("location", {}).get("latitude"),
        longitude=row.get("location", {}).get("longitude"), source_url=row.get("googleMapsUri"))
