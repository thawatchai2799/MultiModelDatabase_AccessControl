"""Shared result-record schema, written as JSON Lines (one JSON object per
line, append-only) rather than a single JSON array or CSV built up in
memory -- a 20+ hour run across many trials must survive being interrupted
partway through without losing everything collected so far, and JSONL
survives a truncated/killed process with only the last line lost, not the
whole file.
"""
import dataclasses
import json
import time
from pathlib import Path


@dataclasses.dataclass
class TrialResult:
    scenario: str            # "a" | "b1".."b6" | "c"
    scale: int
    seed: int
    resource_id: str
    principal_id: str
    layer: str | None        # "relational" | "nosql" | "vector" | None (B-mechanisms: None)
    t_issued: float          # time.monotonic() at revoke
    leak_window_s: float | None
    confirmed_contained: bool | None
    checks_performed: int | None
    wall_clock: str = dataclasses.field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    extra: dict = dataclasses.field(default_factory=dict)


class ResultsWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # If an earlier run was cut off mid-write, the file ends in a partial
        # line with no newline. Appending straight after it would glue the
        # first new record onto that fragment, and analyze.py would then skip
        # BOTH as one unparseable line -- losing a real trial silently. Close
        # the fragment off first so it is skipped alone.
        if self.path.exists() and self.path.stat().st_size > 0:
            with open(self.path, "rb") as fh:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    with open(self.path, "a") as fix:
                        fix.write("\n")
        self._fh = open(self.path, "a", buffering=1)  # line-buffered: each
        # write() call is flushed to disk promptly rather than sitting in a
        # Python-level buffer that a crash could lose along with everything
        # since the last flush.

    def write(self, result: TrialResult) -> None:
        self._fh.write(json.dumps(dataclasses.asdict(result)) + "\n")

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
