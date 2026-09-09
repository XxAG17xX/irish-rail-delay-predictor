"""
ratelimit.py — token buckets for the public API.

Why this exists
---------------
The Function URL is `AuthType: NONE` and nothing stood between a script and the service.
One `/board` call fans out to as many as seven requests against Irish Rail's free feed and
writes an object to S3 per prediction, so an unattended loop is both a bill and a rudeness
to an API whose operators asked for one to two requests a second and have no way to
complain.

What it is, and what it is NOT
------------------------------
This is an **in-process** limiter. Its state lives in one warm Lambda container and is lost
when that container is recycled, so a caller spread across several concurrent containers
gets several buckets. That is a real ceiling and it is the right trade anyway:

- it costs nothing, adds no service, and cannot itself fail;
- the account's concurrency limit is 10, so the worst case is bounded at roughly ten times
  the per-container rate rather than unbounded;
- the second bucket below is global to the container and caps outbound fan-out regardless
  of how many distinct callers are involved, which is the part Irish Rail actually feels.

A determined attacker with many addresses gets through. The fix for that is AWS WAF on the
distribution, which costs about six dollars a month and is the upgrade path when this stops
being enough. Recorded rather than pretended away.

# ponytail: in-process token bucket, per container. AWS WAF rate rules if this stops holding.
"""

from __future__ import annotations

import time


class Bucket:
    """One token bucket. `burst` tokens, refilled at `rate` per second."""

    __slots__ = ("rate", "burst", "tokens", "stamp")

    def __init__(self, rate: float, burst: float, now: float | None = None):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.stamp = time.monotonic() if now is None else now

    def take(self, now: float | None = None, cost: float = 1.0) -> float:
        """Spend one token. Returns 0.0 if allowed, else the seconds until it would be.

        The caller is told how long to wait rather than just being refused, because a
        client that knows when to come back does not poll, and a client that does not know
        polls immediately and makes the problem worse.
        """
        now = time.monotonic() if now is None else now
        # Clamped at zero. A clock that appears to go backwards would otherwise subtract
        # tokens and refuse everyone, and that is exactly what happened the first time this
        # was tested with an injected clock against a bucket stamped from time.monotonic().
        elapsed = max(0.0, now - self.stamp)
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.stamp = now
        if self.tokens >= cost:
            self.tokens -= cost
            return 0.0
        return (cost - self.tokens) / self.rate


class Limiter:
    """Per-caller buckets plus one shared bucket for the whole container.

    `max_keys` bounds memory: a flood of distinct addresses would otherwise grow the map
    without limit, which turns a rate limit into a way to exhaust the function's memory.
    When the map is full the least recently seen keys are dropped, which is the safe
    direction to be wrong: a dropped key gets a fresh bucket and is limited again from the
    next request, whereas refusing everyone would hand an attacker the outage.
    """

    def __init__(self, rate: float, burst: float, shared_rate: float, shared_burst: float,
                 max_keys: int = 2048):
        self.rate = rate
        self.burst = burst
        self.max_keys = max_keys
        self.shared_rate = shared_rate
        self.shared_burst = shared_burst
        # Built on first use so it shares whatever clock the caller passes, rather than
        # being stamped from time.monotonic() at import and disagreeing with an injected one.
        self.shared: Bucket | None = None
        self._buckets: dict[str, Bucket] = {}

    def check(self, key: str, now: float | None = None, cost: float = 1.0) -> float:
        """0.0 when the request may proceed, otherwise seconds to wait."""
        if self.shared is None:
            self.shared = Bucket(self.shared_rate, self.shared_burst, now)
        if len(self._buckets) >= self.max_keys:
            self._evict()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = Bucket(self.rate, self.burst, now)
        wait = bucket.take(now, cost)
        if wait:
            return wait
        # The caller passed its own bucket; the container's shared budget still applies.
        # A refusal here refunds the personal token, or a busy minute would silently cost
        # every caller their allowance twice.
        shared_wait = self.shared.take(now, cost)
        if shared_wait:
            bucket.tokens = min(bucket.burst, bucket.tokens + cost)
        return shared_wait

    def _evict(self) -> None:
        oldest = sorted(self._buckets.items(), key=lambda kv: kv[1].stamp)
        for key, _ in oldest[: max(1, len(oldest) // 4)]:
            del self._buckets[key]


def client_key(source_ip: str, forwarded_for: str | None) -> str:
    """Who to charge for this request.

    Through CloudFront every request arrives from a CloudFront address, so the origin
    request policy forwards `X-Forwarded-For` and the first hop is the visitor. A caller
    hitting the Function URL directly can put whatever it likes in that header, so this is
    honest about ordinary traffic and not a defence against someone deliberately evading
    it. The shared bucket is what covers that case.
    """
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return source_ip or "unknown"


def _self_check():
    b = Bucket(rate=1.0, burst=2.0, now=0.0)
    assert b.take(now=0.0) == 0.0
    assert b.take(now=0.0) == 0.0, "burst of two means two immediate takes"
    wait = b.take(now=0.0)
    assert 0.99 < wait < 1.01, f"third take should wait about a second, got {wait}"
    assert b.take(now=5.0) == 0.0, "refills over time"
    assert b.tokens <= b.burst, "refill never exceeds the burst"

    # The shared bucket refuses even when the personal one would allow, and the personal
    # token is refunded so the caller is not charged for a refusal.
    lim = Limiter(rate=10.0, burst=10.0, shared_rate=1.0, shared_burst=1.0)
    assert lim.check("a", now=0.0) == 0.0
    assert lim.check("b", now=0.0) > 0.0, "shared budget is exhausted"
    before = lim._buckets["b"].tokens
    lim.check("b", now=0.0)
    assert lim._buckets["b"].tokens == before, "a shared refusal must not keep charging"

    # Separate callers get separate budgets.
    lim2 = Limiter(rate=0.1, burst=1.0, shared_rate=100.0, shared_burst=100.0)
    assert lim2.check("one", now=0.0) == 0.0
    assert lim2.check("one", now=0.0) > 0.0
    assert lim2.check("two", now=0.0) == 0.0, "one caller's limit is not another's"

    # Eviction bounds memory without refusing anyone.
    small = Limiter(rate=1.0, burst=1.0, shared_rate=1e6, shared_burst=1e6, max_keys=8)
    for i in range(40):
        small.check(f"k{i}", now=float(i))
    assert len(small._buckets) <= 8, f"map grew to {len(small._buckets)}"

    assert client_key("1.2.3.4", None) == "1.2.3.4"
    assert client_key("1.2.3.4", "9.9.9.9, 1.2.3.4") == "9.9.9.9"
    assert client_key("", "  ") == "unknown"

    print("ratelimit.py self-check passed")


if __name__ == "__main__":
    _self_check()
