"""Scrape ACTC calendars into subscribable .ics files under docs/."""
import html, re, sys, urllib.request
from datetime import date, timedelta
from pathlib import Path

CATS = {"tc": "TC", "tcp": "TC Pista", "tcm": "TC Mouras", "tcpm": "TC Pista Mouras",
        "tcpk": "TC Pick Up", "tcppk": "TC Pista Pick Up"}
MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
          "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12}
# ponytail: regex scrape of rendered text, breaks if ACTC changes markup; upgrade to their Next.js data payload then
ROW = re.compile(r"Fecha (\d+) — ([^|]+)\|\w+, (\d+) (\w+) (\d{4})\|([^|]+)\| — ([^|]+)")
OUT = Path(__file__).parent / "docs"


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


def ics(name, items):
    # ponytail: fixed DTSTAMP so unchanged data yields an identical file and the Action doesn't commit daily
    stamp = "20260101T000000Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//actc-cal//ES", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{esc(name)}", "X-WR-TIMEZONE:America/Argentina/Buenos_Aires",
             "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
    for slug, e in items:
        lines += ["BEGIN:VEVENT", f"UID:{slug}-{e['date'].year}-{e['n']}@actc-cal", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{e['date']:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{e['date'] + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{esc(CATS[slug] + ' · Fecha ' + str(e['n']) + ' — ' + e['name'])}",
                  f"LOCATION:{esc('Autódromo ' + e['track'] + ', ' + e['city'])}",
                  f"URL:https://www.actc.org.ar/{slug}/calendario", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(map(fold, lines)) + "\r\n"


def fetch(slug):
    req = urllib.request.Request(f"https://www.actc.org.ar/{slug}/calendario", headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode()


def main():
    sample = "|Fecha 12 — SAN NICOLAS|dom, 04 oct 2026|Ciudad San Nicolas| — San Nicolas, Buenos Aires|"
    assert parse(sample) == [dict(n=12, name="San Nicolas", date=date(2026, 10, 4),
                                  track="Ciudad San Nicolas", city="San Nicolas, Buenos Aires")]
    assert esc("a,b;c") == "a\\,b\\;c"
    assert all(len(l.encode()) <= 75 for l in fold("LOCATION:" + "Autódromo " * 20).split("\r\n"))
    found = {slug: parse(fetch(slug)) for slug in CATS}
    for slug, evs in found.items():
        print(f"{slug}: {len(evs)}")
    if empty := [s for s, evs in found.items() if not evs]:
        sys.exit(f"no events parsed for {empty}; not writing (site markup changed?)")
    OUT.mkdir(exist_ok=True)
    for slug, evs in found.items():
        (OUT / f"{slug}.ics").write_bytes(ics(f"ACTC {CATS[slug]}", [(slug, e) for e in evs]).encode())
    everything = sorted(((s, e) for s, evs in found.items() for e in evs), key=lambda x: x[1]["date"])
    (OUT / "actc.ics").write_bytes(ics("ACTC", everything).encode())


if __name__ == "__main__":
    main()
