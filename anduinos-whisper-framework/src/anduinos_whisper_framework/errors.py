"""Failures shared by the resident transport, service and developer benchmarks."""


class RecognitionError(RuntimeError):
    pass


class RecognitionCancelled(Exception):
    """An obsolete preview or cancelled session is not a user-facing failure."""


class ResidentUnavailable(RecognitionError):
    """A required native component is missing or incompatible."""


class RecognitionTimeout(RecognitionError):
    """A bounded inference deadline expired."""
