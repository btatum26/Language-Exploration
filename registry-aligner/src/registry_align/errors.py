"""Typed errors and their CLI exit-code contract."""

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    PROCESSING_FAILURE = 1
    INPUT_FAILURE = 2
    DEPENDENCY_FAILURE = 3
    PARTIAL_SUCCESS = 4
    CANCELLED = 130


class RegistryAlignError(Exception):
    """Base class for errors safe to present without a traceback."""

    exit_code = ExitCode.PROCESSING_FAILURE


class ConfigurationError(RegistryAlignError):
    exit_code = ExitCode.INPUT_FAILURE


class RegistryReadError(RegistryAlignError):
    exit_code = ExitCode.INPUT_FAILURE


class InitializationError(RegistryAlignError):
    exit_code = ExitCode.INPUT_FAILURE


class DependencyError(RegistryAlignError):
    exit_code = ExitCode.DEPENDENCY_FAILURE


class ProcessingError(RegistryAlignError):
    exit_code = ExitCode.PROCESSING_FAILURE
