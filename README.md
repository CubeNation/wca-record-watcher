# WCA record watcher

Posts a Discord message whenever a **world record** or a **Bangladesh national
record** is set, using live scoretaking data from
[WCA Live](https://live.worldcubeassociation.org).

Runs on GitHub Actions every 15 minutes. No server, no dependencies.

## How it gets the data

It polls the WCA Live GraphQL API at `https://live.worldcubeassociation.org/api`
(public, no auth) for `recentRecords`.

This is **not** the same as the records page on worldcubeassociation.org. That
page shows ratified results, which appear days or weeks after a competition.
WCA Live is the scoretaking system delegates type into at the venue, so records
show up within minutes.

The trade-off: **these records are provisional.** WCA Live tags a result by
comparing it against a record baseline it refreshes from the WCA API hourly. A
live "NR" can still be withdrawn later — a +2 applied on review, a DNF, a typo
corrected. Every Discord message is footed with a note saying so.

## Setup

1. Create a Discord webhook: Server Settings → Integrations → Webhooks → New
   Webhook, pick the channel, copy the URL.
2. Add it as a repository secret named `DISCORD_WEBHOOK_URL`:

   ```bash
   gh secret set DISCORD_WEBHOOK_URL --repo CubeNation/wca-record-watcher
   ```

That is the whole setup. No state file is committed here, so the first run
primes itself: it marks every record currently in the API window as already
seen and announces nothing. Real notifications start from the second run.

If you ever need to reset — after a long outage, say — run the workflow
manually with **reprime** checked to re-baseline without a flood of messages.

### Optional settings

| Name | Kind | Default | Purpose |
|---|---|---|---|
| `DISCORD_WEBHOOK_URL` | secret | — | Required to post. Unset means dry run. |
| `DISCORD_ROLE_ID` | variable | — | Role to ping, e.g. `1234567890`. |
| `WATCH_COUNTRY` | variable | `BD` | ISO2 code for the national records to watch. |

Set a variable with `gh variable set WATCH_COUNTRY --body BD`.

## How duplicate suppression works

GitHub Actions gives a scheduled job no memory between runs, so the script
commits `state/seen.json` back to this repo. Each entry is keyed on the record
id *and* its result value, so a corrected result is treated as a new event
rather than silently swallowed. Entries older than 30 days are pruned.

A side benefit: those commits count as repository activity, which helps keep
the schedule alive. GitHub disables cron workflows in repositories with 60 days
of no activity.

## Things worth knowing

**Country means the competitor's country, not the competition's.** Bangladeshi
cubers set national records abroad all the time — the six BD records in the feed
when this was built were all set in Sydney. Filtering by competition location
would have missed every one.

**World records are rare.** A typical week holds 70-ish national records and
zero world records. Long silences are normal.

**The feed is not in chronological order.** The script sorts by `enteredAt`
before processing.

**Result values are integers, and the encoding depends on the event.**
Centiseconds for timed events, a raw move count for Fewest Moves singles, moves
× 100 for FM averages, and a packed `DDTTTTTMM` format for Multi-Blind.
`scripts/wca_records_monitor.py` handles all four.

## Running locally

```bash
python scripts/wca_records_monitor.py
```

With `DISCORD_WEBHOOK_URL` unset it performs a dry run: it prints the records it
would announce and the exact Discord payload, and posts nothing.

## Schedule cost

This repository is public, so Actions minutes are free and the 15-minute cron
costs nothing. If you fork it private, note that GitHub bills each run as a full
minute: every 15 minutes is about 2,880 minutes a month, above the 2,000-minute
free allowance. Every 30 minutes (1,440) or hourly (720) fits.

GitHub's scheduler is also best-effort — runs can be delayed during peak load or
skipped entirely. Since the API keeps roughly a week of records and the state
file persists, a missed run is caught up by the next one rather than lost.
