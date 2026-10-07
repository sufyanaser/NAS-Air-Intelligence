"""Sample Sufficiency Engineer: statistical gates for broadcast aggregates.

Prevents small monitoring windows (e.g., 10 minutes) from characterizing
multi-hour dayparts, hourly broadcast clocks, or station program schedules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .validity import SufficiencyStatus

DAYPART_WINDOW_SECONDS = 4.0 * 3600.0  # 14,400s (4 hours)
CLOCK_WINDOW_SECONDS = 3600.0  # 3,600s (1 hour)
MIN_CLOCK_SAMPLE_SECONDS = 7200.0  # At least 2 hours required to observe clock recurrence
MIN_CANDIDATE_SAMPLE_SECONDS = 7200.0  # At least 2 hours required for candidate recurrence


@dataclass
class SufficiencyAssessment:
    sample_seconds: float
    expected_window_seconds: float
    coverage_ratio: float
    sessions_count: int
    days_count: int
    sufficiency_status: SufficiencyStatus
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sample_seconds"] = round(self.sample_seconds, 1)
        data["expected_window_seconds"] = round(self.expected_window_seconds, 1)
        data["coverage_ratio"] = round(self.coverage_ratio, 4)
        data["sufficiency_status"] = self.sufficiency_status.value
        return data


def assess_daypart_sufficiency(
    *,
    sample_seconds: float,
    sessions_count: int = 1,
    days_count: int = 1,
) -> SufficiencyAssessment:
    """Assess whether audio sample duration is sufficient to characterize a 4-hour daypart.

    Thresholds:
    - coverage_ratio >= 0.70: SUFFICIENT
    - coverage_ratio >= 0.25: LIMITED
    - coverage_ratio < 0.25: INSUFFICIENT_SAMPLE
    """
    coverage = (
        min(1.0, sample_seconds / DAYPART_WINDOW_SECONDS) if DAYPART_WINDOW_SECONDS > 0 else 0.0
    )

    if coverage >= 0.70:
        status = SufficiencyStatus.SUFFICIENT
        note = f"Sample covers {coverage:.1%} of the 4h daypart window."
    elif coverage >= 0.25:
        status = SufficiencyStatus.LIMITED
        note = (
            f"Sample covers only {coverage:.1%} of the 4h daypart window; "
            "observations should be treated as provisional."
        )
    else:
        status = SufficiencyStatus.INSUFFICIENT_SAMPLE
        note = (
            f"Sample covers only {coverage:.1%} ({sample_seconds:.0f}s) of the 4h daypart window. "
            "Insufficient to characterize station programming in this daypart."
        )

    return SufficiencyAssessment(
        sample_seconds=sample_seconds,
        expected_window_seconds=DAYPART_WINDOW_SECONDS,
        coverage_ratio=coverage,
        sessions_count=sessions_count,
        days_count=days_count,
        sufficiency_status=status,
        note=note,
    )


def assess_clock_sufficiency(
    *,
    sample_seconds: float,
    sessions_count: int = 1,
    days_count: int = 1,
) -> SufficiencyAssessment:
    """Assess whether sample is sufficient to discover hourly broadcast clock patterns.

    Requires at least 2 hours of captured audio to observe recurrence across hours.
    """
    coverage = (
        min(1.0, sample_seconds / MIN_CLOCK_SAMPLE_SECONDS) if MIN_CLOCK_SAMPLE_SECONDS > 0 else 0.0
    )

    if sample_seconds >= MIN_CLOCK_SAMPLE_SECONDS:
        status = SufficiencyStatus.SUFFICIENT
        note = f"Sample duration ({sample_seconds:.0f}s) spans multiple hours."
    elif sample_seconds >= CLOCK_WINDOW_SECONDS:
        status = SufficiencyStatus.LIMITED
        note = "Sample spans only 1 hour; clock pattern recurrence cannot be verified across hours."
    else:
        status = SufficiencyStatus.INSUFFICIENT_SAMPLE
        note = (
            f"Sample ({sample_seconds:.0f}s) is shorter than an hourly broadcast clock. "
            "Cannot discover clock patterns."
        )

    return SufficiencyAssessment(
        sample_seconds=sample_seconds,
        expected_window_seconds=CLOCK_WINDOW_SECONDS,
        coverage_ratio=coverage,
        sessions_count=sessions_count,
        days_count=days_count,
        sufficiency_status=status,
        note=note,
    )


def assess_candidate_sufficiency(
    *,
    sample_seconds: float,
    occurrences: int,
    hours_spanned: int,
) -> SufficiencyAssessment:
    """Assess sufficiency for program candidate blocks."""
    if occurrences >= 2 and hours_spanned >= 2 and sample_seconds >= MIN_CANDIDATE_SAMPLE_SECONDS:
        status = SufficiencyStatus.SUFFICIENT
        note = f"Candidate recurs across {hours_spanned} hours ({occurrences} occurrences)."
    elif occurrences >= 2:
        status = SufficiencyStatus.LIMITED
        note = "Candidate recurs within a single hour; cross-hour recurrence not proven."
    else:
        status = SufficiencyStatus.INSUFFICIENT_SAMPLE
        note = "Single occurrence; insufficient evidence for program candidate."

    coverage = (
        min(1.0, sample_seconds / MIN_CANDIDATE_SAMPLE_SECONDS)
        if MIN_CANDIDATE_SAMPLE_SECONDS > 0
        else 0.0
    )
    return SufficiencyAssessment(
        sample_seconds=sample_seconds,
        expected_window_seconds=MIN_CANDIDATE_SAMPLE_SECONDS,
        coverage_ratio=coverage,
        sessions_count=1,
        days_count=1,
        sufficiency_status=status,
        note=note,
    )


def assess_cross_day_sufficiency(
    *,
    days_count: int,
    sessions_count: int,
    total_seconds: float,
) -> SufficiencyAssessment:
    """Assess sufficiency for multi-day / schedule-level conclusions."""
    expected_days = 3
    coverage = min(1.0, days_count / expected_days)

    if days_count >= 7:
        status = SufficiencyStatus.SUFFICIENT
        note = f"7-day monitoring completed ({days_count} days, {sessions_count} sessions)."
    elif days_count >= 3:
        status = SufficiencyStatus.LIMITED
        note = f"Multi-day monitoring ({days_count} days); initial schedule profile only."
    else:
        status = SufficiencyStatus.INSUFFICIENT_SAMPLE
        note = (
            f"Only {days_count} day(s) monitored. "
            "Schedule-level conclusions require multi-day data."
        )

    return SufficiencyAssessment(
        sample_seconds=total_seconds,
        expected_window_seconds=expected_days * 86400.0,
        coverage_ratio=coverage,
        sessions_count=sessions_count,
        days_count=days_count,
        sufficiency_status=status,
        note=note,
    )
