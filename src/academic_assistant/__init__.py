"""Evidence-grounded academic answer engine."""

from .core import AnswerEngine, answer
from .models import AcademicAnswerRequest, AcademicAnswerResponse
from .registry import Registry, RegistryUnavailable

__all__ = ["AcademicAnswerRequest", "AcademicAnswerResponse", "AnswerEngine", "Registry", "RegistryUnavailable", "answer"]

