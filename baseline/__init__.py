"""Qt-free baseline domain layer.

Multi-project baseline management: an append-only version store per project,
schema validation, versioning, and B->C governance checks. Kept free of Qt and
of any cloud client so the same logic can back a future HTTP repository
(Phase B) and so it is unit-testable in isolation.
"""

from baseline.errors import BaselineError, GovernanceError, ValidationError
from baseline.store import BaselineRepository, ProjectInfo

__all__ = [
    "BaselineError",
    "GovernanceError",
    "ValidationError",
    "BaselineRepository",
    "ProjectInfo",
]
