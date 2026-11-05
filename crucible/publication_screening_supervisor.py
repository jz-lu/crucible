"""Opt-in hosted child supervision; durable screening retains all authority.

The ASGI process never runs provider or screening database work. Child failures
restart with bounded backoff. Interrupted attempts retain their durable lease
and reservation for conservative recovery; supervision never enables control,
requeues requests, releases a lease, or interprets a model result.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
import subprocess
import sys
import time

LOG = logging.getLogger(__name__)
WORKER_ENABLED_ENV = 'CRUCIBLE_PUBLICATION_SCREENING_WORKER_ENABLED'
WORKER_COMMAND = (sys.executable, '-B', '-m', 'crucible.publication_screening_worker',
                  'run', '--loop', '--poll-seconds', '1')


def enabled_from_environment():
    value = os.environ.get(WORKER_ENABLED_ENV, 'false')
    if value not in {'true', 'false'}:
        raise RuntimeError('CRUCIBLE_PUBLICATION_SCREENING_WORKER_ENABLED must be exactly true or false.')
    return value == 'true'


class ScreeningSupervisor:
    """One owned process at a time; all timings are independent of HTTP work."""
    def __init__(self, *, command=WORKER_COMMAND, spawn=None, restart_min=1.0,
                 restart_max=30.0, stable_after=60.0, shutdown_grace=5.0, kill_grace=2.0):
        self.command = tuple(command)
        self.spawn = spawn or asyncio.create_subprocess_exec
        self.restart_min, self.restart_max = restart_min, restart_max
        self.stable_after = stable_after
        self.shutdown_grace, self.kill_grace = shutdown_grace, kill_grace
        self._stop = asyncio.Event()
        self._task = None
        self._process = None

    def start(self):
        if self._task is not None:
            raise RuntimeError('Publication screening supervision is already started.')
        self._task = asyncio.create_task(self._run(), name='publication-screening-supervisor')

    async def _terminate(self, process):
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=self.shutdown_grace)
            return
        except TimeoutError:
            pass
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=self.kill_grace)
        except TimeoutError:
            LOG.error('Publication screening supervisor: worker_reap_timeout')

    async def _run(self):
        delay = self.restart_min
        while not self._stop.is_set():
            started = time.monotonic()
            process = None
            waiters = []
            try:
                # No pipes retain child output. Even library/DB startup errors
                # cannot place credentials, request text or exceptions in logs.
                process = await self.spawn(*self.command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True)
                self._process = process
                LOG.warning('Publication screening supervisor: worker_started')
                waiters = [asyncio.create_task(process.wait()), asyncio.create_task(self._stop.wait())]
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                if self._stop.is_set():
                    await self._terminate(process)
                    return
                LOG.warning('Publication screening supervisor: worker_exited')
            except asyncio.CancelledError:
                if process is not None:
                    await self._terminate(process)
                raise
            except Exception:
                # Fixed operational copy only. Exceptions can contain secrets.
                LOG.error('Publication screening supervisor: worker_failed')
                if process is not None:
                    await self._terminate(process)
            finally:
                for waiter in waiters:
                    waiter.cancel()
                if waiters:
                    await asyncio.gather(*waiters, return_exceptions=True)
                self._process = None
            if self._stop.is_set():
                return
            if time.monotonic() - started >= self.stable_after:
                delay = self.restart_min
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(self.restart_max, delay * 2)

    async def _stop_and_wait(self):
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task),
                    timeout=self.shutdown_grace + self.kill_grace + 1)
            except TimeoutError:
                # Also bound shutdown if process creation itself stalls. Known
                # children are killed before cancelling their monitor task.
                process = self._process
                if process is not None and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                self._task.cancel()
                try:
                    await asyncio.wait_for(asyncio.gather(self._task, return_exceptions=True),
                                           timeout=self.kill_grace)
                except TimeoutError:
                    LOG.error('Publication screening supervisor: shutdown_timeout')
        LOG.warning('Publication screening supervisor: worker_stopped')


    async def stop(self):
        # Shield keeps lifespan cancellation from abandoning owned cleanup.
        cleanup = asyncio.create_task(self._stop_and_wait())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise


def install(app, settings, *, enabled, supervisor_factory=ScreeningSupervisor):
    """Attach only to the hosting factory; imports/local factories remain inert."""
    if type(enabled) is not bool:
        raise RuntimeError('Use an explicit publication screening worker switch.')
    active = enabled and settings.publication_screening_mode != 'disabled'
    app.state.publication_screening_supervisor = None
    if not active:
        return app
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application) as state:
            supervisor = supervisor_factory()
            application.state.publication_screening_supervisor = supervisor
            try:
                supervisor.start()
                yield state
            finally:
                try:
                    await supervisor.stop()
                finally:
                    application.state.publication_screening_supervisor = None

    app.router.lifespan_context = lifespan
    return app
