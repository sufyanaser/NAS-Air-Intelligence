"""Analytical Validity Architecture for NAS Air Intelligence.

Separates capture success from analysis validity.
Provides the component status model, decision readiness evaluation,
and strict evidence classification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class CaptureStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    CAPTURING = "CAPTURING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class ProcessingStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class TranscriptionStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED = "SKIPPED"


class ClassificationStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    COMPLETED = "COMPLETED"
    LIMITED = "LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class ProgrammingAnalysisStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    COMPLETED = "COMPLETED"
    LIMITED = "LIMITED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class ReportStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExportStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class DecisionReadiness(StrEnum):
    READY = "READY"
    LIMITED = "LIMITED"
    NOT_READY = "NOT_READY"


class EvidenceClass(StrEnum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    NAS_FM_PLANNING_INPUT = "NAS_FM_PLANNING_INPUT"


class SufficiencyStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    LIMITED = "LIMITED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


@dataclass
class EvidenceReference:
    ref_type: str  # "audio" | "chunk" | "event" | "block"
    ref_id: str
    details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalyticalStatement:
    classification: EvidenceClass
    statement: str
    confidence: float
    confidence_basis: str
    evidence_refs: list[str]
    limitations: list[str] = field(default_factory=list)
    nas_fm_planning_input: str | None = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "statement": self.statement,
            "confidence": round(self.confidence, 3),
            "confidence_basis": self.confidence_basis,
            "evidence_refs": self.evidence_refs,
            "limitations": self.limitations,
            "nas_fm_planning_input": self.nas_fm_planning_input,
        }


@dataclass
class AnalyticalValidity:
    capture_status: CaptureStatus
    processing_status: ProcessingStatus
    transcription_status: TranscriptionStatus
    classification_status: ClassificationStatus
    programming_analysis_status: ProgrammingAnalysisStatus
    report_status: ReportStatus
    export_status: ExportStatus
    decision_readiness: DecisionReadiness
    reasons: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_status": self.capture_status.value,
            "processing_status": self.processing_status.value,
            "transcription_status": self.transcription_status.value,
            "classification_status": self.classification_status.value,
            "programming_analysis_status": self.programming_analysis_status.value,
            "report_status": self.report_status.value,
            "export_status": self.export_status.value,
            "decision_readiness": self.decision_readiness.value,
            "reasons": self.reasons,
            "limitations": self.limitations,
        }


def evaluate_analytical_validity(
    *,
    has_chunks: bool,
    captured_seconds: float,
    expected_seconds: float,
    chunk_count: int,
    unanalyzed_chunks: int,
    expects_transcription: bool,
    transcription_failures: int,
    speech_events_count: int,
    mean_speech_confidence: float | None,
    timeline_coverage_percent: float,
    critical_incidents_count: int,
    stopped_early: bool = False,
    is_dependency_unavailable: bool = False,
    is_insufficient_sample: bool = False,
) -> AnalyticalValidity:
    """Evaluate overall analytical validity across all pipeline stages.

    Guarantees:
    - A successfully captured session NEVER implies successful analysis.
    - If transcription is required and failed, transcription=FAILED,
      classification=UNAVAILABLE, programming_analysis=BLOCKED, decision_readiness=NOT_READY.
    """
    reasons: list[str] = []
    limitations: list[str] = []

    # 1. Capture Status
    ratio = (captured_seconds / expected_seconds) if expected_seconds > 0 else 0.0
    if not has_chunks or chunk_count == 0 or captured_seconds <= 0:
        capture_status = CaptureStatus.FAILED
        reasons.append("Capture failed: no audio chunks were recorded.")
    elif stopped_early:
        capture_status = CaptureStatus.STOPPED
        limitations.append(f"Monitoring stopped early after {captured_seconds:.0f}s.")
    elif ratio < 0.5:
        capture_status = CaptureStatus.FAILED
        reasons.append(f"Capture incomplete: only {ratio:.1%} of expected audio captured.")
    else:
        capture_status = CaptureStatus.COMPLETED

    # 2. Processing Status
    if capture_status == CaptureStatus.FAILED:
        processing_status = ProcessingStatus.FAILED
    elif unanalyzed_chunks == 0 and chunk_count > 0:
        processing_status = ProcessingStatus.COMPLETED
    elif unanalyzed_chunks > 0:
        processing_status = ProcessingStatus.DEGRADED
        limitations.append(f"{unanalyzed_chunks} of {chunk_count} chunks were never analyzed.")
    else:
        processing_status = ProcessingStatus.NOT_STARTED

    # 3. Transcription Status
    if not expects_transcription:
        transcription_status = TranscriptionStatus.SKIPPED
        limitations.append("Speech transcription was not requested for this session.")
    elif is_dependency_unavailable:
        transcription_status = TranscriptionStatus.UNAVAILABLE
        reasons.append("Transcription engine or model dependency is unavailable.")
    elif chunk_count > 0 and transcription_failures >= max(1, chunk_count):
        transcription_status = TranscriptionStatus.FAILED
        reasons.append(
            f"Transcription failed completely ({transcription_failures}/{chunk_count} chunks)."
        )
    elif chunk_count > 0 and transcription_failures >= max(1, chunk_count // 2):
        transcription_status = TranscriptionStatus.FAILED
        reasons.append(
            f"Transcription failed on majority of chunks ({transcription_failures}/{chunk_count})."
        )
    elif transcription_failures > 0:
        transcription_status = TranscriptionStatus.DEGRADED
        limitations.append(
            f"Transcription failed on {transcription_failures} of {chunk_count} chunks."
        )
    elif speech_events_count == 0 and chunk_count > 0:
        # Chunks were analyzed without explicit exception, but no speech was transcribed
        transcription_status = TranscriptionStatus.COMPLETED
        limitations.append("No speech segments detected in captured audio.")
    else:
        transcription_status = TranscriptionStatus.COMPLETED

    # 4. Classification Status
    if transcription_status in (TranscriptionStatus.FAILED, TranscriptionStatus.UNAVAILABLE):
        classification_status = ClassificationStatus.UNAVAILABLE
        reasons.append("Classification is unavailable because transcription dependency failed.")
    elif transcription_status == TranscriptionStatus.SKIPPED:
        classification_status = ClassificationStatus.LIMITED
        limitations.append("Classification limited to baseline silence detection.")
    elif transcription_status == TranscriptionStatus.DEGRADED:
        classification_status = ClassificationStatus.LIMITED
        limitations.append("Classification is limited due to partial transcription failures.")
    elif mean_speech_confidence is not None and mean_speech_confidence < 0.3:
        classification_status = ClassificationStatus.LIMITED
        limitations.append(f"Classification confidence is low (mean {mean_speech_confidence:.2f}).")
    else:
        classification_status = ClassificationStatus.COMPLETED

    # 5. Programming Analysis Status
    if capture_status == CaptureStatus.FAILED:
        programming_analysis_status = ProgrammingAnalysisStatus.FAILED
        reasons.append("Programming analysis failed because audio capture failed.")
    elif classification_status == ClassificationStatus.UNAVAILABLE:
        programming_analysis_status = ProgrammingAnalysisStatus.BLOCKED
        reasons.append(
            "Programming analysis is BLOCKED: required speech and broadcast classification "
            "data is unavailable."
        )
    elif is_insufficient_sample:
        programming_analysis_status = ProgrammingAnalysisStatus.INSUFFICIENT_SAMPLE
        limitations.append(
            "Sample duration is insufficient for definitive multi-hour programming patterns."
        )
    elif classification_status == ClassificationStatus.LIMITED:
        programming_analysis_status = ProgrammingAnalysisStatus.LIMITED
    else:
        programming_analysis_status = ProgrammingAnalysisStatus.COMPLETED

    # 6. Report and Export Status
    report_status = (
        ReportStatus.COMPLETED if capture_status != CaptureStatus.FAILED else ReportStatus.FAILED
    )
    export_status = (
        ExportStatus.COMPLETED if capture_status == CaptureStatus.COMPLETED else ExportStatus.FAILED
    )

    # 7. Decision Readiness
    if (
        capture_status == CaptureStatus.FAILED
        or transcription_status in (TranscriptionStatus.FAILED, TranscriptionStatus.UNAVAILABLE)
        or classification_status == ClassificationStatus.UNAVAILABLE
        or programming_analysis_status
        in (ProgrammingAnalysisStatus.BLOCKED, ProgrammingAnalysisStatus.FAILED)
        or critical_incidents_count > 0
    ):
        decision_readiness = DecisionReadiness.NOT_READY
    elif (
        transcription_status in (TranscriptionStatus.DEGRADED, TranscriptionStatus.SKIPPED)
        or classification_status == ClassificationStatus.LIMITED
        or programming_analysis_status
        in (ProgrammingAnalysisStatus.LIMITED, ProgrammingAnalysisStatus.INSUFFICIENT_SAMPLE)
        or timeline_coverage_percent < 90.0
    ):
        decision_readiness = DecisionReadiness.LIMITED
    else:
        decision_readiness = DecisionReadiness.READY

    return AnalyticalValidity(
        capture_status=capture_status,
        processing_status=processing_status,
        transcription_status=transcription_status,
        classification_status=classification_status,
        programming_analysis_status=programming_analysis_status,
        report_status=report_status,
        export_status=export_status,
        decision_readiness=decision_readiness,
        reasons=reasons,
        limitations=limitations,
    )
