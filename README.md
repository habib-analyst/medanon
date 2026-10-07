# medanon

[![CI](https://github.com/habib-analyst/medanon/actions/workflows/ci.yml/badge.svg)](https://github.com/habib-analyst/medanon/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

DICOM anonymization plus a PHI-leak QA pass — because "anonymized" releases
leak more often than anyone admits.

## Problem

Medical-imaging researchers must strip protected health information (PHI)
before sharing datasets, but de-identification is usually a one-way script
with no verification. Silent PHI leaks in supposedly anonymized releases —
a patient name left in a private tag, a phone number in a technologist note,
an ID burned into the pixel data of an ultrasound frame — are a genuine
compliance risk. **medanon** pairs a
[DICOM PS3.15](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/PS3.15.html)
Basic Application Level Confidentiality Profile anonymizer with an
independent QA pass that hunts for whatever the anonymizer missed.

## Architecture

```
                    ┌──────────────┐
                    │  medanon     │
                    │  synth       │  realistic-but-fake DICOMs
                    └──────┬───────┘  (names, MRNs, DOBs, phones,
                             │        optional burned-in annotations)
                             ▼
┌─────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│ raw     │──▶│  medanon anonymize   │──▶│      medanon qa          │
│ DICOMs  │   │                      │   │                          │
└─────────┘   │ • PatientName→ANON.  │   │ (a) tag-level regex scan │
              │ • PatientID→salted   │   │     names/dates/IDs/     │
              │   SHA-256 pseudonym  │   │     phone/email/addr     │
              │ • dates shifted by   │   │ (b) pixel check: border  │
              │   per-patient offset │   │     edge-density         │
              │   (pseudonym_map.    │   │     heuristic for        │
              │   json keeps longi-  │   │     burned-in text       │
              │   tudinal studies    │   │ (c) markdown report +    │
              │   consistent)        │   │     PASS/FAIL verdict    │
              │ • UIDs remapped      │   │                          │
              │   deterministically  │   │  HIGH finding ⇒ FAIL     │
              │ • private tags       │   │                          │
              │   removed; pixel     │   │                          │
              │   data untouched     │   │                          │
              └──────────────────────┘   └──────────────────────────┘
```

## Quickstart (< 5 min)

```bash
pip install -r requirements.txt   # pydicom, numpy — nothing else
# or: pip install -e .            # also installs the `medanon` command

# full pipeline on synthetic data (no real patient data needed):
python -m medanon demo

# the three stages individually:
python -m medanon synth --out data/raw -n 20
python -m medanon anonymize data/raw --out data/anon --salt <hex>
python -m medanon qa data/anon --report qa.md
```

## Real example output

Actual output of `python -m medanon demo` (20 synthetic files, 3 with
burned-in annotations, seed `20261007`):

```
== medanon demo: synth -> anonymize -> qa ==

[1/3] generating synthetic DICOMs (3 with burned-in annotations)...

[2/3] anonymizing (PS3.15 Basic Profile)...
[medanon] generated salt: 2e978a5d3feba556a295e5e693e0c57f  (store it to reproduce this run)
      20 files, 5 patients, map: medanon-demo/data/anon/pseudonym_map.json

[3/3] QA pass...
      verdict: PASS (HIGH=0 MEDIUM=3 LOW=0)
      [MEDIUM] IMG_007.dcm (7FE0,0010): possible burned-in annotation: border edge density 0.050, border/center ratio 50037202.4
      [MEDIUM] IMG_017.dcm (7FE0,0010): possible burned-in annotation: border edge density 0.049, border/center ratio 48921131.0
      [MEDIUM] IMG_018.dcm (7FE0,0010): possible burned-in annotation: border edge density 0.057, border/center ratio 56919642.9

Demo artifacts in medanon-demo/  (report: medanon-demo/qa.md)
NOTE: QA is a heuristic safety net, not a compliance guarantee — validate against your institution's policy/IRB.
```

All tag-level PHI stripped (names → `ANONYMIZED`, IDs → `ANON-<salted
SHA-256>`, dates shifted per-patient, private tags dropped), pixel data
byte-identical — and the QA caught exactly the 3 files with burned-in
annotations. Plant a name in a private tag after anonymization and the QA
fails the run with a HIGH finding (covered by the test suite).

## Disclaimer

**medanon is a research utility, not a compliance product.** It implements a
simplified subset of the DICOM PS3.15 Basic Application Level Confidentiality
Profile and a heuristic QA pass. It does not know your institution's policy,
your IRB protocol, or your data-use agreement. Always validate anonymized
output against your institution's de-identification policy and IRB
requirements before sharing data, and treat every QA verdict as a second
opinion — not a clearance.

## Roadmap

- [ ] Full PS3.15 profile options (Clean Pixel Data, Retain Longitudinal
      Temporal Information with Modified Dates)
- [ ] OCR-based burned-in text detection (currently gradient-heuristic only)
- [ ] DICOMDIR / multi-frame and compressed transfer syntax handling
- [ ] Allow-list config for site-specific tags
- [ ] HTML QA report with pixel thumbnails of flagged regions

## References

- DICOM PS3.15 — *Security and System Management Profiles*, Basic
  Application Level Confidentiality Profile.
  <https://dicom.nema.org/medical/dicom/current/output/chtml/part15/PS3.15.html>
- HIPAA Safe Harbor de-identification, 45 CFR §164.514(b)(2).

## License

MIT — see [LICENSE](LICENSE). © 2026 Habib Ur Rehman.
