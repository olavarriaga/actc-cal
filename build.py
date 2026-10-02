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
# ponytail: keyword filter for on-track sessions; skips "Salida a pista", TV/grid notes, admin items
KEEP = re.compile(r"entrenamiento|clasificaci|\bserie\b|\bfinal\b|\bcarrera\b", re.I)


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


def session_event(x):
    return [f"UID:{x['uid']}@actc-cal", f"DTSTART:{x['start']}", f"DTEND:{x['end']}",
            f"SUMMARY:{esc(x['title'])}", f"LOCATION:{esc(x['location'])}",
            f"URL:https://www.actc.org.ar/cronogramas/{x['schedule']}"]


def ics(name, events):
    # ponytail: fixed DTSTAMP so unchanged data yields an identical file and the Action doesn't commit daily
    stamp = "20260101T000000Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//actc-cal//ES", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{esc(name)}", "X-WR-TIMEZONE:America/Argentina/Buenos_Aires",
             "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
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
            if i["trackType"] == "administrative" or not slugs or not KEEP.search(i["title"]):
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
    live = [x for x in json.loads(fetch("api/schedules"))["data"] if x.get("published")]
    fresh = [s for x in live for s in sessions(x)]
    # a live schedule replaces everything we stored for it, so edits and removals propagate
    ids = {x["id"] for x in live}
    return sorted([s for s in saved if s["schedule"] not in ids] + fresh, key=lambda s: (s["start"], s["uid"]))


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
    found = {slug: parse(fetch(f"{slug}/calendario")) for slug in CATS}
    saved = update_sessions()
    for slug, evs in found.items():
        print(f"{slug}: {len(evs)} fechas, {sum(slug in x['slugs'] for x in saved)} sesiones")
    if empty := [s for s, evs in found.items() if not evs]:
        sys.exit(f"no events parsed for {empty}; not writing (site markup changed?)")
    OUT.mkdir(exist_ok=True)
    SESSIONS.write_text(json.dumps(saved, ensure_ascii=False, indent=1) + "\n")
    for slug, evs in found.items():
        events = [race_event(slug, e) for e in evs] + [session_event(x) for x in saved if slug in x["slugs"]]
        (OUT / f"{slug}.ics").write_bytes(ics(f"ACTC {CATS[slug]}", events).encode())
    races = sorted(((s, e) for s, evs in found.items() for e in evs), key=lambda x: x[1]["date"])
    everything = [race_event(s, e) for s, e in races] + [session_event(x) for x in saved]
    (OUT / "actc.ics").write_bytes(ics("ACTC", everything).encode())


if __name__ == "__main__":
    main()
