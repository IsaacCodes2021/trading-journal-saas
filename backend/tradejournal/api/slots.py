"""How many imports run at once (doc 04: threat T5, and the launch gate "import concurrency limit").

Reading a file and regrouping a journal is the heaviest thing a request does: it holds a worker thread and the processor
for a second or more. Without a limit, a few people importing together (or one person pressing the button forty times)
could take every worker thread the server has and leave nothing for anyone else's page. So:

- at most `total` imports run at once in this process, and the rest are told to come back in a few seconds (429 with
  `Retry-After`), not queued, because a queue of waiting requests is the same thread problem in a different place;
- a person runs at most one at a time. The database already makes two of a person's imports take turns (a per-user
  advisory lock), but they would both be holding a thread while they waited.

The count is per process, which is the right size of limit for protecting a process. With more instances the limit is
`total` on each, and that is still a bound.
"""

from __future__ import annotations

import threading
from uuid import UUID

MINE = "You already have an import running. Wait for it to finish, then try again."
BUSY = "Imports are busy right now. Try again in a few seconds."
RETRY_AFTER_SECONDS = 5


class ImportSlots:
    def __init__(self, total: int) -> None:
        if total < 1:
            raise ValueError("at least one import must be allowed to run")
        self.total = total
        self._lock = threading.Lock()
        self._running: set[UUID] = set()  # the people with an import running: one each, so its size is how many run

    def take(self, person: UUID) -> str | None:
        """None when `person` now has a place; otherwise the sentence that says why not (nothing was taken)."""
        with self._lock:
            if person in self._running:
                return MINE
            if len(self._running) >= self.total:
                return BUSY
            self._running.add(person)
            return None

    def release(self, person: UUID) -> None:
        with self._lock:
            self._running.discard(person)

    @property
    def running(self) -> int:
        with self._lock:
            return len(self._running)
