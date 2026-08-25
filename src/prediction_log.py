"""
prediction_log.py — write every served prediction down before its outcome exists.

The accuracy page is only trustworthy if a prediction provably predates the arrival it
predicts (CLAUDE.md leakage rules). Three consequences shape this module:

Rows carry the predicted quantiles, not the inputs. If scoring could re-derive a
prediction it would be running today's model against a known outcome, which is the
regeneration the rules forbid. The scorer reads these rows and appends outcomes; it never
recomputes them, and its IAM role has no write access to this prefix.

Writes fail closed. A prediction that could not be logged is not served, so the accuracy
page measures the whole population rather than whichever subset happened to log
successfully. Unlogged predictions would not be missing at random: they would cluster
during infrastructure trouble, which is exactly when behaviour is unusual.

Every row goes to S3 and to stdout. Lambda ships stdout to CloudWatch, which stamps it at
ingestion, so the second copy is timestamped by AWS rather than by our own clock. That is
independent corroboration of the ordering claim for the cost of one print. S3 is written
first, so a stdout line exists if and only if the prediction was logged and served.

What this does NOT claim: the log is tamper-evident, not tamper-proof. IAM gives the API
PutObject and nothing else, and bucket versioning means an overwrite leaves the original
recoverable. Neither stops an account admin rewriting history. Object Lock would, but it
can only be enabled at bucket creation. Say so on the accuracy page rather than implying a
guarantee that is not there.
"""

import json
import time
import uuid
from datetime import datetime

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")

# Without any one of these a row cannot be scored. Checked before the write rather than
# discovered by the scorer weeks later against a log that cannot be rebuilt.
REQUIRED = (
    "predicted_at", "model_version",
    "train_date", "train_code", "station_code", "scheduled_arrival",
    "pred_q10_sec", "pred_q50_sec", "pred_q90_sec",
)


class LogWriteFailed(Exception):
    """The prediction could not be logged, so it must not be served."""


def iso_train_date(s):
    """'25 Aug 2026' -> '2026-08-25' for the partition key."""
    d, m, y = s.strip().split()
    return f"{int(y):04d}-{MONTHS.index(m[:3].lower()) + 1:02d}-{int(d):02d}"


class PredictionLog:
    def __init__(self, bucket, prefix, client, retries=1, backoff=0.25):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client
        self.retries = retries
        self.backoff = backoff

    def write(self, rows, request_id=None):
        """Log one request's predictions. Returns the S3 key. Raises LogWriteFailed."""
        if not rows:
            raise LogWriteFailed("no rows to log")

        request_id = request_id or uuid.uuid4().hex[:12]
        for row in rows:
            missing = [f for f in REQUIRED if row.get(f) is None]
            if missing:
                raise LogWriteFailed(f"row missing required fields: {missing}")
            row.setdefault("prediction_id", uuid.uuid4().hex)
            row.setdefault("api_request_id", request_id)

        # Partitioned by SERVICE date, not prediction date: the scorer joins on the train
        # date, so scoring one day reads exactly one prefix.
        day = iso_train_date(rows[0]["train_date"])
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        key = f"{self.prefix}/date={day}/{stamp}-{request_id}.jsonl"
        body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8")

        last = None
        for attempt in range(self.retries + 1):
            try:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
                break
            except Exception as e:
                last = e
                if attempt < self.retries:
                    time.sleep(self.backoff)
        else:
            raise LogWriteFailed(f"S3 write failed after {self.retries + 1} attempts: {last}")

        # After the durable write, so a CloudWatch line means the prediction was logged
        # AND served. Printing first would leave lines for predictions nobody received.
        for row in rows:
            print(json.dumps({"log": "prediction", "key": key, **row}, sort_keys=True))
        return key


def _self_check():
    class Stub:
        def __init__(self, fail=0):
            self.fail, self.puts = fail, []

        def put_object(self, Bucket, Key, Body):
            if self.fail:
                self.fail -= 1
                raise RuntimeError("simulated S3 outage")
            self.puts.append((Key, Body))

    base = {
        "predicted_at": "2026-08-25T14:03:11+01:00",
        "model_version": "20260813T221035Z-0c444e3",
        "train_date": "25 Aug 2026", "train_code": "A220", "station_code": "THRLS",
        "scheduled_arrival": "17:06:30",
        "pred_q10_sec": 42, "pred_q50_sec": 130, "pred_q90_sec": 448,
        "operator_eta": "17:08:30",
    }

    s = Stub()
    key = PredictionLog("b", "predictions", s, backoff=0).write([dict(base)])
    assert key.startswith("predictions/date=2026-08-25/"), key
    assert len(s.puts) == 1
    logged = json.loads(s.puts[0][1].decode().strip())
    assert logged["prediction_id"] and logged["api_request_id"], "ids not filled in"

    # one transient failure is retried, not surfaced
    s = Stub(fail=1)
    PredictionLog("b", "predictions", s, retries=1, backoff=0).write([dict(base)])
    assert len(s.puts) == 1, "retry did not recover"

    # a persistent failure must raise, so the caller cannot serve an unlogged prediction
    s = Stub(fail=99)
    try:
        PredictionLog("b", "predictions", s, retries=1, backoff=0).write([dict(base)])
        raise AssertionError("should have raised LogWriteFailed")
    except LogWriteFailed:
        pass

    # an unscoreable row is refused before it reaches S3
    s = Stub()
    bad = dict(base)
    del bad["pred_q50_sec"]
    try:
        PredictionLog("b", "predictions", s, backoff=0).write([bad])
        raise AssertionError("should have rejected the incomplete row")
    except LogWriteFailed as e:
        assert "pred_q50_sec" in str(e)
    assert not s.puts, "incomplete row was written anyway"

    print("prediction_log self-check passed")


if __name__ == "__main__":
    _self_check()
