# TripIt → Todoist Local Outreach

Poll TripIt for upcoming trips via the TripIt ICS feed and create Todoist Inbox tasks to reach out to contacts in the destination city.

## Features
- TripIt upcoming trips (next 90 days) via ICS feed
- Match destination to contacts by exact city name or within radius (default 50km)
- Create Todoist Inbox tasks immediately
- Local state tracking to avoid duplicate tasks
- Geocode caching via OpenStreetMap Nominatim

## Setup

1) Create a virtual env (optional) and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Create your config file:

```bash
cp config.example.json config.json
```

Edit `config.json` with your TripIt ICS URL and Todoist API token.

Notes:
- Todoist API key: https://app.todoist.com/app/settings/integrations/developer#
- TripIt ICS URL: https://www.tripit.com/app/settings/calendar-feed

3) Prepare contacts directory (default `~/travel-contacts`):

- File name: `City, ST.txt` preferred (case-insensitive) to avoid ambiguity
- Matching uses the filename string, so include state/province in the filename
- Format: one contact per line
- Line format: `Name — Notes` (notes optional)

Example `New Orleans, LA.txt`:

```
Alex Dupre — Coffee in the Quarter
Jamie LeBlanc — Lunch near CBD
```

## Usage

```bash
python tripit_local_outreach.py config.json
```

To ignore state and reprocess all trips:

```bash
python tripit_local_outreach.py config.json --ignore-state
```

The script will create:
- `state.json` — processed TripIt trip IDs
- `geo_cache.json` — cached geocodes

## How matching works

1) Exact match on city file name (normalized)
2) If no exact match, geocode trip city and contact cities; pick a city file within `radius_km` (default 50km)

## Cron example

Run every morning at 8:00am:

```bash
0 8 * * * cd $HOME/.openclaw/workspace/tripit-local-outreach && /usr/bin/python3 tripit_local_outreach.py config.json >> outreach.log 2>&1
```

## Notes

- Todoist tasks are created in your Inbox by default (no project ID).
- TripIt trips are read from the ICS feed URL configured in `tripit_ics_url`.
- Nominatim requires a polite User-Agent; this script sets one.
