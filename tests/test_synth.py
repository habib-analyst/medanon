"""Tests for the synthetic DICOM generator."""

import pydicom
import pytest

from medanon.synth import generate


def test_generate_creates_valid_dicom(tmp_path):
    paths = generate(tmp_path / "raw", n=6, seed=42, shape=(64, 64))
    assert len(paths) == 6
    for p in paths:
        ds = pydicom.dcmread(p)
        assert ds.SOPInstanceUID
        assert ds.pixel_array.shape == (64, 64)


def test_synthetic_data_contains_phi_before_anonymization(tmp_path):
    paths = generate(tmp_path / "raw", n=4, seed=42, shape=(64, 64))
    ds = pydicom.dcmread(paths[0])
    # Realistic PHI present so the pipeline has something to strip.
    assert "^" in str(ds.PatientName)
    assert str(ds.PatientBirthDate)
    assert str(ds.PatientAddress)
    assert str(ds.PatientTelephoneNumbers)
    assert any(e.tag.is_private for e in ds.iterall())


def test_generate_is_deterministic(tmp_path):
    p1 = generate(tmp_path / "a", n=3, seed=7, shape=(64, 64))
    p2 = generate(tmp_path / "b", n=3, seed=7, shape=(64, 64))
    for a, b in zip(p1, p2):
        da, db = pydicom.dcmread(a), pydicom.dcmread(b)
        assert str(da.PatientName) == str(db.PatientName)
        assert (da.pixel_array == db.pixel_array).all()


def test_longitudinal_study_uids_shared(tmp_path):
    paths = generate(tmp_path / "raw", n=9, seed=42, shape=(64, 64))
    uids = {str(pydicom.dcmread(p, stop_before_pixels=True).StudyInstanceUID)
            for p in paths}
    assert 1 < len(uids) <= 5  # a few patients, several files each
