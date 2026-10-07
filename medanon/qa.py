"""QA pass over anonymized DICOM: tag-level PHI scan + burned-in pixel check.

Findings carry a severity:

* HIGH   — definite PHI leak (real-looking name/ID/phone/email retained,
            duplicate SOP Instance UIDs). Any HIGH finding => verdict FAIL.
* MEDIUM — suspicious (private tags retained, date-like strings in free
            text, possible burned-in annotations in pixel data).
* LOW    — informational.

The pixel check looks for burned-in annotations (patient names / IDs burned
into ultrasound frames are the classic leak vector). It measures gradient
edge density in the top/bottom border strips versus the image centre:
text-like structures produce dense, high-contrast edges exactly where
annotations are burned in. numpy only, no heavy dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pydicom

from .anonymizer import SHIFT_DATE_TAGS, TEXT_VRS

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Phone: optional country code, then 10-15 digits with single separators.
# Structured so hex pseudonyms (ANON-...) and dates can't match.
PHONE_RE = re.compile(r"(?<!\d)(?:\+\d{1,3}[-.\s]?)?(?:\d[-.\s]?){9,14}\d(?!\d)")
DATE8_RE = re.compile(r"\b(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b")
# Two-or-more capitalized words, e.g. "John Carter".
NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})+\b")
PSEUDO_RE = re.compile(r"ANON-[0-9a-f]{16}")
STUDY_PSEUDO_RE = re.compile(r"STUDY-[0-9a-f]{10}")

#: Tags whose values are medanon pseudonyms: covered by the structural
#: checks below, so free-text pattern checks skip them (a 10-digit hash
#: fragment is indistinguishable from a phone number otherwise).
PSEUDO_TAGS = {
    (0x0010, 0x0020),  # PatientID
    (0x0020, 0x0010),  # StudyID
}

#: Free-text tags where name-like prose is expected and benign.
DESCRIPTION_TAGS = {
    (0x0008, 0x1030),  # StudyDescription
    (0x0008, 0x103E),  # SeriesDescription
    (0x0018, 0x1030),  # ProtocolName
    (0x0012, 0x0062),  # PatientIdentityRemoved
    (0x0012, 0x0063),  # De-identificationMethod
}

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

PIXEL_TAG = (0x7FE0, 0x0010)


@dataclass
class Finding:
    severity: str  # HIGH | MEDIUM | LOW
    file: str
    tag: str
    message: str


@dataclass
class QAReport:
    files_scanned: int = 0
    tags_checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "FAIL" if any(f.severity == "HIGH" for f in self.findings) else "PASS"

    def counts(self) -> dict[str, int]:
        c = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in self.findings:
            c[f.severity] += 1
        return c

    def to_markdown(self) -> str:
        c = self.counts()
        lines = [
            "# medanon QA report",
            "",
            f"**Verdict: {self.verdict}**",
            "",
            "| metric | value |",
            "|---|---|",
            f"| files scanned | {self.files_scanned} |",
            f"| tags checked | {self.tags_checked} |",
            f"| HIGH findings | {c['HIGH']} |",
            f"| MEDIUM findings | {c['MEDIUM']} |",
            f"| LOW findings | {c['LOW']} |",
            "",
        ]
        if self.findings:
            lines += ["## Findings", "",
                      "| severity | file | tag | message |",
                      "|---|---|---|---|"]
            for f in sorted(self.findings, key=lambda x: SEVERITY_ORDER[x.severity]):
                lines.append(f"| {f.severity} | {f.file} | {f.tag} | {f.message} |")
        else:
            lines.append("No findings — all checks clean.")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- pixel check

def _edge_density(arr: np.ndarray) -> tuple[float, float]:
    """Return (border_edge_density, border_to_center_ratio)."""
    a = np.asarray(arr, dtype=np.float32)
    if a.ndim == 3:  # multi-frame / RGB -> use first frame, luminance-ish mean
        a = a[0] if a.shape[0] < a.shape[-1] else a.mean(axis=-1)
    if a.ndim != 2 or a.size == 0:
        return 0.0, 0.0
    gx = np.abs(np.diff(a, axis=1))
    gy = np.abs(np.diff(a, axis=0))
    mag = np.zeros_like(a)
    mag[:, :-1] += gx
    mag[:-1, :] += gy
    peak = mag.max()
    if peak <= 0:
        return 0.0, 0.0
    edges = mag > (0.25 * peak)
    h = a.shape[0]
    strip = max(1, h // 12)
    top = edges[:strip, :].mean()
    bottom = edges[-strip:, :].mean()
    center = edges[h // 3: 2 * h // 3, :].mean()
    border = max(top, bottom)
    return float(border), float(border / (center + 1e-9))


def pixel_suspicious(arr: np.ndarray, density_thresh: float = 0.04,
                     ratio_thresh: float = 2.5) -> tuple[bool, float, float]:
    """Heuristic burned-in annotation detector.

    Returns (suspicious, border_density, border_to_center_ratio).
    Burned-in text lives in the border strips and shows up as dense,
    high-contrast edges relative to the (usually smooth) image centre.
    """
    density, ratio = _edge_density(arr)
    return (density > density_thresh and ratio > ratio_thresh, density, ratio)


# ---------------------------------------------------------------- tag scan

def _tag_str(tag) -> str:
    return f"({tag.group:04X},{tag.element:04X})"


def scan_file(path: Path) -> tuple[list[Finding], int]:
    """Scan one DICOM file. Returns (findings, tags_checked)."""
    findings: list[Finding] = []
    fname = Path(path).name
    try:
        ds = pydicom.dcmread(path)
    except Exception as exc:  # unreadable file -> HIGH, it can't be verified
        return [Finding("HIGH", fname, "-", f"unreadable DICOM: {exc}")], 0

    tags_checked = 0

    # --- structural identity checks -------------------------------------
    pname = str(ds.get("PatientName", "") or "")
    if pname and pname != "ANONYMIZED":
        findings.append(Finding("HIGH", fname, "(0010,0010)",
                                f"PatientName not anonymized: {pname!r}"))
    pid = str(ds.get("PatientID", "") or "")
    if not PSEUDO_RE.fullmatch(pid):
        findings.append(Finding("HIGH", fname, "(0010,0020)",
                                f"PatientID is not a medanon pseudonym: {pid!r}"))
    sid = str(ds.get("StudyID", "") or "")
    if sid and not STUDY_PSEUDO_RE.fullmatch(sid):
        findings.append(Finding("HIGH", fname, "(0020,0010)",
                                f"StudyID is not a medanon pseudonym: {sid!r}"))
    if str(ds.get("InstitutionName", "") or "") not in ("GENERALIZED", ""):
        findings.append(Finding("MEDIUM", fname, "(0008,0080)",
                                "InstitutionName not generalized"))

    # --- element-wise scan ----------------------------------------------
    private_seen = 0
    for elem in ds.iterall():
        key = (elem.tag.group, elem.tag.element)
        if key == PIXEL_TAG:
            continue
        tags_checked += 1
        is_private = bool(elem.tag.is_private)
        if is_private:
            private_seen += 1
            if private_seen <= 5:
                findings.append(Finding("MEDIUM", fname, _tag_str(elem.tag),
                                        "private tag retained (may contain PHI)"))
        if elem.VR not in TEXT_VRS:
            continue
        vals = elem.value if isinstance(elem.value, (list, tuple)) else [elem.value]
        for v in vals:
            text = str(v or "")
            if not text.strip():
                continue
            if key in PSEUDO_TAGS:
                continue  # structural pseudonym checks above already cover these
            if elem.VR == "PN" and text != "ANONYMIZED":
                findings.append(Finding("HIGH", fname, _tag_str(elem.tag),
                                        f"person name retained: {text!r}"))
                continue
            if EMAIL_RE.search(text):
                findings.append(Finding("HIGH", fname, _tag_str(elem.tag),
                                        f"email-like string: {text!r}"))
            if PHONE_RE.search(text):
                findings.append(Finding("HIGH", fname, _tag_str(elem.tag),
                                        f"phone-like string: {text!r}"))
            # PHI-looking content hiding in a private tag is HIGH; elsewhere
            # date/name-like free text is MEDIUM (worth human review).
            if key not in SHIFT_DATE_TAGS and DATE8_RE.search(text):
                findings.append(Finding("HIGH" if is_private else "MEDIUM",
                                        fname, _tag_str(elem.tag),
                                        f"date-like string in free text: {text!r}"))
            if key not in DESCRIPTION_TAGS and NAME_RE.search(text):
                findings.append(Finding("HIGH" if is_private else "MEDIUM",
                                        fname, _tag_str(elem.tag),
                                        f"name-like string: {text!r}"))
    if private_seen > 5:
        findings.append(Finding("MEDIUM", fname, "-",
                                f"... and {private_seen - 5} more private tags"))

    # --- pixel check ------------------------------------------------------
    if PIXEL_TAG in ds:
        try:
            arr = ds.pixel_array
            suspicious, density, ratio = pixel_suspicious(arr)
            if suspicious:
                findings.append(Finding(
                    "MEDIUM", fname, "(7FE0,0010)",
                    f"possible burned-in annotation: border edge density "
                    f"{density:.3f}, border/center ratio {ratio:.1f}"))
        except Exception as exc:
            findings.append(Finding("LOW", fname, "(7FE0,0010)",
                                    f"pixel check skipped: {exc}"))

    return findings, tags_checked


def scan_directory(indir: Path) -> QAReport:
    """Scan every ``*.dcm`` in ``indir`` (non-recursive)."""
    indir = Path(indir)
    files = sorted(indir.glob("*.dcm"))
    report = QAReport()
    seen_uids: dict[str, str] = {}
    for path in files:
        findings, n_tags = scan_file(path)
        report.files_scanned += 1
        report.tags_checked += n_tags
        report.findings.extend(findings)
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            uid = str(ds.get("SOPInstanceUID", "") or "")
            if uid:
                if uid in seen_uids:
                    report.findings.append(Finding(
                        "HIGH", path.name, "(0008,0018)",
                        f"duplicate SOPInstanceUID also in {seen_uids[uid]}"))
                else:
                    seen_uids[uid] = path.name
        except Exception:
            pass
    return report


def write_markdown(report: QAReport, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_markdown())
    return path
