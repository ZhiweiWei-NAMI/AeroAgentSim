"""Typed failures at the public, read-only run boundary."""


class RunReadError(Exception):
    """A recorded run could not be read under the requested contract."""


class UnsupportedRunFormat(RunReadError):
    """The source format, codec or provenance policy is not admitted."""


class NoCommittedPrefix(RunReadError):
    """A trustworthy acknowledged byte/index coordinate is unavailable."""


class CorruptRecord(RunReadError):
    """Acknowledged evidence cannot be decoded or has invalid coordinates."""


class RunReplaced(RunReadError):
    """The retained source was replaced, truncated or changed after capture."""
