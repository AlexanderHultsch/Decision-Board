"""Fakes for ``subprocess`` shared by the tests: ``opencode run`` is started
with ``Popen`` (so a session can stop it), everything else with ``run``.
One fake function serves both."""

from __future__ import annotations

import contextlib
import subprocess
from unittest import mock


def _popen_from(fake):
    class FakePopen:
        def __init__(self, command, **kwargs):
            self.command, self.kwargs, self.returncode = command, kwargs, None

        def communicate(self, input=None, timeout=None):
            result = fake(self.command, input=input, env=self.kwargs.get("env"), timeout=timeout)
            self.returncode = result.returncode
            return result.stdout, result.stderr

        def kill(self):
            pass
    return FakePopen


@contextlib.contextmanager
def patch_subprocess(fake=None, *, side_effect=None):
    if side_effect is not None:
        run, popen = mock.Mock(side_effect=side_effect), mock.Mock(side_effect=side_effect)
    else:
        run, popen = fake, _popen_from(fake)
    with mock.patch.object(subprocess, "run", run), mock.patch.object(subprocess, "Popen", popen):
        yield run
