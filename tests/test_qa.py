"""Tests for the QA pass: tag scan, PHI re-injection, pixel heuristic."""

import numpy as np
import pydicom
import pytest

from medanon.anonymizer import anonymize_directory
from medanon.qa import pixel_suspicious, scan_directory
from medanon.synth import draw_burned_in_text, generate, smooth_phantom

SHAPE = (64, 64)


@pytest.fixture()
def clean_anon(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "anon"
    generate(raw, n=8, seed=777, shape=SHAPE)
    anonymize_directory(raw, out, salt="d" * 32)
    return out


def test_clean_anonymized_passes(clean_anon):
    report = scan_directory(clean_anon)
    assert report.files_scanned == 8
    assert report.tags_checked > 0
    assert report.verdict == "PASS"
    assert report.counts()["HIGH"] == 0


def test_qa_catches_reinjected_phi_in_private_tag(clean_anon, tmp_path):
    # Adversary / buggy pipeline plants a name in a private tag post-anonymization.
    victim = sorted(clean_anon.glob("*.dcm"))[0]
    ds = pydicom.dcmread(victim)
    ds.add_new((0x0011, 0x10AA), "LO", "John Carter")
    ds.save_as(victim)

    report = scan_directory(clean_anon)
    assert report.verdict == "FAIL"
    highs = [f for f in report.findings if f.severity == "HIGH"]
    assert highs, "expected a HIGH finding for the re-injected name"
    assert any("John Carter" in f.message for f in highs)


def test_qa_catches_unanonymized_name(clean_anon):
    victim = sorted(clean_anon.glob("*.dcm"))[1]
    ds = pydicom.dcmread(victim)
    ds.PatientName = "Smith^John"
    ds.save_as(victim)
    report = scan_directory(clean_anon)
    assert report.verdict == "FAIL"
    assert any(f.tag == "(0010,0010)" and f.severity == "HIGH"
               for f in report.findings)


def test_qa_catches_phone_and_email(clean_anon):
    victim = sorted(clean_anon.glob("*.dcm"))[2]
    ds = pydicom.dcmread(victim)
    ds.add_new((0x0011, 0x10BB), "LT", "call +1-555-123-4567 or jane@example.com")
    ds.save_as(victim)
    report = scan_directory(clean_anon)
    msgs = " ".join(f.message for f in report.findings if f.severity == "HIGH")
    assert "phone-like" in msgs and "email-like" in msgs


def test_pixel_heuristic_flags_annotated_not_clean():
    import random
    rng = random.Random(0)
    clean = smooth_phantom(rng, SHAPE)
    annotated = draw_burned_in_text(clean.copy(), rng)

    suspicious, density, ratio = pixel_suspicious(annotated)
    assert suspicious, f"annotated not flagged (density={density:.3f}, ratio={ratio:.1f})"

    suspicious_c, density_c, ratio_c = pixel_suspicious(clean)
    assert not suspicious_c, f"clean flagged (density={density_c:.3f}, ratio={ratio_c:.1f})"


def test_qa_flags_burned_in_in_directory(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "anon"
    generate(raw, n=4, seed=999, annotate=2, shape=SHAPE)
    anonymize_directory(raw, out, salt="e" * 32)
    report = scan_directory(out)
    pixel_hits = [f for f in report.findings if "burned-in" in f.message]
    assert len(pixel_hits) == 2, f"expected 2 pixel findings, got {len(pixel_hits)}"
    # pixel findings are MEDIUM -> no PHI leak -> verdict still PASS
    assert report.verdict == "PASS"


def test_markdown_report(tmp_path, clean_anon):
    from medanon.qa import write_markdown
    report = scan_directory(clean_anon)
    p = write_markdown(report, tmp_path / "qa.md")
    text = p.read_text()
    assert "Verdict: PASS" in text
    assert "files scanned" in text
