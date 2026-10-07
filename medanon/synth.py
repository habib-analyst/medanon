"""Synthetic DICOM generator for demos and tests.

Creates realistic-but-fake DICOM files: random pixel data plus PHI-filled
tags (names, MRNs, birth dates, addresses, phone numbers) so the
anonymize -> qa pipeline can be exercised without any real patient data.

All identities are fictitious. Any resemblance to real persons is coincidental.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset

FIRST_NAMES = ["James", "Maria", "Robert", "Linda", "Michael", "Sarah",
               "David", "Emma", "Daniel", "Olivia", "William", "Sophia"]
LAST_NAMES = ["Carter", "Nguyen", "Smith", "Garcia", "Kim", "Patel",
              "Johnson", "Weber", "Silva", "Khan", "Muller", "Rossi"]
STREETS = ["12 Maple Ave", "48 Oak Street", "7 Birch Lane", "301 Pine Rd",
           "55 Cedar Blvd", "19 Elm Drive"]
CITIES = ["Springfield", "Riverton", "Lakeside", "Fairview", "Brookfield"]
MODALITIES = ["MR", "CT", "US", "DX"]
STUDY_DESCS = ["Brain MRI without contrast", "Chest CT", "Abdominal Ultrasound",
               "Knee MRI", "Head CT"]


def _rand_uid(rng: random.Random) -> str:
    # Private test root 2.25-style; fine for synthetic data.
    return "2.25." + str(rng.getrandbits(128))


def make_patient(rng: random.Random) -> dict:
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    dob = date(1940, 1, 1) + timedelta(days=rng.randint(0, 25000))
    return {
        "name": f"{last}^{first}",
        "id": f"{rng.randint(100000, 999999)}",
        "dob": dob.strftime("%Y%m%d"),
        "address": f"{rng.choice(STREETS)}, {rng.choice(CITIES)}",
        "phone": f"+1-{rng.randint(200, 989)}-{rng.randint(200, 989)}-{rng.randint(1000, 9999)}",
        "sex": rng.choice(["M", "F"]),
    }


def smooth_phantom(rng: random.Random, shape: tuple[int, int] = (256, 256)) -> np.ndarray:
    """Smooth, low-gradient phantom: no sharp edges (clean w.r.t. the pixel heuristic)."""
    np_rng = np.random.default_rng(rng.randrange(2 ** 63))
    h, w = shape
    coarse = np_rng.random((max(4, h // 32), max(4, w // 32))).astype(np.float32)
    up = np.kron(coarse, np.ones((32, 32), dtype=np.float32))[:h, :w]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    grad = 300 + 0.8 * xx + 0.5 * yy  # gentle background ramp
    noise = np_rng.normal(0, 4, size=(h, w)).astype(np.float32)
    arr = (up * 900 + grad + noise).clip(0, 4095)
    return arr.astype(np.uint16)


def draw_burned_in_text(arr: np.ndarray, rng: random.Random) -> np.ndarray:
    """Burn text-like bright bars into the top border strip (simulates annotations).

    Bars sit inside the top h//12 rows — the same strip the pixel heuristic
    inspects — so the synthetic annotation is a faithful test of the detector.
    """
    out = arr.copy().astype(np.int32)
    h, w = out.shape[:2]
    peak = int(out.max())
    bright = min(65535, peak + 3000)
    strip = max(6, h // 12)
    y0, y1 = 2, strip
    x = 8
    # 2-3 "words": runs of bright character-like blocks separated by gaps.
    for _ in range(rng.randint(2, 3)):
        word_w = rng.randint(w // 6, w // 4)
        while x < 8 + word_w and x < w - 8:
            cw = rng.randint(5, 11)
            out[y0:y1, x:x + cw] = bright
            x += cw + rng.randint(3, 6)
        x += rng.randint(10, 20)
    return np.clip(out, 0, 65535).astype(np.uint16)


def make_dataset(patient: dict, rng: random.Random,
                 shape: tuple[int, int] = (256, 256),
                 study_uid: str | None = None,
                 series_uid: str | None = None,
                 modality: str | None = None,
                 annotated: bool = False) -> FileDataset:
    study_uid = study_uid or _rand_uid(rng)
    series_uid = series_uid or _rand_uid(rng)
    modality = modality or rng.choice(MODALITIES)
    study_date = (date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))).strftime("%Y%m%d")

    arr = smooth_phantom(rng, shape)
    if annotated:
        arr = draw_burned_in_text(arr, rng)

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture
    file_meta.MediaStorageSOPInstanceUID = _rand_uid(rng)
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

    ds = FileDataset(None, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality
    ds.PatientName = patient["name"]
    ds.PatientID = patient["id"]
    ds.PatientBirthDate = patient["dob"]
    ds.PatientSex = patient["sex"]
    ds.PatientAddress = patient["address"]
    ds.PatientTelephoneNumbers = patient["phone"]
    ds.StudyDate = study_date
    ds.SeriesDate = study_date
    ds.AcquisitionDate = study_date
    ds.ContentDate = study_date
    ds.StudyDescription = rng.choice(STUDY_DESCS)
    ds.SeriesDescription = f"{modality} series 1"
    ds.InstitutionName = "Springfield General Hospital"
    ds.InstitutionAddress = "100 Hospital Way, Springfield"
    ds.ReferringPhysicianName = f"{rng.choice(LAST_NAMES)}^{rng.choice(FIRST_NAMES)}"
    ds.StationName = "SCANNER-03"
    ds.AccessionNumber = f"ACC{rng.randint(100000, 999999)}"
    ds.StudyID = f"{rng.randint(10000, 99999)}"
    # A private tag with PHI, as real scanners/PACS often emit.
    ds.add_new((0x0011, 0x1010), "LO", f"Technologist note for {patient['name']}")

    ds.Rows, ds.Columns = arr.shape
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = arr.tobytes()
    return ds


def generate(out_dir: Path, n: int = 20, seed: int = 42,
             annotate: int = 0,
             shape: tuple[int, int] = (256, 256)) -> list[Path]:
    """Generate ``n`` synthetic DICOM files (``annotate`` of them burned-in).

    Files from a small pool of patients share StudyInstanceUIDs, so the
    set exercises longitudinal consistency of the pseudonym map.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    n_patients = max(1, min(5, n // 3))
    patients = [make_patient(rng) for _ in range(n_patients)]
    study_uids = {i: _rand_uid(rng) for i in range(n_patients)}

    paths = []
    annotated_idx = set(rng.sample(range(n), min(annotate, n)))
    for i in range(n):
        p = i % n_patients
        ds = make_dataset(
            patients[p], rng, shape=shape,
            study_uid=study_uids[p],
            annotated=(i in annotated_idx),
        )
        path = out_dir / f"IMG_{i:03d}.dcm"
        ds.save_as(path)
        paths.append(path)
    return paths
