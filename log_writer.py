"""One bounded async writer shared by UDP, TCP and HTTP intake."""
import asyncio
import database


class LogWriter:
    def __init__(self):
        self.task = None
        self.queue = None
        self.accepting = False

    async def start(self):
        if self.task is not None:
            raise RuntimeError('Log writer is already running')
        self.queue = asyncio.Queue(maxsize=1000)
        self.accepting = True
        self.task = asyncio.create_task(self.run())

    async def submit(self, **entry):
        if not self.accepting:
            return await asyncio.to_thread(database.insert_log, **entry)
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((entry, future))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Accepted writes still commit when a TCP/HTTP client disconnects.
            future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            raise

    async def run(self):
        while True:
            first = await self.queue.get()
            if first is None:
                self.queue.task_done()
                return
            batch = [first]
            deadline = asyncio.get_running_loop().time() + 0.025
            while len(batch) < 100:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self.queue.get(), remaining))
                except asyncio.TimeoutError:
                    break
            try:
                results = await asyncio.to_thread(database.insert_logs, [entry for entry, _ in batch])
            except Exception as exc:
                results = [exc] * len(batch)
            for (_, future), result in zip(batch, results):
                if not future.done():
                    if isinstance(result, Exception):
                        future.set_exception(result)
                    else:
                        future.set_result(result)
                self.queue.task_done()

    async def stop(self):
        self.accepting = False
        if self.task:
            await self.queue.join()
            await self.queue.put(None)
            await self.task
            self.task = None


writer = LogWriter()
