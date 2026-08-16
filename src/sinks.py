"""
sinks.py — where a poll cycle's output goes.

`poll_cycle()` in poll_live.py must stay ONE implementation. When the poller runs on
Lambda it writes to S3 instead of local disk, and the tempting shortcut — a separate
handler that reimplements fetch-and-extract against S3 — would make the parallel-run
cutover meaningless: the diff would be testing the reimplementation, not the port. Same
mistake as the duplicated feature lists in D35, which produced an artifact that could not
serve and which nothing detected.

So storage is the only thing that varies, and it varies behind this interface.

    begin_cycle(cycle_ts, day)      start a cycle; both values already Dublin-correct
    put_raw(kind, body, station)    archive one response, returns its reference string
    put_records(rows)               emit extracted rows
    put_failure(record)             record a request that exhausted its retries
    end_cycle(meta)                 close the cycle

`put_raw` returns the reference that goes into each row's `source_file`, because local and
S3 lay their keys out differently and the caller should not need to know which it has.

Two implementations are planned:

  LocalSink   writes files as the poller always has — one gzip per station, rows appended
              to a per-day JSONL. Behaviour-preserving by design: the refactor that
              introduced this interface must not change a single byte of local output.

  S3Sink      (not yet built) buffers and writes one object per cycle rather than one per
              station, per the batching note in CLAUDE.md, and records cycle metadata so
              a partial cycle under Lambda's time budget is visible downstream.
"""

import json
from pathlib import Path


class LocalSink:
    """Files on disk, exactly as poll_live.py has always written them.

    Deliberately does nothing with cycle metadata. A local run has no time budget and so
    never produces a partial cycle; inventing a metadata file here would be output the
    old code never wrote, which would muddy the very diff this refactor has to pass.
    """

    def __init__(self, raw_dir: Path, out_dir: Path, failure_log: Path,
                 write_gz, append_jsonl):
        # write_gz and append_jsonl are injected rather than imported so this module
        # stays free of the backfill import chain and is trivial to exercise in isolation.
        self.raw_dir = Path(raw_dir)
        self.out_dir = Path(out_dir)
        self.failure_log = Path(failure_log)
        self._write_gz = write_gz
        self._append_jsonl = append_jsonl
        self.cycle_ts = None
        self.day = None

    def begin_cycle(self, cycle_ts: str, day: str) -> None:
        self.cycle_ts = cycle_ts
        self.day = day

    def put_raw(self, kind: str, body: bytes, station: str | None = None) -> str:
        if kind == "current":
            rel = f"current/{self.cycle_ts}.xml.gz"
        elif kind == "station":
            rel = f"station/{station}/{self.cycle_ts}.xml.gz"
        else:
            raise ValueError(f"unknown raw kind {kind!r}")
        self._write_gz(self.raw_dir / rel, body)
        return rel

    def put_records(self, rows: list) -> None:
        # Appended one row at a time, as before: the file must survive a kill at any
        # instant, and a partially written batch is worse than a short one.
        for row in rows:
            self._append_jsonl(self.out_dir / f"{self.day}.jsonl", row)

    def put_failure(self, record: dict) -> None:
        self._append_jsonl(self.failure_log, record)

    def end_cycle(self, meta: dict) -> None:
        return None


class MemorySink:
    """Collects everything in memory. For tests and for diffing one cycle against another
    without touching disk."""

    def __init__(self):
        self.raw = {}
        self.records = []
        self.failures = []
        self.cycles = []
        self.cycle_ts = None
        self.day = None

    def begin_cycle(self, cycle_ts: str, day: str) -> None:
        self.cycle_ts, self.day = cycle_ts, day

    def put_raw(self, kind: str, body: bytes, station: str | None = None) -> str:
        rel = (f"current/{self.cycle_ts}.xml.gz" if kind == "current"
               else f"station/{station}/{self.cycle_ts}.xml.gz")
        self.raw[rel] = body
        return rel

    def put_records(self, rows: list) -> None:
        self.records.extend(rows)

    def put_failure(self, record: dict) -> None:
        self.failures.append(record)

    def end_cycle(self, meta: dict) -> None:
        self.cycles.append(dict(meta))

    def as_jsonl(self) -> str:
        return "".join(json.dumps(r, sort_keys=True) + "\n" for r in self.records)
