"""Lifecycle ownership for runtime work, including delayed refresh children."""

from __future__ import annotations

import asyncio


class RuntimeTasks:
    def __init__(self, hass):
        self._hass = hass
        self._tasks: set[asyncio.Task] = set()
        self.closed = False

    def create(self, coroutine, name=None):
        if self.closed:
            coroutine.close()
            return None
        task = self._hass.async_create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel(self, *, close=False):
        self.closed = close
        tasks = self._tasks - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)

    @property
    def pending(self):
        return sum(not task.done() for task in self._tasks)
