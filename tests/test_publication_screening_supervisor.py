"""Hosting supervision tests: synthetic child processes only, never a provider."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from crucible import publication_screening_supervisor as hosting


def test_opt_in_switch_is_strict_and_defaults_disabled(monkeypatch):
    monkeypatch.delenv(hosting.WORKER_ENABLED_ENV, raising=False)
    assert hosting.enabled_from_environment() is False
    for value, expected in [('false', False), ('true', True)]:
        monkeypatch.setenv(hosting.WORKER_ENABLED_ENV, value)
        assert hosting.enabled_from_environment() is expected
    for value in ('TRUE', '1', '', 'arbitrary-secret-value'):
        monkeypatch.setenv(hosting.WORKER_ENABLED_ENV, value)
        with pytest.raises(RuntimeError) as error:
            hosting.enabled_from_environment()
        assert value not in str(error.value) if value else True


@pytest.mark.parametrize('enabled,mode', [(False, 'automatic'), (True, 'disabled'), (False, 'disabled')])
def test_disabled_lifespan_never_constructs_or_starts_worker(enabled, mode):
    app = FastAPI()
    original = app.router.lifespan_context
    def forbidden():
        pytest.fail('Disabled supervision must not construct a worker')
    assert hosting.install(app, SimpleNamespace(publication_screening_mode=mode),
        enabled=enabled, supervisor_factory=forbidden) is app
    assert app.router.lifespan_context is original
    assert app.state.publication_screening_supervisor is None


@pytest.mark.parametrize('body_failure', [False, True])
def test_lifespan_preserves_startup_state_and_stops_before_existing_teardown(body_failure):
    events = []
    class Worker:
        def start(self): events.append('worker_start')
        async def stop(self): events.append('worker_stop')
    @asynccontextmanager
    async def previous(app):
        events.append('prior_start')
        try:
            yield {'preserved': 'state'}
        finally:
            events.append('prior_stop')
    app = FastAPI(lifespan=previous)
    hosting.install(app, SimpleNamespace(publication_screening_mode='automatic'),
        enabled=True, supervisor_factory=Worker)
    async def scenario():
        try:
            async with app.router.lifespan_context(app) as state:
                assert state == {'preserved': 'state'}
                assert isinstance(app.state.publication_screening_supervisor, Worker)
                events.append('serving')
                if body_failure:
                    raise ValueError('synthetic app failure')
        except ValueError:
            assert body_failure
    asyncio.run(scenario())
    assert events == ['prior_start', 'worker_start', 'serving', 'worker_stop', 'prior_stop']
    assert app.state.publication_screening_supervisor is None


def test_prior_startup_failure_does_not_spawn_worker():
    @asynccontextmanager
    async def previous(app):
        raise RuntimeError('synthetic startup failure')
        yield
    app = FastAPI(lifespan=previous)
    def forbidden(): pytest.fail('Prior startup did not finish')
    hosting.install(app, SimpleNamespace(publication_screening_mode='automatic'),
        enabled=True, supervisor_factory=forbidden)
    async def scenario():
        with pytest.raises(RuntimeError, match='synthetic startup failure'):
            async with app.router.lifespan_context(app):
                pytest.fail('Failed startup served requests')
    asyncio.run(scenario())


class Child:
    def __init__(self, *, returncode=None, ignore_term=False):
        self.returncode = returncode
        self.ignore_term = ignore_term
        self.done = asyncio.Event()
        self.terminated = self.killed = 0
        if returncode is not None:
            self.done.set()
    async def wait(self):
        await self.done.wait()
        return self.returncode
    def terminate(self):
        self.terminated += 1
        if not self.ignore_term:
            self.returncode = -15
            self.done.set()
    def kill(self):
        self.killed += 1
        self.returncode = -9
        self.done.set()


def test_child_failures_restart_with_backoff_and_raw_output_is_never_inherited(caplog):
    async def scenario():
        calls, options, children = [], [], []
        four = asyncio.Event()
        async def spawn(*command, **kwargs):
            calls.append(time.monotonic())
            options.append((command, kwargs))
            if len(calls) == 1:
                raise OSError('SENSITIVE_DATABASE_URL')
            child = Child(returncode=1 if len(calls) < 4 else None)
            children.append(child)
            if len(calls) == 4:
                four.set()
            return child
        supervisor = hosting.ScreeningSupervisor(spawn=spawn, restart_min=.02, restart_max=.04)
        supervisor.start()
        await asyncio.wait_for(four.wait(), timeout=2)
        await supervisor.stop()
        assert len(calls) == 4
        assert calls[1] - calls[0] >= .015
        assert calls[2] - calls[1] >= .035
        assert calls[3] - calls[2] >= .035
        assert all(command == hosting.WORKER_COMMAND for command, _ in options)
        assert all(kwargs['stdout'] == kwargs['stderr'] == kwargs['stdin'] == subprocess.DEVNULL
                   and kwargs['start_new_session'] is True and kwargs['close_fds'] is True
                   for _, kwargs in options)
        assert children[-1].terminated == 1 and children[-1].killed == 0
    asyncio.run(scenario())
    assert 'SENSITIVE_DATABASE_URL' not in caplog.text
    assert 'worker_failed' in caplog.text and 'worker_exited' in caplog.text


def test_shutdown_interrupts_backoff_without_starting_another_child():
    async def scenario():
        started = asyncio.Event()
        calls = []
        async def spawn(*args, **kwargs):
            calls.append(True)
            started.set()
            return Child(returncode=1)
        supervisor = hosting.ScreeningSupervisor(spawn=spawn, restart_min=30, restart_max=30)
        supervisor.start()
        await started.wait()
        await asyncio.sleep(0)
        await asyncio.wait_for(supervisor.stop(), timeout=.2)
        assert len(calls) == 1
    asyncio.run(scenario())


def test_shutdown_kills_unresponsive_child_and_keeps_asgi_event_loop_responsive():
    async def scenario():
        started = asyncio.Event()
        child = Child(ignore_term=True)
        async def spawn(*args, **kwargs):
            started.set()
            return child
        supervisor = hosting.ScreeningSupervisor(spawn=spawn, shutdown_grace=.04, kill_grace=.04)
        supervisor.start()
        await started.wait()
        stop = asyncio.create_task(supervisor.stop())
        # Event-loop work proceeds while a worker is blocked during shutdown.
        await asyncio.sleep(.01)
        assert not stop.done()
        await asyncio.wait_for(stop, timeout=.3)
        assert child.terminated == child.killed == 1 and child.returncode == -9
    asyncio.run(scenario())


def test_cancelled_lifespan_still_stops_owned_child():
    async def scenario():
        started = asyncio.Event()
        child = Child(ignore_term=True)
        async def spawn(*args, **kwargs):
            started.set()
            return child
        supervisor = hosting.ScreeningSupervisor(spawn=spawn, shutdown_grace=.02, kill_grace=.02)
        supervisor.start()
        await started.wait()
        stopping = asyncio.create_task(supervisor.stop())
        await asyncio.sleep(0)
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert child.returncode == -9
    asyncio.run(scenario())


def test_real_synthetic_child_is_reaped_without_forwarding_output(tmp_path, capfd):
    marker = tmp_path / 'started'
    script = ('import pathlib,signal,time; '
              'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
              'print("PRIVATE_CHILD_STDOUT", flush=True); '
              'print("PRIVATE_CHILD_STDERR", file=__import__("sys").stderr, flush=True); '
              f'pathlib.Path({str(marker)!r}).write_text("ready"); time.sleep(30)')
    async def scenario():
        supervisor = hosting.ScreeningSupervisor(command=(sys.executable, '-c', script),
            shutdown_grace=.05, kill_grace=.2)
        supervisor.start()
        async with asyncio.timeout(3):
            while not marker.exists():
                await asyncio.sleep(.01)
        process = supervisor._process
        assert process is not None and process.returncode is None
        await asyncio.wait_for(supervisor.stop(), timeout=1)
        assert process.returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    asyncio.run(scenario())
    captured = capfd.readouterr()
    assert 'PRIVATE_CHILD_STDOUT' not in captured.out
    assert 'PRIVATE_CHILD_STDERR' not in captured.err


def test_shutdown_cancels_stalled_process_creation():
    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def spawn(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        supervisor = hosting.ScreeningSupervisor(spawn=spawn, shutdown_grace=.01, kill_grace=.01)
        supervisor.start()
        await started.wait()
        await asyncio.wait_for(supervisor.stop(), timeout=1.5)
        assert cancelled.is_set() and supervisor._task.done()
    asyncio.run(scenario())


def test_hosted_factory_wires_opt_in_only_at_lifespan_and_keeps_ingress(monkeypatch):
    from crucible import app as core, app_platform, config
    events = []
    settings = SimpleNamespace(production=True, access_mode='beta', origins=('https://crucible.fail',),
                               publication_screening_mode='automatic')
    monkeypatch.setenv('CRUCIBLE_ENV', 'production')
    monkeypatch.setenv('CRUCIBLE_ACCESS_MODE', 'beta')
    monkeypatch.setenv(hosting.WORKER_ENABLED_ENV, 'true')
    monkeypatch.setattr(config, 'load_settings', lambda: settings)
    inner = FastAPI()
    monkeypatch.setattr(core, 'create_app', lambda value: inner if value is settings else None)
    monkeypatch.setattr(hosting.ScreeningSupervisor, 'start', lambda self: events.append('started'))
    async def stopped(self): events.append('stopped')
    monkeypatch.setattr(hosting.ScreeningSupervisor, 'stop', stopped)
    result = app_platform.create_app()
    assert isinstance(result, app_platform.AppPlatformIngress) and result.app is inner
    assert events == []  # Import/factory construction does not dispatch work.
    async def scenario():
        async with inner.router.lifespan_context(inner):
            assert events == ['started']
    asyncio.run(scenario())
    assert events == ['started', 'stopped']


def test_bad_hosted_supervisor_switch_fails_before_app_or_database_creation(monkeypatch):
    from crucible import app as core, app_platform, config
    monkeypatch.setenv('CRUCIBLE_ENV', 'production')
    monkeypatch.setenv('CRUCIBLE_ACCESS_MODE', 'beta')
    monkeypatch.setenv(hosting.WORKER_ENABLED_ENV, 'invalid')
    def forbidden(*args): pytest.fail('Invalid switch reached configuration/application creation')
    monkeypatch.setattr(config, 'load_settings', forbidden)
    monkeypatch.setattr(core, 'create_app', forbidden)
    with pytest.raises(RuntimeError, match='must be exactly true or false'):
        app_platform.create_app()
