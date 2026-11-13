from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch


spec = importlib.util.spec_from_file_location(
    "container_start", Path(__file__).resolve().parents[1] / "chat-simulator" / "backend" / "container_start.py",
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ContainerStartTests(unittest.TestCase):
    def settings_path(self, environment):
        path = Mock(spec=Path)
        path.lstat.return_value = SimpleNamespace(st_uid=42, st_mode=0o100600)
        path.is_symlink.return_value = False
        path.read_text.return_value = json.dumps({"environment": environment})
        return path

    def test_settings_reject_wrong_owner_permissions_symlinks_and_invalid_environment(self):
        path = self.settings_path({"TOKEN": "private-runtime-value"})
        self.assertEqual(launcher.load_settings(path, 42), {"TOKEN": "private-runtime-value"})
        for owner, mode, symlink in ((41, 0o100600, False), (42, 0o100644, False), (42, 0o100600, True)):
            path.lstat.return_value = SimpleNamespace(st_uid=owner, st_mode=mode)
            path.is_symlink.return_value = symlink
            with self.subTest(owner=owner, mode=mode, symlink=symlink), self.assertRaises(ValueError):
                launcher.load_settings(path, 42)
        for environment in (None, [], {"BAD=KEY": "x"}, {"KEY": 1}, {"KEY": "bad\0value"}):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                launcher.load_settings(self.settings_path(environment), 42)

    def test_config_change_restarts_child_and_shutdown_terminates_replacement(self):
        first, second = Mock(), Mock()
        first.poll.return_value = second.poll.return_value = None
        handlers = {}
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        with (
            patch.object(launcher.os, "geteuid", return_value=42, create=True),
            patch.object(launcher.os, "open", return_value=17),
            patch.object(launcher.os, "fdopen", mock_open()),
            patch.object(launcher.signal, "signal", side_effect=lambda number, handler: handlers.update({number: handler})),
            patch.object(launcher, "load_settings", side_effect=[{"VERSION": "1"}, {"VERSION": "2"}]),
            patch.object(launcher.subprocess, "Popen", side_effect=[first, second]) as popen,
            patch.object(launcher.time, "sleep", side_effect=sleep),
        ):
            launcher.supervise(Mock(exists=Mock(return_value=True)), ["fixed-application"], "gateway")
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(popen.call_args_list[1].kwargs["env"]["VERSION"], "2")
        first.terminate.assert_called_once()
        second.terminate.assert_called_once()

    def test_child_failure_is_not_reported_as_ready_or_restarted_silently(self):
        child = Mock(returncode=7)
        child.poll.return_value = 7
        with (
            patch.object(launcher.os, "geteuid", return_value=42, create=True),
            patch.object(launcher.os, "open", return_value=17),
            patch.object(launcher.os, "fdopen", mock_open()),
            patch.object(launcher.signal, "signal"),
            patch.object(launcher, "load_settings", return_value={}),
            patch.object(launcher.subprocess, "Popen", return_value=child) as popen,
            self.assertRaisesRegex(RuntimeError, "status 7"),
        ):
            launcher.supervise(Mock(exists=Mock(return_value=True)), ["fixed-application"], "gateway")
        popen.assert_called_once()

    def test_simulator_mount_is_initialized_before_dropping_root(self):
        directory = Mock(spec=Path)
        directory.lstat.return_value = SimpleNamespace(st_uid=0)
        directory.is_symlink.return_value = False
        directory.is_dir.return_value = True
        with (
            patch.object(launcher, "Path", return_value=directory),
            patch.object(launcher.os, "geteuid", side_effect=[0, 10001], create=True),
            patch.object(launcher.os, "chown", create=True) as chown,
            patch.object(launcher.os, "chmod") as chmod,
            patch.object(launcher.os, "setgroups", create=True) as groups,
            patch.object(launcher.os, "setgid", create=True) as gid,
            patch.object(launcher.os, "setuid", create=True) as uid,
        ):
            launcher.prepare_simulator_storage()
        chown.assert_called_once_with(directory, 10001, 10001)
        chmod.assert_called_once_with(directory, 0o700)
        groups.assert_called_once_with([])
        gid.assert_called_once_with(10001)
        uid.assert_called_once_with(10001)
