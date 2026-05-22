"""Exceptions raised by pysilverline."""

from __future__ import annotations


class SilverlineError(Exception):
    """Base error."""


class CannotConnect(SilverlineError):
    """Network or transport-level failure."""


class InvalidAuth(SilverlineError):
    """The local_key was rejected by the device."""


class ProtocolError(SilverlineError):
    """The frame was malformed or out of spec."""


class IncompleteFrame(SilverlineError):
    """Not malformed — just not all bytes have arrived yet.

    Distinct from ProtocolError so callers can tell the difference
    between "drop the connection, we're desynchronized" (ProtocolError)
    and "wait for the next chunk and try again" (IncompleteFrame).
    TCP is free to split any frame across read boundaries, so this
    is the normal case, not an error condition.
    """
