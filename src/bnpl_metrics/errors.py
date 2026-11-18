"""Exceptions shared across layers."""


class InputError(Exception):
    """A source could not provide a value (missing file, no match, unparsable reply...)."""
