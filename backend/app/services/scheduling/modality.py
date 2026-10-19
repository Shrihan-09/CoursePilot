"""Conservative modality classification from all structured SOC meetings.

Missing times alone do not establish asynchronous delivery. A course's
classification is deliberately not persisted: options vary by section/term.
"""

from collections.abc import Iterable

from app.domain.modality import Modality, ModalityEvidence
from app.domain.schedule import MeetingInterval, MeetingKind
from app.services.scheduling.meetings import CLASS_MODES, ONLINE_MODE_DESC


def aggregate(evidence: Iterable[ModalityEvidence]) -> ModalityEvidence:
    parts = list(evidence)
    modes = sorted({m for p in parts for m in p.observed_modes if m is not Modality.UNKNOWN})
    complete = bool(parts) and all(p.complete for p in parts)
    reasons = sorted({r for p in parts for r in p.reasons})
    if not parts:
        reasons.append("no_components")
    classification = Modality.UNKNOWN
    if complete and modes:
        classification = modes[0] if len(modes) == 1 else Modality.HYBRID
    return ModalityEvidence(
        classification=classification,
        complete=complete,
        observed_modes=modes,
        reasons=reasons,
        component_indexes=sorted({i for p in parts for i in p.component_indexes}),
    )


def classify_meetings(
    meetings: list[MeetingInterval],
    *,
    index: str | None = None,
    unstructured_meetings: bool = False,
) -> ModalityEvidence:
    """Classify one exact section. Unknown components prevent a known label."""
    parts = []
    for m in meetings:
        desc = (m.mode_desc or "").strip().upper()
        mode = Modality.UNKNOWN
        reason = "unrecognized_delivery_evidence"
        if m.kind is MeetingKind.ASYNCHRONOUS and desc == ONLINE_MODE_DESC:
            mode, reason = Modality.ASYNCHRONOUS, "explicit_online_without_times"
        elif m.kind is MeetingKind.TIMED:
            if desc == ONLINE_MODE_DESC:
                mode, reason = Modality.SYNCHRONOUS_ONLINE, "explicit_online_with_times"
            elif (
                desc in CLASS_MODES
                and m.campus
                and m.campus.upper() != "ONL"
                and m.building
                and m.room
            ):
                mode, reason = Modality.IN_PERSON, "classroom_location_and_class_mode"
        else:
            reason = f"meeting_time_{m.kind.value}"
        parts.append(
            ModalityEvidence(
                classification=mode,
                complete=mode is not Modality.UNKNOWN,
                observed_modes=[mode],
                reasons=[reason],
            )
        )
    if unstructured_meetings:
        parts.append(ModalityEvidence(reasons=["unstructured_meeting_note"]))
    result = aggregate(parts)
    if index:
        result.component_indexes = [index]
    return result
