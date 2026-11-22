"""Bounded paired power integrals and observation windows."""
from .const import MAX_SAMPLE_GAP, WINDOW_SECONDS
from .schema import numeric


class Rolling:
    def __init__(self, samples=None):
        self.samples = list(samples or [])

    def add(self, timestamp, values):
        if self.samples and timestamp < self.samples[-1][0]:
            return
        if self.samples and timestamp == self.samples[-1][0]:
            self.samples[-1] = [timestamp, values]
        else:
            self.samples.append([timestamp, values])
        # One anchor before the cutoff defines the interval that crosses it.
        cutoff = timestamp-WINDOW_SECONDS
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.pop(0)
        self.samples = self.samples[-4000:]

    def changes(self, key, timestamp):
        window = [(t, v) for t, v in self.samples if timestamp-WINDOW_SECONDS <= t <= timestamp]
        rows = [(t, v[key]) for t, v in window if numeric(v.get(key)) is not None]
        if len(rows) < 2:
            return dict(ready=False, change=None, minimum=None, coverage=0.)
        # These windows receive one sample per minute. Only observed defrost pauses
        # between eligible readings may account for missing heating minutes.
        paused = sum(1 for t, v in window if rows[0][0] < t < rows[-1][0] and v.get("defrost"))
        coverage = min(1., (len(rows) + paused) * 60 / WINDOW_SECONDS)
        return dict(ready=coverage >= .8 and len(rows) >= 5 and timestamp-rows[-1][0] <= MAX_SAMPLE_GAP,
                    change=rows[-1][1]-rows[0][1], minimum=min(v for t, v in rows), coverage=coverage)

    def integral(self, key, timestamp, *, require_all=False):
        lo, area, covered, invalid = timestamp-WINDOW_SECONDS, 0., 0., False
        for index, (start, values) in enumerate(self.samples):
            end = self.samples[index+1][0] if index+1 < len(self.samples) else timestamp
            left, right = max(lo, start), min(timestamp, end)
            if right <= left:
                continue
            value = values.get(key)
            if numeric(value) is None or end-start > MAX_SAMPLE_GAP:
                invalid = True
                continue
            area += value*(right-left)
            covered += right-left
        available = covered >= .95*WINDOW_SECONDS and not (require_all and invalid)
        return dict(available=available, area=area, coverage=covered/WINDOW_SECONDS,
                    mean=area/covered if covered else None)

    def cop(self, timestamp):
        heat = self.integral("heat", timestamp, require_all=True)
        electric = self.integral("electric", timestamp, require_all=True)
        valid = heat["available"] and electric["available"] and abs(heat["coverage"]-electric["coverage"])*WINDOW_SECONDS <= 1 and electric["area"]/3600 >= .025
        return heat["area"]/electric["area"] if valid else None

