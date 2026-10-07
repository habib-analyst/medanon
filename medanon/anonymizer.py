"""DICOM anonymizer implementing the DICOM PS3.15 Basic Application Level
Confidentiality Profile (simplified for research use).

What it does
------------
* PatientName and every other Person Name (PN) tag      -> "ANONYMIZED"
* PatientID                                            -> deterministic pseudonym
                                                          "ANON-" + salted SHA-256 (16 hex chars)
* StudyDate / SeriesDate / AcquisitionDate /
  ContentDate / PatientBirthDate                        -> shifted by a per-patient
                                                          random offset (days), stored in the
                                                          pseudonym map so longitudinal
                                                          studies stay consistent
* PatientAddress, phone numbers, occupation, comments,
  InstitutionAddress, StationName, ...                  -> removed (emptied)
* InstitutionName                                       -> "GENERALIZED"
* StudyID                                               -> deterministic pseudonym
* SOP Instance / Study / Series UIDs                    -> deterministically remapped
                                                          ("2.25." + 128-bit hash int), so
                                                          intra-dataset references stay valid
* Private tags (odd groups)                             -> removed
* Pixel data and all clinically relevant non-PHI tags
  (Modality, StudyDescription, Rows, Columns, ...)      -> preserved byte-for-byte

The pseudonym map (``pseudonym_map.json``) stores only
``{pseudonym: shift_days}`` — it never contains original identifiers.
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset

PLACEHOLDER_NAME = "ANONYMIZED"
PLACEHOLDER_INSTITUTION = "GENERALIZED"

#: Date (DA) tags that are *shifted*, not removed — shifting preserves
#: longitudinal intervals while breaking linkage to real calendar dates.
SHIFT_DATE_TAGS = {
    (0x0008, 0x0020),  # StudyDate
    (0x0008, 0x0021),  # SeriesDate
    (0x0008, 0x0022),  # AcquisitionDate
    (0x0008, 0x0023),  # ContentDate
    (0x0010, 0x0030),  # PatientBirthDate
}

#: Direct-identifier / contact / free-text tags that are emptied.
REMOVE_TAGS = {
    (0x0008, 0x0050),  # AccessionNumber
    (0x0008, 0x0081),  # InstitutionAddress
    (0x0008, 0x1010),  # StationName
    (0x0008, 0x1080),  # AdmittingDiagnosesDescription
    (0x0010, 0x1000),  # OtherPatientIDs
    (0x0010, 0x1040),  # PatientAddress
    (0x0010, 0x1060),  # PatientMothersBirthName
    (0x0010, 0x1090),  # MedicalRecordLocator
    (0x0010, 0x2000),  # MedicalAlerts
    (0x0010, 0x2110),  # Allergies
    (0x0010, 0x2154),  # PatientTelephoneNumbers
    (0x0010, 0x2155),  # PatientTelecomInformation
    (0x0010, 0x2160),  # EthnicGroup
    (0x0010, 0x2180),  # Occupation
    (0x0010, 0x21A0),  # SmokingStatus
    (0x0010, 0x21B0),  # AdditionalPatientHistory
    (0x0010, 0x21D0),  # PatientReligiousPreference
    (0x0010, 0x4000),  # PatientComments
    (0x0038, 0x0010),  # AdmissionID
    (0x0038, 0x0300),  # CurrentPatientLocation
}

#: UI tags that are *class* identifiers and must never be remapped.
CLASS_UID_TAGS = {
    (0x0002, 0x0002),  # MediaStorageSOPClassUID
    (0x0002, 0x0010),  # TransferSyntaxUID
    (0x0008, 0x0016),  # SOPClassUID
}

#: Text VRs that can plausibly carry PHI in free text.
TEXT_VRS = {"PN", "LO", "SH", "ST", "LT", "UT", "AE"}


def _salted_hash(salt: str, value: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


def pseudonym_for(salt: str, patient_id: str) -> str:
    """Deterministic patient pseudonym: ANON-<16 hex chars>."""
    return "ANON-" + _salted_hash(salt, patient_id)[:16]


def remapped_uid(salt: str, uid: str) -> str:
    """Deterministically remap an instance UID onto the 2.25 (UUID) OID arc."""
    digest = hashlib.sha256(f"{salt}:uid:{uid}".encode("utf-8")).digest()[:16]
    return "2.25." + str(int.from_bytes(digest, "big"))


def shift_date(value: str, days: int) -> str:
    """Shift a DICOM DA (YYYYMMDD) value by ``days``; pass through if unparseable."""
    if not value:
        return value
    try:
        dt = datetime.strptime(value.strip(), "%Y%m%d")
    except ValueError:
        return value
    return (dt + timedelta(days=days)).strftime("%Y%m%d")


class Anonymizer:
    """Stateful anonymizer: keeps UID and per-patient shift mappings consistent."""

    def __init__(self, salt: str, map_path: Path | None = None, rng: random.Random | None = None):
        self.salt = salt
        self.map_path = map_path
        self.rng = rng or random.Random()
        self.uid_map: dict[str, str] = {}
        self.patients: dict[str, int] = {}  # pseudonym -> shift days
        if map_path is not None and map_path.exists():
            data = json.loads(map_path.read_text())
            self.patients = dict(data.get("patients", {}))

    # -- mapping helpers -------------------------------------------------
    def _uid(self, uid: str) -> str:
        if uid not in self.uid_map:
            self.uid_map[uid] = remapped_uid(self.salt, uid)
        return self.uid_map[uid]

    def shift_for(self, pseudonym: str) -> int:
        if pseudonym not in self.patients:
            shift = 0
            while shift == 0:  # a zero shift would leave real dates intact
                shift = self.rng.randint(-60, 60)
            self.patients[pseudonym] = shift
        return self.patients[pseudonym]

    # -- dataset processing ----------------------------------------------
    def _process_dataset(self, ds: Dataset, shift_days: int) -> None:
        to_delete = []
        for elem in ds:
            key = (elem.tag.group, elem.tag.element)
            if elem.VR == "SQ":
                for item in elem.value:
                    self._process_dataset(item, shift_days)
                continue
            if elem.tag.is_private:
                to_delete.append(elem.tag)
                continue
            if key in REMOVE_TAGS:
                elem.value = ""
                continue
            if key in SHIFT_DATE_TAGS and elem.value:
                elem.value = shift_date(str(elem.value), shift_days)
                continue
            if elem.VR == "PN":
                elem.value = PLACEHOLDER_NAME
                continue
            if key == (0x0008, 0x0080):  # InstitutionName
                elem.value = PLACEHOLDER_INSTITUTION
                continue
            if key == (0x0020, 0x0010) and elem.value:  # StudyID (SH: max 16 chars)
                elem.value = "STUDY-" + _salted_hash(self.salt, str(elem.value))[:10]
                continue
            if elem.VR == "UI" and key not in CLASS_UID_TAGS and elem.value:
                elem.value = self._uid(str(elem.value))
        for tag in to_delete:
            del ds[tag]

    def anonymize_file(self, src: Path, dst: Path) -> dict:
        """Anonymize one file; returns a summary dict."""
        ds = pydicom.dcmread(src)
        orig_pid = str(ds.get("PatientID", "UNKNOWN") or "UNKNOWN")
        pseudo = pseudonym_for(self.salt, orig_pid)
        shift = self.shift_for(pseudo)

        self._process_dataset(ds, shift)
        ds.PatientID = pseudo
        # PS3.15: record that identity was removed + the method used.
        ds.add_new((0x0012, 0x0062), "CS", "YES")
        ds.add_new((0x0012, 0x0063), "LO", f"medanon PS3.15 Basic Profile")

        # Keep file meta consistent with the remapped dataset UIDs.
        file_meta = getattr(ds, "file_meta", None)
        if file_meta is not None and file_meta.get("MediaStorageSOPInstanceUID"):
            file_meta.MediaStorageSOPInstanceUID = ds.get("SOPInstanceUID", "")

        dst.parent.mkdir(parents=True, exist_ok=True)
        ds.save_as(dst)
        return {"file": dst.name, "pseudonym": pseudo, "shift_days": shift}

    def anonymize_directory(self, src_dir: Path, dst_dir: Path) -> list[dict]:
        """Anonymize every ``*.dcm`` in ``src_dir`` into ``dst_dir``."""
        src_dir, dst_dir = Path(src_dir), Path(dst_dir)
        files = sorted(src_dir.glob("*.dcm"))
        if not files:
            raise FileNotFoundError(f"no .dcm files found in {src_dir}")
        results = [self.anonymize_file(f, dst_dir / f.name) for f in files]
        self.save_map(dst_dir / "pseudonym_map.json")
        return results

    def save_map(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "generator": "medanon",
                    "salt_hint": f"sha256:{hashlib.sha256(self.salt.encode()).hexdigest()[:16]}",
                    "patients": self.patients,
                },
                indent=2,
            )
        )


def anonymize_directory(src_dir: Path, dst_dir: Path, salt: str | None = None,
                        map_path: Path | None = None) -> tuple[list[dict], str]:
    """Convenience wrapper. Generates a salt when none is given; returns (results, salt)."""
    if salt is None:
        salt = secrets.token_hex(16)
        print(f"[medanon] generated salt: {salt}  (store it to reproduce this run)")
    anon = Anonymizer(salt=salt, map_path=map_path)
    results = anon.anonymize_directory(src_dir, dst_dir)
    if map_path is None:
        anon.save_map(Path(dst_dir) / "pseudonym_map.json")
    return results, salt
