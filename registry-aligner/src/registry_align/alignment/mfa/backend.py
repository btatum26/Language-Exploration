"""Version-aware MFA subprocess backend."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from registry_align.alignment.mfa.commands import (
    build_align_command,
    build_validation_command,
    select_mode,
)
from registry_align.alignment.mfa.parser import parse_results
from registry_align.alignment.mfa.staging import stage_jobs
from registry_align.alignment.models import AlignmentJob, BackendDiagnostics, BackendResult
from registry_align.config import AlignmentConfig
from registry_align.errors import DependencyError, ProcessingError
from registry_align.events import Issue, Severity


class MfaBackend:
    name = "mfa"

    def __init__(self, config: AlignmentConfig) -> None:
        self.config = config
        self._diagnostics: BackendDiagnostics | None = None
        self._resource_fingerprints: dict[str, str] = {}

    def _run(
        self, command: list[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=timeout
            )
        except (FileNotFoundError, OSError) as exc:
            raise DependencyError(
                f"MFA executable is unavailable: {self.config.mfa.executable}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProcessingError(f"MFA command timed out: {command[1]}") from exc

    @staticmethod
    def _installed_model_file(resource_type: str, resource: str) -> Path | None:
        root_value = os.getenv("MFA_ROOT_DIR")
        if not root_value:
            return None
        directory = Path(root_value) / "pretrained_models" / resource_type
        try:
            candidates = sorted(
                path
                for path in directory.iterdir()
                if path.is_file() and (path.name == resource or path.stem == resource)
            )
        except OSError:
            return None
        return candidates[0] if len(candidates) == 1 else None

    def doctor(self) -> BackendDiagnostics:
        if self._diagnostics is not None:
            return self._diagnostics
        executable = self.config.mfa.executable
        if shutil.which(executable) is None and not Path(executable).is_file():
            self._diagnostics = BackendDiagnostics(
                usable=False,
                name=self.name,
                issues=(
                    Issue(
                        code="MFA_NOT_FOUND",
                        severity=Severity.ERROR,
                        stage="doctor",
                        message=f"MFA executable is unavailable: {executable}",
                        hint="Install Montreal Forced Aligner, then set alignment.mfa.executable.",
                    ),
                ),
                remediation_commands=("conda install -c conda-forge montreal-forced-aligner",),
            )
            return self._diagnostics
        version_result = self._run([executable, "version"], timeout=30)
        help_result = self._run([executable, "--help"], timeout=30)
        version = (version_result.stdout or version_result.stderr).strip().splitlines()
        help_text = f"{help_result.stdout}\n{help_result.stderr}"
        capabilities = tuple(
            name
            for name in ("align", "align_legacy", "align_hf", "validate", "model")
            if name in help_text
        )
        issues: list[Issue] = []
        remediation: list[str] = []
        if self.config.mfa.config_path and not Path(self.config.mfa.config_path).is_file():
            issues.append(
                Issue(
                    code="MFA_CONFIG_MISSING",
                    severity=Severity.ERROR,
                    stage="doctor",
                    message=f"MFA configuration file is unavailable: {self.config.mfa.config_path}",
                )
            )
        if version_result.returncode != 0:
            issues.append(
                Issue(
                    code="MFA_VERSION_FAILED",
                    severity=Severity.ERROR,
                    stage="doctor",
                    message="MFA version command failed",
                    details={"stderr": version_result.stderr.strip()},
                )
            )
        mode = select_mode(self.config.mfa, set(capabilities))
        resources = [("acoustic", self.config.mfa.acoustic_model)]
        if mode == "legacy":
            resources.append(("dictionary", self.config.mfa.dictionary))
        for resource_type, resource in resources:
            if not resource:
                issues.append(
                    Issue(
                        code="MFA_MODEL_UNCONFIGURED",
                        severity=Severity.ERROR,
                        stage="doctor",
                        message=f"MFA {resource_type} model is not configured",
                    )
                )
                continue
            resource_path = Path(resource)
            if resource_path.is_file():
                self._resource_fingerprints[resource_type] = hashlib.sha256(
                    resource_path.read_bytes()
                ).hexdigest()
                continue
            if mode == "hosted":
                if not Path(resource).exists():
                    issues.append(
                        Issue(
                            code="MFA_HOSTED_MODEL_NOT_LOCAL",
                            severity=Severity.ERROR,
                            stage="doctor",
                            message=f"hosted MFA model must already exist locally: {resource}",
                            hint=(
                                "Download the model explicitly before processing and configure "
                                "its local path."
                            ),
                        )
                    )
                else:
                    model_path = Path(resource)
                    metadata = [
                        (
                            str(path.relative_to(model_path)),
                            path.stat().st_size,
                            path.stat().st_mtime_ns,
                        )
                        for path in sorted(model_path.rglob("*"))
                        if path.is_file()
                    ]
                    self._resource_fingerprints[resource_type] = hashlib.sha256(
                        json.dumps(metadata, sort_keys=True).encode()
                    ).hexdigest()
                continue
            inspected = self._run(
                [executable, "model", "inspect", resource_type, resource], timeout=60
            )
            if inspected.returncode != 0:
                listed = self._run([executable, "model", "list", resource_type], timeout=60)
                try:
                    installed = ast.literal_eval(listed.stdout.strip())
                except (SyntaxError, ValueError):
                    installed = ()
                if listed.returncode != 0 or resource not in installed:
                    issues.append(
                        Issue(
                            code="MFA_MODEL_MISSING",
                            severity=Severity.ERROR,
                            stage="doctor",
                            message=f"MFA {resource_type} model is unavailable: {resource}",
                            hint=f"Run: mfa model download {resource_type} {resource}",
                        )
                    )
                    remediation.append(f"mfa model download {resource_type} {resource}")
                    continue
                fingerprint_source = f"{listed.stdout}\n{listed.stderr}"
            else:
                fingerprint_source = f"{inspected.stdout}\n{inspected.stderr}"
            installed_model = self._installed_model_file(resource_type, resource)
            fingerprint_bytes = (
                installed_model.read_bytes()
                if installed_model is not None
                else fingerprint_source.encode()
            )
            self._resource_fingerprints[resource_type] = hashlib.sha256(
                fingerprint_bytes
            ).hexdigest()
        self._diagnostics = BackendDiagnostics(
            usable=not any(issue.severity == Severity.ERROR for issue in issues),
            name=self.name,
            version=version[0] if version else None,
            capabilities=capabilities,
            issues=tuple(issues),
            remediation_commands=tuple(remediation),
        )
        return self._diagnostics

    def fingerprint(self) -> str:
        diagnostics = self.doctor()
        config_path = Path(self.config.mfa.config_path) if self.config.mfa.config_path else None
        payload = {
            "name": self.name,
            "version": diagnostics.version,
            "capabilities": diagnostics.capabilities,
            "model_mode": self.config.mfa.model_mode,
            "config_path": self.config.mfa.config_path,
            "config_sha256": (
                hashlib.sha256(config_path.read_bytes()).hexdigest()
                if config_path is not None and config_path.is_file()
                else None
            ),
            "acoustic_model": self.config.mfa.acoustic_model,
            "dictionary": self.config.mfa.dictionary,
            "g2p_model": self.config.mfa.g2p_model,
            "use_g2p": self.config.mfa.use_g2p_for_oov,
            "fine_tune": self.config.mfa.fine_tune,
            "resource_fingerprints": self._resource_fingerprints,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def align(self, jobs: tuple[AlignmentJob, ...], workspace: Path, run_id: str) -> BackendResult:
        diagnostics = self.doctor()
        if not diagnostics.usable:
            raise DependencyError("MFA or its configured models are unavailable; run doctor")
        staging = workspace / "staging" / "mfa" / run_id
        corpus = staging / "corpus"
        output = workspace / "backend" / "mfa" / run_id
        validation_output = workspace / "backend" / "mfa" / f"{run_id}-validation"
        temporary = staging / "temporary"
        for directory in (output, validation_output, temporary):
            directory.mkdir(parents=True, exist_ok=True)
        manifest = stage_jobs(jobs, corpus)
        capabilities = set(diagnostics.capabilities)
        validation_command = build_validation_command(
            self.config.mfa.executable,
            corpus,
            validation_output,
            temporary,
            self.config,
        )
        captured_stdout: list[str] = []
        captured_stderr: list[str] = []
        issues: list[Issue] = []
        if validation_command is not None:
            validation = self._run(validation_command)
            captured_stdout.append(validation.stdout)
            captured_stderr.append(validation.stderr)
            if validation.returncode != 0:
                raise ProcessingError(f"MFA corpus validation failed: {validation.stderr.strip()}")
            oov_files = sorted(
                path
                for path in validation_output.rglob("*")
                if path.is_file() and path.name.casefold().startswith("oovs_found")
            )
            oov_lines: set[str] = set()
            for path in oov_files:
                try:
                    oov_lines.update(
                        line.strip()
                        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                        if line.strip()
                    )
                except OSError:
                    continue
            if oov_lines:
                issues.append(
                    Issue(
                        code="MFA_OOV_ITEMS",
                        severity=Severity.WARNING,
                        stage="backend-validation",
                        message=f"MFA reported {len(oov_lines)} unique OOV word(s)",
                        hint=(
                            "Review validation artifacts and configure G2P explicitly if needed."
                        ),
                        details={"examples": sorted(oov_lines)[:20]},
                    )
                )
        command = build_align_command(
            self.config.mfa.executable,
            corpus,
            output,
            temporary,
            self.config,
            capabilities,
        )
        aligned = self._run(command)
        captured_stdout.append(aligned.stdout)
        captured_stderr.append(aligned.stderr)
        if aligned.returncode != 0:
            raise ProcessingError(f"MFA alignment failed: {aligned.stderr.strip()}")
        segments = parse_results(
            output,
            manifest,
            jobs,
            run_id,
            self.config.mfa.acoustic_model,
        )
        return BackendResult(
            segments=segments,
            issues=tuple(issues),
            raw_output_directory=output,
            command=tuple(command),
            stdout="\n".join(captured_stdout),
            stderr="\n".join(captured_stderr),
        )
