import asyncio
import sys
import threading


class _TaskHandler:
    def __init__(self):
        self.tasks = []
        self._lock = threading.Lock()

    def create(self, coro):
        """Schedule the execution of a coroutine object in a spawn task."""
        task = create_task(coro)
        with self._lock:
            self.tasks.append(task)
        task.add_done_callback(self._remove_completed_task)
        return task

    def _remove_completed_task(self, task):
        try:
            with self._lock:
                self.tasks.remove(task)
        except ValueError:
            # May have been cancelled or removed otherwise
            ...

    async def cancel(self, task):
        task.cancel()
        await task

    async def cancel_all(self, wait=False):
        with self._lock:
            tasks = list(self.tasks)
            self.tasks.clear()

        for task in tasks:
            task.cancel()

        if wait and tasks:
            await asyncio.wait(tasks)

    async def wait(self):
        with self._lock:
            tasks = list(self.tasks)

        if tasks:
            await asyncio.wait(tasks)


if sys.version_info < (3, 7):
    # python <= 3.6 compatibility
    def get_running_loop():
        return asyncio.get_event_loop()

    def run(coro, debug=False):
        return get_running_loop().run_until_complete(coro)

    def create_task(coro):
        return get_running_loop().create_task(coro)

else:
    get_running_loop = asyncio.get_running_loop
    run = asyncio.run
    create_task = asyncio.create_task
