#!/usr/bin/env python3
"""
TripIt → Todoist local outreach.
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

TODOIST_TASKS_URL = "https://api.todoist.com/api/v1/tasks"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "tripit-local-outreach/1.0 (personal use)"

DEFAULT_RADIUS_KM = 50
DEFAULT_CONTACTS_DIR = "~/travel-contacts"
STATE_FILE = "state.json"
GEO_CACHE_FILE = "geo_cache.json"


@dataclass
class Trip:
    trip_id: str
    city: str
    start_date: Optional[str]
    end_date: Optional[str]


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def normalize_city(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in name)
    return " ".join(cleaned.split())


def extract_city_state(raw: str) -> str:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2:
        return f"{parts[0]}, {parts[1]}"
    return parts[0] if parts else raw


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import radians, sin, cos, sqrt, atan2

    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return r * c


def geocode_city(city: str, cache: Dict[str, Dict[str, float]]) -> Optional[Tuple[float, float]]:
    key = normalize_city(city)
    if key in cache:
        return (cache[key]["lat"], cache[key]["lon"])

    params = {"q": city, "format": "json", "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    cache[key] = {"lat": lat, "lon": lon}
    return (lat, lon)


def load_contacts_map(contacts_dir: str) -> Dict[str, str]:
    contacts_dir = os.path.expanduser(contacts_dir)
    if not os.path.isdir(contacts_dir):
        return {}
    mapping = {}
    for entry in os.listdir(contacts_dir):
        if not entry.lower().endswith(".txt"):
            continue
        city_name = os.path.splitext(entry)[0]
        mapping[normalize_city(city_name)] = os.path.join(contacts_dir, entry)
    return mapping


def parse_event_city(event) -> Optional[str]:
    location = event.get("location")
    if location:
        value = str(location)
        if value.strip():
            return value.strip()

    summary = event.get("summary")
    if summary:
        value = str(summary)
        if value.strip():
            return value.strip()

    return None


def coerce_dt(value, tz: ZoneInfo) -> Optional[datetime]:
    if value is None:
        return None
    dt = value.dt if hasattr(value, "dt") else value
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    if hasattr(dt, "year") and hasattr(dt, "month") and hasattr(dt, "day"):
        return datetime(dt.year, dt.month, dt.day, tzinfo=tz)
    return None


def parse_event_dates(event, tz: ZoneInfo) -> Tuple[Optional[str], Optional[str]]:
    start = coerce_dt(event.get("dtstart"), tz)
    end = coerce_dt(event.get("dtend"), tz)

    start_date = start.date().isoformat() if start else None
    end_date = end.date().isoformat() if end else None

    return start_date, end_date


def pick_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def event_in_range(event, start_window: datetime, end_window: datetime, tz: ZoneInfo) -> bool:
    begin_dt = coerce_dt(event.get("dtstart"), tz)
    if not begin_dt:
        return False
    end_dt = coerce_dt(event.get("dtend"), tz) or begin_dt
    return begin_dt.date() <= end_window.date() and end_dt.date() >= start_window.date()


def fetch_trips(ics_url: str, timezone: str) -> List[Trip]:
    tz = pick_timezone(timezone)
    now = datetime.now(tz)
    start_window = now
    end_window = now + timedelta(days=90)

    resp = requests.get(ics_url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()

    cal = Calendar.from_ical(resp.text)

    grouped: Dict[str, Trip] = {}
    for event in cal.walk("VEVENT"):
        if not event_in_range(event, start_window, end_window, tz):
            continue
        city = parse_event_city(event)
        if not city:
            continue
        start, end = parse_event_dates(event, tz)
        if not start:
            continue
        key = normalize_city(city)
        existing = grouped.get(key)
        if not existing:
            grouped[key] = Trip(trip_id=key, city=city, start_date=start, end_date=end)
            continue
        if existing.start_date is None or (start and start < existing.start_date):
            existing.start_date = start
        if existing.end_date is None or (end and end > existing.end_date):
            existing.end_date = end

    return list(grouped.values())


def parse_contact_line(line: str) -> Tuple[str, Optional[str]]:
    parts = [p.strip() for p in line.split("—", 1)]
    name = parts[0]
    notes = parts[1] if len(parts) > 1 and parts[1] else None
    return name, notes


def create_todoist_task(api_token: str, title: str, description: str) -> None:
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload = {"content": title, "description": description}
    resp = requests.post(TODOIST_TASKS_URL, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()


def match_city(
    trip_city: str,
    contacts_map: Dict[str, str],
    geo_cache: Dict[str, Dict[str, float]],
    radius_km: float,
) -> Tuple[Optional[str], Optional[str], Optional[float]]:
    normalized_trip = normalize_city(trip_city)
    if normalized_trip in contacts_map:
        return contacts_map[normalized_trip], "exact", None

    best_exact = None
    for key, path in contacts_map.items():
        if key and key in normalized_trip:
            if best_exact is None or len(key) > len(best_exact[0]):
                best_exact = (key, path)
    if best_exact:
        return best_exact[1], "exact", None

    trip_coords = geocode_city(trip_city, geo_cache)
    if not trip_coords:
        return None, None, None

    best_match = None
    best_distance = None
    for city_norm, path in contacts_map.items():
        city_name = os.path.splitext(os.path.basename(path))[0]
        coords = geocode_city(city_name, geo_cache)
        if not coords:
            continue
        distance = haversine_km(trip_coords[0], trip_coords[1], coords[0], coords[1])
        if distance <= radius_km and (best_distance is None or distance < best_distance):
            best_match = path
            best_distance = distance

    if best_match:
        return best_match, "radius", best_distance
    return None, None, None


def build_description(
    trip: Trip,
    notes: Optional[str],
    match_type: str,
    distance_km: Optional[float],
    timezone: str,
) -> str:
    parts = [f"Trip to {trip.city}"]
    if trip.start_date or trip.end_date:
        parts.append(
            f"Dates ({timezone}): {trip.start_date or 'unknown'} → {trip.end_date or 'unknown'}"
        )
    if match_type == "exact":
        parts.append("Match: exact city name")
    elif match_type == "radius" and distance_km is not None:
        parts.append(f"Match: within {distance_km:.1f} km")
    if notes:
        parts.append(f"Notes: {notes}")
    return "\n".join(parts)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: tripit_local_outreach.py /path/to/config.json [--ignore-state]")
        return 1

    config_path = sys.argv[1]
    ignore_state = "--ignore-state" in sys.argv[2:]
    config = load_json(config_path, None)
    if not config:
        print("Failed to load config.")
        return 1

    ics_url = config.get("tripit_ics_url")
    todoist_token = config.get("todoist", {}).get("api_token")
    if not ics_url or not todoist_token:
        print("Missing TripIt ICS URL or Todoist API token.")
        return 1

    contacts_dir = os.path.expandvars(config.get("contacts_dir", DEFAULT_CONTACTS_DIR))
    radius_km = float(config.get("radius_km", DEFAULT_RADIUS_KM))
    timezone = config.get("timezone", "UTC")

    state = load_json(STATE_FILE, {"processed_trip_ids": []})
    processed = set() if ignore_state else set(state.get("processed_trip_ids", []))

    geo_cache = load_json(GEO_CACHE_FILE, {})

    contacts_map = load_contacts_map(contacts_dir)
    if not contacts_map:
        print(f"No contacts found in {os.path.expanduser(contacts_dir)}")
        return 1

    trips = fetch_trips(ics_url, timezone)
    if not trips:
        print("No upcoming trips found.")
        return 0

    new_trips = [t for t in trips if t.trip_id not in processed]
    if not new_trips:
        print("No new trips to process.")
        return 0

    created_count = 0
    for trip in new_trips:
        match_path, match_type, distance = match_city(trip.city, contacts_map, geo_cache, radius_km)
        if not match_path:
            continue

        with open(match_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        for line in lines:
            name, notes = parse_contact_line(line)
            if not name:
                continue
            title = f"Reach out to {name} re: {trip.city} trip"
            description = build_description(trip, notes, match_type, distance, timezone)
            create_todoist_task(todoist_token, title, description)
            created_count += 1

        processed.add(trip.trip_id)

    state["processed_trip_ids"] = sorted(processed)
    save_json(STATE_FILE, state)
    save_json(GEO_CACHE_FILE, geo_cache)

    print(f"Created {created_count} Todoist task(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
