"""Canonical seconds-to-sample conversion."""

from decimal import ROUND_HALF_UP, Decimal


def seconds_to_sample(seconds: float, sample_rate_hz: int) -> int:
    if seconds < 0:
        raise ValueError("seconds cannot be negative")
    if sample_rate_hz <= 0:
        raise ValueError("sample rate must be positive")
    value = Decimal(str(seconds)) * Decimal(sample_rate_hz)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
