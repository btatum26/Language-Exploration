"""Public application API for registry-aligner."""

from registry_align.domain.runs import ProcessRequest, ProcessResult
from registry_align.pipeline.service import AlignmentService

__all__ = ["AlignmentService", "ProcessRequest", "ProcessResult"]
__version__ = "0.1.0"
