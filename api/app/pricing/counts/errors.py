"""Typed errors, so a caller can tell a bad ladder from a bug."""


class LadderError(ValueError):
    """The ladder cannot be priced at all — too few rungs, or malformed odds.

    Raised rather than returned because there is no price to give. Conditions
    that merely make a price LESS trustworthy (non-monotone survival, an
    extreme overround, an exactly-identified fit) are flags on the result, not
    errors: suppressing those would hide exactly the information the caller
    needs to decide how much to trust the number.
    """
