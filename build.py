"""Scrape ACTC calendars into subscribable .ics files under docs/."""
import html, json, re, sys, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

CATS = {"tc": "TC", "tcp": "TC Pista", "tcm": "TC Mouras", "tcpm": "TC Pista Mouras",
        "tcpk": "TC Pick Up", "tcppk": "TC Pista Pick Up"}
MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
          "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12}
# ponytail: regex scrape of rendered text, breaks if ACTC changes markup; upgrade to their Next.js data payload then
ROW = re.compile(r"Fecha (\d+) — ([^|]+)\|\w+, (\d+) (\w+) (\d{4})\|([^|]+)\| — ([^|]+)")
OUT = Path(__file__).parent / "docs"
SESSIONS = OUT / "sessions.json"  # ACTC's API only keeps the current weekend, so we remember past ones here
# ponytail: keyword match on titles; anything else ("Salida a pista", TV/grid notes) is dropped
KINDS = {"Entrenamientos": r"entrenamiento", "Clasificación": r"clasificaci", "Series": r"\bserie\b",
         "Final": r"\bfinal\b"}


def kind_of(title):
    return next((k for k, rx in KINDS.items() if re.search(rx, title, re.I)), None)


def parse(page):
    text = re.sub(r"<script.*?</script>", "", page, flags=re.S)
    text = re.sub(r"\|+", "|", html.unescape(re.sub(r"<[^>]+>", "|", text)))
    events = {}  # keyed by fecha number: the page repeats the "próxima fecha" card
    for n, name, d, mon, y, track, city in ROW.findall(text):
        events[int(n)] = dict(n=int(n), name=name.strip().title(), date=date(int(y), MONTHS[mon.lower()], int(d)),
                              track=track.strip(), city=city.strip())
    return [events[k] for k in sorted(events)]


def esc(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """RFC 5545: lines max 75 octets, continuation lines start with a space."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > 75:
            out.append(cur.decode())
            cur = b" "
        cur += b
    return "\r\n".join(out + [cur.decode()])


def race_event(slug, e):
    return [f"UID:{slug}-{e['date'].year}-{e['n']}@actc-cal",
            f"DTSTART;VALUE=DATE:{e['date']:%Y%m%d}",
            f"DTEND;VALUE=DATE:{e['date'] + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{esc(CATS[slug] + ' · Fecha ' + str(e['n']) + ' — ' + e['name'])}",
            f"LOCATION:{esc('Autódromo ' + e['track'] + ', ' + e['city'])}",
            f"URL:https://www.actc.org.ar/{slug}/calendario"]


def blocks(slug, mine, races):
    """Merge a category's sessions into one block per (day, kind): 4 quali groups -> one 'Clasificación'."""
    merged = {}
    for x in mine:
        day, kind = day_of(x["start"]), kind_of(x["title"])
        if kind:
            b = merged.setdefault((day, kind), dict(x, start=x["start"], end=x["end"]))
            b["start"], b["end"] = min(b["start"], x["start"]), max(b["end"], x["end"])
    out = []
    for (day, kind), b in sorted(merged.items()):
        race = next((e for e in races if (day - e["date"]).days in range(-3, 1)), None)
        title = f"{CATS[slug]} · {kind}" + (f" — {race['name']}" if kind == "Final" and race else "")
        # same UID whether estimated or real, so the published schedule updates the estimate in place
        out.append(dict(b, uid=f"{slug}-{day:%Y%m%d}-{kind.lower()}", slugs=[slug],
                        title=title + (" (estimado)" if b.get("estimated") else "")))
    return out


def session_event(x):
    url = f"cronogramas/{x['schedule']}" if x["schedule"] else f"{x['slugs'][0]}/calendario"
    return [f"UID:{x['uid']}@actc-cal", f"DTSTART:{x['start']}", f"DTEND:{x['end']}",
            f"SUMMARY:{esc(x['title'])}", f"LOCATION:{esc(x['location'])}", f"URL:https://www.actc.org.ar/{url}",
            *(["STATUS:TENTATIVE"] if x.get("estimated") else [])]


def day_of(stamp):
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").date()


def estimates(slug, races, saved, today):
    """Upcoming races with no published schedule get the last real weekend's times, shifted to their date."""
    mine = [x for x in saved if slug in x["slugs"]]
    if not mine:
        return []
    last = mine[-1]["schedule"]  # saved is sorted by start, so this is the most recent real weekend
    template = [x for x in mine if x["schedule"] == last]
    weekend = range(-3, 1)  # sessions run from Thursday to race Sunday
    in_weekend = lambda x, e: (day_of(x["start"]) - e["date"]).days in weekend
    # anchor on the template's race day, not its last session (which may be Saturday)
    sunday = next((e["date"] for e in races if in_weekend(template[-1], e)), day_of(template[-1]["start"]))
    out = []
    for e in races:
        if e["date"] < today or any(in_weekend(x, e) for x in mine):
            continue
        shift = timedelta(days=(e["date"] - sunday).days)
        move = lambda t: f"{datetime.strptime(t, '%Y%m%dT%H%M%SZ') + shift:%Y%m%dT%H%M%SZ}"
        out += [dict(x, uid=f"est-{slug}-{e['date']:%Y%m%d}-{x['uid']}", schedule=None, slugs=[slug], estimated=True,
                     location=f"Autódromo {e['track']}, {e['city']}",
                     start=move(x["start"]), end=move(x["end"])) for x in template]
    return out


def ics(name, events):
    # ponytail: fixed DTSTAMP so unchanged data yields an identical file and the Action doesn't commit daily
    stamp = "20260101T000000Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//actc-cal//ES", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{esc(name)}", "X-WR-TIMEZONE:America/Argentina/Buenos_Aires",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for body in events:
        lines += ["BEGIN:VEVENT", f"DTSTAMP:{stamp}", *body, "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(map(fold, lines)) + "\r\n"


def fetch(path):
    req = urllib.request.Request(f"https://www.actc.org.ar/{path}", headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode()


def sessions(schedule):
    """On-track sessions of one published schedule, times converted to UTC."""
    c = schedule["circuit"] or {}
    location = ", ".join(filter(None, [c.get("name"), c.get("location")]))
    out = []
    for day in schedule["days"]:
        for i in day["items"]:
            slugs = sorted({l["label"].lower() for l in i["logos"] if l.get("label")} & CATS.keys())
            if i["trackType"] == "administrative" or not slugs or not kind_of(i["title"]):
                continue
            # ponytail: Argentina is fixed UTC-3 with no DST; use zoneinfo if that ever changes
            midnight = datetime.fromisoformat(day["date"][:10]) + timedelta(hours=3)
            start = midnight + timedelta(minutes=i["startMin"])
            end = midnight + timedelta(minutes=i["endMin"]) if (i["endMin"] or 0) > i["startMin"] else start + timedelta(minutes=15)
            title = i["title"].strip() + (f" · {i['subtitle'].strip()}" if i["subtitle"] else "")
            out.append(dict(uid=i["id"], schedule=schedule["id"], slugs=slugs, title=title, location=location,
                            start=f"{start:%Y%m%dT%H%M%SZ}", end=f"{end:%Y%m%dT%H%M%SZ}"))
    return out


def update_sessions():
    saved = json.loads(SESSIONS.read_text()) if SESSIONS.exists() else []
    try:
        live = [x for x in json.loads(fetch("api/schedules"))["data"] if x.get("published")]
    except urllib.error.HTTPError as err:  # Cloudflare 403s datacenter IPs (e.g. GitHub Actions) on /api
        print(f"api/schedules: {err}; keeping {len(saved)} saved sessions")
        return saved
    fresh = [s for x in live for s in sessions(x)]
    # a live schedule replaces everything we stored for it, so edits and removals propagate
    ids = {x["id"] for x in live}
    return sorted([s for s in saved if s["schedule"] not in ids] + fresh, key=lambda s: (s["start"], s["uid"]))


def need_times(today):
    """True while this weekend has a race but ACTC hasn't published its schedule yet (drives the hourly weekend timer)."""
    sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    feed = (OUT / "actc.ics").read_text()
    if f"DTSTART;VALUE=DATE:{sunday:%Y%m%d}" not in feed and f"DTSTART:{sunday:%Y%m%d}T" not in feed:
        return False  # no race this weekend
    saved = json.loads(SESSIONS.read_text()) if SESSIONS.exists() else []
    return not any(timedelta(days=-3) <= day_of(x["start"]) - sunday <= timedelta(0) for x in saved)


def main():
    sample = "|Fecha 12 — SAN NICOLAS|dom, 04 oct 2026|Ciudad San Nicolas| — San Nicolas, Buenos Aires|"
    assert parse(sample) == [dict(n=12, name="San Nicolas", date=date(2026, 10, 4),
                                  track="Ciudad San Nicolas", city="San Nicolas, Buenos Aires")]
    assert esc("a,b;c") == "a\\,b\\;c"
    assert all(len(l.encode()) <= 75 for l in fold("LOCATION:" + "Autódromo " * 20).split("\r\n"))
    item = dict(id="i1", startMin=840, endMin=890, title="FINAL TC", subtitle=None, trackType="activities_tv",
                logos=[dict(label="TC")])
    noise = dict(item, id="i2", title="Salida a pista TC")
    [got] = sessions(dict(id="s1", circuit=None, days=[dict(date="2026-09-13T00:00:00.000Z", items=[item, noise])]))
    assert (got["start"], got["end"], got["slugs"]) == ("20260913T170000Z", "20260913T175000Z", ["tc"])
    races = [dict(n=12, name="X", date=date(2026, 10, 4), track="T", city="C"),
             dict(n=13, name="Y", date=date(2026, 10, 25), track="T", city="C")]
    real = dict(got, start="20261003T170000Z", end="20261003T175000Z", schedule="s1")  # fecha 12 already published
    [est] = estimates("tc", races, [real], date(2026, 10, 2))
    assert (est["start"], est["estimated"], est["uid"]) == ("20261024T170000Z", True, "est-tc-20261025-i1")
    assert estimates("tcm", races, [real], date(2026, 10, 2)) == []
    q = dict(got, title="Clasificación TC", start="20260912T194800Z", end="20260912T195600Z")
    bs = blocks("tc", [q, dict(q, start="20260912T202700Z", end="20260912T203500Z"), got, est], races)
    assert [(b["title"], b["start"], b["end"]) for b in bs] == [
        ("TC · Clasificación", "20260912T194800Z", "20260912T203500Z"),
        ("TC · Final", "20260913T170000Z", "20260913T175000Z"),
        ("TC · Final — Y (estimado)", "20261024T170000Z", "20261024T175000Z")]
    found = {slug: parse(fetch(f"{slug}/calendario")) for slug in CATS}
    saved = update_sessions()
    for slug, evs in found.items():
        print(f"{slug}: {len(evs)} fechas, {sum(slug in x['slugs'] for x in saved)} sesiones")
    if empty := [s for s, evs in found.items() if not evs]:
        sys.exit(f"no events parsed for {empty}; not writing (site markup changed?)")
    OUT.mkdir(exist_ok=True)
    SESSIONS.write_text(json.dumps(saved, ensure_ascii=False, indent=1) + "\n")
    today = date.today()
    est = {slug: estimates(slug, evs, saved, today) for slug, evs in found.items()}
    everything = []
    for slug, evs in found.items():
        bs = blocks(slug, [x for x in saved if slug in x["slugs"]] + est[slug], evs)
        timed = {e["n"] for e in evs for b in bs if (day_of(b["start"]) - e["date"]).days in range(-3, 1)}
        # all-day event only for races we have no times for yet
        events = [race_event(slug, e) for e in evs if e["n"] not in timed] + [session_event(b) for b in bs]
        print(f"{slug}: {len(events)} eventos ({len(bs)} bloques)")
        (OUT / f"{slug}.ics").write_bytes(ics(f"ACTC {CATS[slug]}", events).encode())
        everything += events
    (OUT / "actc.ics").write_bytes(ics("ACTC", everything).encode())


if __name__ == "__main__":
    if sys.argv[1:] == ["--need-times"]:
        sys.exit(0 if need_times(date.today()) else 1)
    main()
