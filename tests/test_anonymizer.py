"""Tests for the anonymizer: round-trip on synthetic DICOMs."""

import json
import re

import numpy as np
import pydicom
import pytest

from medanon.anonymizer import Anonymizer, anonymize_directory, pseudonym_for
from medanon.synth import generate

SHAPE = (64, 64)  # small images keep the suite fast


@pytest.fixture()
def raw_dir(tmp_path):
    d = tmp_path / "raw"
    generate(d, n=12, seed=1234, shape=SHAPE)
    return d


@pytest.fixture()
def anon_result(tmp_path, raw_dir):
    out = tmp_path / "anon"
    results, salt = anonymize_directory(raw_dir, out, salt="a" * 32)
    return out, results, salt, raw_dir


def _pixels(path):
    return pydicom.dcmread(path).pixel_array


def test_phi_tags_removed(anon_result):
    out, results, salt, raw_dir = anon_result
    for f in sorted(out.glob("*.dcm")):
        ds = pydicom.dcmread(f)
        assert str(ds.PatientName) == "ANONYMIZED"
        assert re.fullmatch(r"ANON-[0-9a-f]{16}", str(ds.PatientID))
        assert str(ds.PatientAddress) == ""
        assert str(ds.PatientTelephoneNumbers) == ""
        assert str(ds.InstitutionAddress) == ""
        assert str(ds.StationName) == ""
        assert str(ds.AccessionNumber) == ""
        assert str(ds.InstitutionName) == "GENERALIZED"
        # every person-name tag anonymized
        for elem in ds.iterall():
            if elem.VR == "PN":
                assert str(elem.value) == "ANONYMIZED", elem.tag
        # no private tags survive
        assert not any(e.tag.is_private for e in ds.iterall())
        # identity-removed marker set (PS3.15)
        assert ds[0x00120062].value == "YES"


def test_pseudonym_map_consistent(tmp_path, raw_dir):
    out1, out2 = tmp_path / "a1", tmp_path / "a2"
    r1, salt = anonymize_directory(raw_dir, out1, salt="b" * 32)
    # second run with same salt -> identical pseudonyms (deterministic)
    r2, _ = anonymize_directory(raw_dir, out2, salt="b" * 32)
    assert [r["pseudonym"] for r in r1] == [r["pseudonym"] for r in r2]
    # map file exists, holds shift offsets, contains no original IDs
    mapp = json.loads((out1 / "pseudonym_map.json").read_text())
    assert set(mapp["patients"]) == {r["pseudonym"] for r in r1}
    blob = json.dumps(mapp)
    for raw in raw_dir.glob("*.dcm"):
        assert str(pydicom.dcmread(raw, stop_before_pixels=True).PatientID) not in blob
    # longitudinal: extending with the existing map keeps the same shift days
    out3 = tmp_path / "a3"
    r3, _ = anonymize_directory(raw_dir, out3, salt="b" * 32,
                               map_path=out1 / "pseudonym_map.json")
    assert [r["shift_days"] for r in r3] == [r["shift_days"] for r in r1]


def test_dates_shifted(anon_result):
    out, results, salt, raw_dir = anon_result
    for res in results:
        src = pydicom.dcmread(raw_dir / res["file"], stop_before_pixels=True)
        dst = pydicom.dcmread(out / res["file"], stop_before_pixels=True)
        assert str(dst.PatientBirthDate) != str(src.PatientBirthDate)
        assert str(dst.StudyDate) != str(src.StudyDate)
        # shifted values are still valid YYYYMMDD
        assert re.fullmatch(r"\d{8}", str(dst.StudyDate))


def test_pixel_data_byte_identical(anon_result):
    out, results, salt, raw_dir = anon_result
    for res in results:
        a = _pixels(raw_dir / res["file"])
        b = _pixels(out / res["file"])
        assert a.shape == b.shape
        assert np.array_equal(a, b)
        assert (raw_dir / res["file"]).stat  # noqa - sanity


def test_clinical_tags_preserved(anon_result):
    out, results, salt, raw_dir = anon_result
    for res in results:
        src = pydicom.dcmread(raw_dir / res["file"], stop_before_pixels=True)
        dst = pydicom.dcmread(out / res["file"], stop_before_pixels=True)
        for kw in ("Modality", "StudyDescription", "Rows", "Columns",
                   "BitsAllocated", "PhotometricInterpretation"):
            assert str(dst.get(kw)) == str(src.get(kw)), kw
        # class UIDs untouched, instance UIDs remapped onto 2.25.
        assert str(dst.SOPClassUID) == str(src.SOPClassUID)
        assert str(dst.SOPInstanceUID).startswith("2.25.")
        assert str(dst.SOPInstanceUID) != str(src.SOPInstanceUID)
        assert str(dst.StudyInstanceUID).startswith("2.25.")


def test_pseudonym_deterministic():
    assert pseudonym_for("s", "123") == pseudonym_for("s", "123")
    assert pseudonym_for("s", "123") != pseudonym_for("s", "124")
    assert pseudonym_for("s", "123") != pseudonym_for("other", "123")


def test_empty_source_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        anonymize_directory(tmp_path / "nope", tmp_path / "out", salt="c" * 32)
