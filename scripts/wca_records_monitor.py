#!/usr/bin/env python3
"""Watch WCA Live for new world records and Bangladesh national records.

Polls the WCA Live GraphQL API, compares against a committed state file so the
same record is never announced twice, and posts anything new to a Discord
webhook.

Standard library only, so CI needs no pip install step.

Environment:
  DISCORD_WEBHOOK_URL  required to post; unset means dry run (prints instead)
  DISCORD_ROLE_ID      optional role to ping when a record lands
  WATCH_COUNTRY        ISO2 code for the national records to watch (default BD)
  REPRIME              "true" resets state and announces nothing
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://live.worldcubeassociation.org/api"
STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "seen.json"
WATCH_COUNTRY = os.environ.get("WATCH_COUNTRY", "BD").upper()
RETENTION_DAYS = 30
# Discord rejects API requests without a User-Agent with a bare 403.
USER_AGENT = "wca-record-watcher (+https://github.com/CubeNation/wca-record-watcher)"

# recentRecords accepts no arguments, so every filter below is applied locally.
QUERY = """
query RecentRecords {
  recentRecords {
    id
    type
    tag
    attemptResult
    result {
      enteredAt
      person {
        name
        wcaId
        country { iso2 name }
      }
      round {
        id
        name
        competitionEvent {
          event { id name }
          competition { id name }
        }
      }
    }
  }
}
"""


# --- result formatting -----------------------------------------------------
# attemptResult is an integer whose meaning depends on the event.

def format_clock(centiseconds):
    """Centiseconds to 50.03, 2:23.34 or 1:05:22.19."""
    total = int(centiseconds)
    cs = total % 100
    seconds = total // 100
    s, m, h = seconds % 60, (seconds // 60) % 60, seconds // 3600
    if h:
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
    if m:
        return f"{m}:{s:02d}.{cs:02d}"
    return f"{s}.{cs:02d}"


def format_multibld(value):
    """Packed DDTTTTTMM to '63/65 in 58:23'."""
    raw = str(int(value)).zfill(9)
    difference = 99 - int(raw[0:2])
    seconds = int(raw[2:7])
    missed = int(raw[7:9])
    solved = difference + missed
    attempted = solved + missed
    clock = "unknown time" if seconds == 99999 else f"{seconds // 60}:{seconds % 60:02d}"
    return f"{solved}/{attempted} in {clock}"


def format_result(value, event_id, kind):
    value = int(value)
    if value == -1:
        return "DNF"
    if value == -2:
        return "DNS"
    if value <= 0:
        return "-"
    if event_id in ("333mbf", "333mbo"):
        return format_multibld(value)
    if event_id == "333fm":
        # Singles are a raw move count; averages are moves x 100.
        return f"{value / 100:.2f} moves" if kind == "average" else f"{value} moves"
    return format_clock(value)


# --- state -----------------------------------------------------------------

def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        print(f"State file unreadable ({exc}); treating this as a first run.", file=sys.stderr)
        return None


def save_state(seen):
    """Drop entries older than the API window so the file stays small."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"seen": pruned}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return pruned


def record_key(record):
    """A corrected result keeps its id but changes attemptResult, so key on both."""
    return f"{record['id']}|{record['attemptResult']}"


# --- api -------------------------------------------------------------------

def fetch_records(attempts=3):
    body = json.dumps({"query": QUERY}).encode("utf-8")
    last_error = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("errors"):
                raise RuntimeError(f"GraphQL errors: {payload['errors']}")
            return payload["data"]["recentRecords"]
        except (urllib.error.URLError, OSError, ValueError, KeyError, RuntimeError) as exc:
            last_error = exc
            print(f"Fetch attempt {attempt}/{attempts} failed: {exc}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise SystemExit(f"Could not reach WCA Live: {last_error}")


def is_interesting(record):
    tag = record["tag"]
    if tag == "WR":
        return True
    # Country is the competitor's, not the competition's: Bangladesh records
    # are regularly set abroad.
    return tag == "NR" and record["result"]["person"]["country"]["iso2"] == WATCH_COUNTRY


# --- discord ---------------------------------------------------------------

def build_embed(record):
    result = record["result"]
    person = result["person"]
    comp_event = result["round"]["competitionEvent"]
    event = comp_event["event"]
    competition = comp_event["competition"]
    is_wr = record["tag"] == "WR"

    if is_wr:
        heading = "World Record"
        flag = "\U0001F30D"
    else:
        heading = f"{person['country']['name']} National Record"
        flag = "\U0001F1E7\U0001F1E9" if person["country"]["iso2"] == "BD" else "\U0001F3C6"

    kind = record["type"]
    value = format_result(record["attemptResult"], event["id"], kind)

    who = person["name"]
    if person.get("wcaId"):
        who = f"[{person['name']}](https://www.worldcubeassociation.org/persons/{person['wcaId']})"

    # The URL must be unique per embed. Discord's client silently collapses
    # embeds that share an identical url, so linking every record to its
    # competition page made five of six records vanish from the channel. The
    # round link is also a better destination, and the fragment keeps a
    # single and an average from the same round distinct.
    round_id = result["round"]["id"]
    url = (
        f"https://live.worldcubeassociation.org/competitions/{competition['id']}"
        f"/rounds/{round_id}#{record['id']}"
    )

    return {
        "title": f"{flag} New {heading}",
        "url": url,
        "description": f"**{who}** ({person['country']['name']}) set a new {record['tag']} in {event['name']}.",
        "color": 0xF1C40F if is_wr else 0x006A4E,
        "fields": [
            {"name": "Result", "value": f"**{value}**", "inline": True},
            {"name": "Event", "value": f"{event['name']} ({kind})", "inline": True},
            {
                "name": "Competition",
                "value": f"{competition['name']} - {result['round']['name']}",
                "inline": False,
            },
        ],
        "timestamp": result["enteredAt"],
        "footer": {"text": "WCA Live - provisional, pending WCA ratification"},
    }


def post_to_discord(webhook_url, embeds, role_id):
    """One message per record.

    Batching several embeds into one message is tempting, but it gives a single
    notification for a whole batch, and Discord's client collapses embeds that
    share a url. One message per record avoids both, at the cost of a ping per
    record - acceptable for something this rare.
    """
    for index, embed in enumerate(embeds):
        payload = {"embeds": [embed]}
        if role_id:
            payload["content"] = f"<@&{role_id}>"
            payload["allowed_mentions"] = {"roles": [role_id]}

        body = json.dumps(payload).encode("utf-8")
        for attempt in range(1, 4):
            request = urllib.request.Request(
                webhook_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    print(
                        f"Posted {index + 1}/{len(embeds)} to Discord "
                        f"(HTTP {response.status}): {embed['fields'][1]['value']}"
                    )
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    wait = float(exc.headers.get("Retry-After", 5) or 5)
                    print(f"Rate limited; retrying in {wait}s.", file=sys.stderr)
                    time.sleep(wait)
                    continue
                detail = exc.read().decode("utf-8", "replace")
                raise SystemExit(f"Discord rejected the post: {exc.code} {detail}")
            except (urllib.error.URLError, OSError) as exc:
                if attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                raise SystemExit(f"Could not reach Discord: {exc}")
        time.sleep(1)


# --- main ------------------------------------------------------------------

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    role_id = os.environ.get("DISCORD_ROLE_ID", "").strip()
    reprime = os.environ.get("REPRIME", "").strip().lower() == "true"

    records = fetch_records()
    print(f"Fetched {len(records)} recent records from WCA Live.")

    state = load_state()
    first_run = state is None or reprime
    seen = {} if first_run else dict(state.get("seen", {}))

    # recentRecords is not returned in chronological order.
    records.sort(key=lambda r: r["result"]["enteredAt"])

    new_records = []
    for record in records:
        key = record_key(record)
        already_seen = key in seen
        seen[key] = record["result"]["enteredAt"]
        if already_seen or not is_interesting(record):
            continue
        new_records.append(record)

    save_state(seen)

    if first_run:
        reason = "Re-primed on request" if reprime else "First run"
        print(f"{reason}: marked {len(seen)} records as seen, notifying about none.")
        return

    if not new_records:
        print(f"Nothing new: no world records and no {WATCH_COUNTRY} national records.")
        return

    embeds = [build_embed(record) for record in new_records]
    for record in new_records:
        person = record["result"]["person"]
        event = record["result"]["round"]["competitionEvent"]["event"]
        value = format_result(record["attemptResult"], event["id"], record["type"])
        print(f"NEW {record['tag']} {record['type']} - {event['name']} {value} - {person['name']}")

    if not webhook_url:
        print("\nDRY RUN: DISCORD_WEBHOOK_URL is not set, nothing was posted.")
        print(json.dumps({"embeds": embeds}, indent=2, ensure_ascii=False))
        return

    post_to_discord(webhook_url, embeds, role_id)


if __name__ == "__main__":
    main()
