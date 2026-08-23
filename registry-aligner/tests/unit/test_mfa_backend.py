from __future__ import annotations

import subprocess
from typing import Any

from registry_align.alignment.mfa.backend import MfaBackend
from registry_align.config import AlignmentConfig, MfaConfig


def test_doctor_accepts_installed_model_when_dictionary_inspector_crashes(
    monkeypatch: Any,
) -> None:
    backend = MfaBackend(
        AlignmentConfig(mfa=MfaConfig(acoustic_model="italian_cv", dictionary="italian_cv"))
    )
    monkeypatch.setattr("registry_align.alignment.mfa.backend.shutil.which", lambda value: value)

    def run(
        command: list[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        if command[1:] == ["version"]:
            return subprocess.CompletedProcess(command, 0, "3.4.2\n", "")
        if command[1:] == ["--help"]:
            return subprocess.CompletedProcess(command, 0, "align validate model", "")
        if command[1:4] == ["model", "inspect", "acoustic"]:
            return subprocess.CompletedProcess(command, 0, "italian acoustic model", "")
        if command[1:4] == ["model", "inspect", "dictionary"]:
            return subprocess.CompletedProcess(command, 1, "", "inspector crashed")
        if command[1:4] == ["model", "list", "dictionary"]:
            return subprocess.CompletedProcess(command, 0, "['italian_cv']\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", run)

    diagnostics = backend.doctor()

    assert diagnostics.usable
    assert diagnostics.issues == ()
