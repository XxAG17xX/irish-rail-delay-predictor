"""
calibration.py — re-centre the 80% range on how trains have actually run lately (D89).

Why this exists
---------------
The model learned from June to August. From September the railway ran later than that: the
share of arrivals more than 15 minutes late went from 1.1% in August to 5.1% in November,
and the range's late misses rose from 10% to 25% while its early misses stayed near 10%.
The middle estimate stayed better than Irish Rail's, so the fault is the range, not the model.

What it does
------------
Each night the scorer fits two offsets per (line group, lead band) from the last
`WINDOW_DAYS` of scored outcomes, measured against the model's RAW quantiles:

    lower offset = 10th percentile of (actual - raw q10)
    upper offset = 90th percentile of (actual - raw q90)

so that on the fitting window exactly 10% of arrivals fall below the range and 10% above.
The API adds them to each new prediction's raw q10 and q90. The median is never moved, so
the error figures and the head-to-head against Irish Rail are unchanged by construction.

Why it is not leakage
---------------------
Offsets are fitted only on outcomes scored before the prediction is made: the file the API
reads is written by the nightly scorer from days that have already happened. Nothing about
a prediction's own outcome can reach it. Offsets are fitted against raw quantiles, which
every row logs, so a correction is never applied on top of a previous correction.

Thin cells
----------
A (group, band) cell with fewer than `MIN_N` scored rows in the window uses its lead band's
offset across all groups; a band with fewer uses no offset at all.

Backtest (2026-11-19, one week at a time from 1 October, fitted on the 14 days before it):
coverage 72.2/72.3/70.1/64.9/68.7/68.5/67.0% before, 78.8/80.2/78.4/73.9/81.5/81.4/79.4%
after; median range width 240s to 333s in the last week. Full table in D89.
"""

from __future__ import annotations

from datetime import datetime, timezone

WINDOW_DAYS = 14
MIN_N = 200
SCHEME = "split-conformal per (station_group, lead_band), asymmetric, raw-quantile residuals"


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile, the same definition pandas uses by default."""
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _raw(row: dict, side: str):
    """A row's raw model quantile: logged separately once calibration is serving, and equal
    to the served one before it was."""
    v = row.get(f"raw_q{side}_sec")
    return v if v is not None else row.get(f"pred_q{side}_sec")


def fit(rows: list[dict], window: tuple[str, str]) -> dict:
    """Offsets from scored rows. `window` is the (first, last) service date the rows cover,
    recorded so the file says exactly which outcomes it learned from."""
    cells: dict[str, dict] = {}
    bands: dict[str, dict] = {}
    for r in rows:
        if r.get("score_state") != "scored" or r.get("actual_delay_sec") is None:
            continue
        q10, q90 = _raw(r, "10"), _raw(r, "90")
        band = r.get("lead_band")
        if q10 is None or q90 is None or not isinstance(band, str) or not band:
            continue
        group = r.get("station_group")
        group = group if isinstance(group, str) else ""
        lo, hi = r["actual_delay_sec"] - q10, r["actual_delay_sec"] - q90
        for key, store in ((f"{group}|{band}", cells), (band, bands)):
            s = store.setdefault(key, {"lo": [], "hi": []})
            s["lo"].append(lo)
            s["hi"].append(hi)

    def summarise(store):
        return {k: {"n": len(v["lo"]), "lo": round(_quantile(v["lo"], 0.10)),
                    "hi": round(_quantile(v["hi"], 0.90))}
                for k, v in sorted(store.items()) if len(v["lo"]) >= MIN_N}

    return {"version": datetime.now(timezone.utc).strftime("cal-%Y%m%dT%H%M%SZ"),
            "scheme": SCHEME, "window": list(window), "window_days": WINDOW_DAYS,
            "min_n": MIN_N, "cells": summarise(cells), "bands": summarise(bands)}


# A train already past its scheduled arrival has a negative lead and no band, but it is the
# most imminent arrival on a board, so it takes the shortest band's offsets.
OVERDUE_BAND = "0-5 min"


def offsets(cal: dict | None, group: str, band: str | None) -> tuple[int, int, str | None]:
    """(lower, upper, version) for one prediction; (0, 0, None) without a calibration."""
    if not cal:
        return 0, 0, None
    band = band or OVERDUE_BAND
    c = cal.get("cells", {}).get(f"{group or ''}|{band}") or cal.get("bands", {}).get(band)
    if not c:
        return 0, 0, cal.get("version")
    return int(c["lo"]), int(c["hi"]), cal.get("version")


def apply(q10: int, q50: int, q90: int, lo: int, hi: int) -> tuple[int, int]:
    """The served range. Held either side of the median, which is never moved."""
    return min(q10 + lo, q50), max(q90 + hi, q50)


def _self_check():
    # 300 rows per cell: actual sits 60 s past raw q90 in a tenth of them, 30 s inside
    # otherwise, so the upper offset is the 90th percentile of (actual - q90).
    rows = []
    for i in range(300):
        late = i % 10 == 0
        rows.append({"score_state": "scored", "station_group": "dart", "lead_band": "0-5 min",
                     "pred_q10_sec": -60, "pred_q90_sec": 120,
                     "actual_delay_sec": 180 if late else 90})
    cal = fit(rows, ("2026-11-01", "2026-11-14"))
    c = cal["cells"]["dart|0-5 min"]
    # 270 residuals of -30 and 30 of +60: the interpolated 90th percentile sits a tenth of
    # the way from the 270th to the 271st, -30 + 0.1 * 90 = -21.
    assert c["n"] == 300 and c["lo"] == 150 and c["hi"] == -21, c
    assert cal["bands"]["0-5 min"]["n"] == 300

    # A thin cell falls back to its band; an unknown band gets nothing.
    assert offsets(cal, "dart", "0-5 min")[:2] == (150, -21)
    assert offsets(cal, "other", "0-5 min")[:2] == (150, -21), "band fallback"
    assert offsets(cal, "dart", "60+ min") == (0, 0, cal["version"])
    assert offsets(None, "dart", "0-5 min") == (0, 0, None)
    assert offsets(cal, "dart", None)[:2] == (150, -21), "an overdue train takes 0-5 min"

    # Raw quantiles win over served ones, so a correction never compounds.
    raw_row = {"score_state": "scored", "lead_band": "0-5 min", "station_group": "dart",
               "pred_q10_sec": -999, "pred_q90_sec": 999, "raw_q10_sec": -60, "raw_q90_sec": 120,
               "actual_delay_sec": 90}
    assert _raw(raw_row, "10") == -60 and _raw(raw_row, "90") == 120

    # The range never crosses the median.
    assert apply(-60, 30, 120, 150, -30) == (30, 90)
    assert apply(-60, 30, 120, -20, 60) == (-80, 180)
    # A row without a lead band, or with a non-string one (NaN from a table), is skipped.
    assert fit([{**rows[0], "lead_band": float("nan")}] * 500, ("a", "b"))["cells"] == {}
    # Declined and echo rows teach nothing.
    assert fit([{"score_state": "echo_suspect", "lead_band": "0-5 min", "actual_delay_sec": 1,
                 "pred_q10_sec": 0, "pred_q90_sec": 0}] * 500, ("a", "b"))["cells"] == {}
    print("calibration.py self-check passed")


if __name__ == "__main__":
    _self_check()
