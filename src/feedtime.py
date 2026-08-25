"""
feedtime.py — parsing the Irish Rail feed's time and date formats, once.

These conversions were written three times over: `to_seconds` in parse_raw.py, `hms` in
compare_to_operator.py, and `iso_train_date` in prediction_log.py. Same job, three names.
The live API needs them too, and a fourth copy is how D35 happened.

parse_raw.py and compare_to_operator.py still carry their own versions. Both work and are
covered by their own checks, so converging them is a follow-up rather than something to
do to working code three weeks from a deadline. New code imports from here.
"""

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")

# 00:00:00 is structurally absent, not midnight: an origin has no scheduled arrival.
PLACEHOLDER = {"", "00:00", "00:00:00"}

HALF_DAY = 43200
FULL_DAY = 86400


def hms(t):
    """'HH:MM:SS' or 'HH:MM' to seconds since midnight. None if absent or unparseable."""
    if t is None or t in PLACEHOLDER:
        return None
    parts = t.strip().split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return None


def unwrap(seconds):
    """Make a journey's clock times monotonic across midnight by adding days.

    A service leaving at 23:40 and arriving 00:20 would otherwise look like it went
    backwards by 23 hours, which turns a 40-minute run into a negative delay.
    """
    out, offset, prev = [], 0, None
    for s in seconds:
        if s is None:
            out.append(None)
            continue
        if prev is not None and s + offset < prev - HALF_DAY:
            offset += FULL_DAY
        v = s + offset
        out.append(v)
        prev = v
    return out


def iso_train_date(s):
    """'25 Aug 2026' to '2026-08-25'."""
    d, m, y = s.strip().split()
    return f"{int(y):04d}-{MONTHS.index(m[:3].lower()) + 1:02d}-{int(d):02d}"


def feed_train_date(d):
    """A date to the format TrainDate wants: '25 Aug 2026'."""
    return f"{d.day:02d} {MONTHS[d.month - 1].capitalize()} {d.year}"
