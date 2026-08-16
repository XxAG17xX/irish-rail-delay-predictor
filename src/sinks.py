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
              to a per-day JSONL — plus one cycle-metadata file per cycle.

  S3Sink      buffers and writes one object per cycle rather than one per station, per
              the batching note in CLAUDE.md.

  MemorySink  collects everything in memory, for tests.
"""

import gzip
import io
import json
import os
import tarfile
from pathlib import Path


class LocalSink:
    """Files on disk, as poll_live.py has always written them, plus cycle metadata.

    Cycle metadata mirrors the S3 layout one-file-per-cycle so the parallel-run diff can
    compare both sides symmetrically. It was deliberately omitted from the first version
    of this class — a local run has no deadline, so it never produces a partial cycle —
    but a control that records less than the candidate cannot be diffed against it, and
    changing the control mid-experiment is worse than changing it now.
    """

    def __init__(self, raw_dir: Path, out_dir: Path, failure_log: Path,
                 write_gz, append_jsonl, cycles_dir: Path = None):
        # write_gz and append_jsonl are injected rather than imported so this module
        # stays free of the backfill import chain and is trivial to exercise in isolation.
        self.raw_dir = Path(raw_dir)
        self.out_dir = Path(out_dir)
        self.failure_log = Path(failure_log)
        self.cycles_dir = Path(cycles_dir) if cycles_dir else self.out_dir.parent / "cycles"
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
        """One JSON file per cycle, matching the S3 layout so the diff stays symmetric.

        Atomic like everything else that writes here (D4): a cycle record that exists is
        a cycle record that is complete.
        """
        d = self.cycles_dir / self.day
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f"{self.cycle_ts}.json"
        tmp = dest.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
        os.replace(tmp, dest)


class S3Sink:
    """One object per cycle instead of one per station, per the batching note in
    CLAUDE.md — 31 objects every five minutes is ~259k PUTs/month for no gain.

    The boto3 client is injected, so this module never imports boto3 and can be exercised
    against a stub. Layout:

        {prefix}/raw/date=YYYY-MM-DD/{cycleTs}.tar.gz     current.xml + station/CODE.xml
        {prefix}/expected/date=YYYY-MM-DD/{cycleTs}.jsonl.gz
        {prefix}/cycles/date=YYYY-MM-DD/{cycleTs}.json
        {prefix}/failures/date=YYYY-MM-DD/{cycleTs}.jsonl  (only when there are any)

    Records carry the same field names and values as the local sink, with ONE exception:
    `source_file` necessarily differs, because it is a provenance pointer and the two
    layouts genuinely are different. Making S3 report a local-shaped path to flatter the
    diff would be a lie in the data. The comparison excludes that one field and requires
    exact agreement on every other.
    """

    def __init__(self, bucket: str, prefix: str, client):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client
        self.cycle_ts = None
        self.day = None
        self._raw = {}
        self._records = []
        self._failures = []

    def _key(self, kind: str, name: str) -> str:
        return f"{self.prefix}/{kind}/date={self.day}/{name}"

    def begin_cycle(self, cycle_ts: str, day: str) -> None:
        self.cycle_ts, self.day = cycle_ts, day
        self._raw, self._records, self._failures = {}, [], []

    def put_raw(self, kind: str, body: bytes, station: str | None = None) -> str:
        member = "current.xml" if kind == "current" else f"station/{station}.xml"
        self._raw[member] = body
        # Buffered, not written yet: the whole cycle becomes one tar.gz at end_cycle.
        return f"{self._key('raw', self.cycle_ts + '.tar.gz')}#{member}"

    def put_records(self, rows: list) -> None:
        self._records.extend(rows)

    def put_failure(self, record: dict) -> None:
        self._failures.append(record)

    def end_cycle(self, meta: dict) -> None:
        if self._raw:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for member, body in sorted(self._raw.items()):
                    info = tarfile.TarInfo(member)
                    info.size = len(body)
                    info.mtime = 0  # deterministic: same input gives the same object
                    tar.addfile(info, io.BytesIO(body))
            self._put(self._key("raw", f"{self.cycle_ts}.tar.gz"), buf.getvalue())

        if self._records:
            payload = "".join(json.dumps(r, sort_keys=True) + "\n"
                              for r in self._records).encode("utf-8")
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
                gz.write(payload)
            self._put(self._key("expected", f"{self.cycle_ts}.jsonl.gz"), buf.getvalue())

        if self._failures:
            payload = "".join(json.dumps(r, sort_keys=True) + "\n"
                              for r in self._failures).encode("utf-8")
            self._put(self._key("failures", f"{self.cycle_ts}.jsonl"), payload)

        self._put(self._key("cycles", f"{self.cycle_ts}.json"),
                  json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"))

    def _put(self, key: str, body: bytes) -> None:
        # A PUT is atomic: the object either appears whole or not at all, so none of the
        # temp-file machinery the local sink needs applies here.
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)


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
