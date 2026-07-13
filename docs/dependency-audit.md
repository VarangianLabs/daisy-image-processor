# Dependency Health Audit
## Daisy Image Processor — Vendored Package Security Review

> **Audit Date:** 2026-07-13  
> **Auditor:** Principal Platform Architect  
> **Scope:** All packages vendored in `src/` that are bundled into the Lambda deployment package  
> **Verdict:** ✅ No known unpatched CVEs in the deployed package as of this date

---

## 1. Package Version Matrix

All packages below are confirmed at their current latest version on PyPI as of 2026-07-13.

| Package | Installed Version | Latest on PyPI | Status | Notes |
|---|---|---|---|---|
| `boto3` | 1.43.46 | 1.43.46 | ✅ Current | AWS SDK for Python |
| `botocore` | 1.43.46 | 1.43.46 | ✅ Current | boto3 dependency |
| `Pillow` | 12.3.0 | 12.3.0 | ✅ Current — **see Section 2** | Image processing core |
| `urllib3` | 2.7.0 | 2.7.0 | ✅ Current | HTTP transport for botocore |
| `s3transfer` | 0.19.1 | 0.19.1 | ✅ Current | boto3 S3 upload/download manager |
| `jmespath` | 1.1.0 | 1.1.0 | ✅ Current | boto3 JSON query dependency |
| `python-dateutil` | 2.9.0.post0 | 2.9.0.post0 | ✅ Current | botocore date parsing |
| `six` | 1.17.0 | 1.17.0 | ⚠️ Dead dependency — see Section 3 | Python 2/3 compatibility shim |

---

## 2. Pillow 12.3.0 — CVE Disclosure Analysis

Ten security advisories against Pillow were published in the week of 2026-07-06 to 2026-07-13. This section documents each advisory, its severity, and the assessed impact on the Daisy Image Processor pipeline.

### Release Timeline Context

| Event | Date |
|---|---|
| Pillow 12.3.0 released | 2026-07-01 |
| Security advisories published | 2026-07-06 to 2026-07-13 |
| This audit conducted | 2026-07-13 |

The advisories were published **after** the 12.3.0 release — consistent with the Pillow project's responsible disclosure pattern, where CVEs are filed publicly after the patched release ships. Pillow 12.3.0 includes a "Security" section in its official release notes. **12.3.0 is the patched version for all ten advisories below.**

---

### Advisory Detail and Pipeline Exposure

#### GHSA-9hw9-ch79-4vh6 — Controlled heap out-of-bounds write in `ImageCmsTransform.apply()`
- **Severity:** HIGH
- **Affected API:** `PIL.ImageCms.ImageCmsTransform.apply()`
- **Pipeline usage:** The Daisy pipeline does not use `ImageCms` in any form. No colour profile conversion is performed.
- **Exposure:** ✅ None

---

#### GHSA-6r8x-57c9-28j4 — Heap out-of-bounds write in `Image.paste()` / `Image.crop()` via signed coordinate overflow
- **Severity:** HIGH
- **Affected API:** `Image.paste()`, `Image.crop()`
- **Pipeline usage:** `apply_watermark()` in `image_processor.py` calls `Image.alpha_composite()`. `alpha_composite` may internally invoke paste-like operations, but all coordinate inputs are derived from `img.width` and `img.height` — both are positive integers bounded by `Image.MAX_IMAGE_PIXELS = 50_000_000`. No user-supplied coordinates reach this code path.
- **Exposure:** ✅ Negligible. Coordinate inputs are system-controlled, not attacker-controlled. Patched in 12.3.0.

---

#### GHSA-phj9-mv4w-65pm — `GdImageFile._open()`: image dimensions accepted without `_decompression_bomb_check()` (4.3 GB allocation via crafted `.gd` file)
- **Severity:** HIGH
- **Affected API:** `PIL.GdImageFile._open()`
- **Pipeline usage:** The `.gd` format is not in the pre-signed URL file extension allowlist (`{.jpg, .jpeg, .png, .webp}`). A `.gd` file cannot be uploaded through the API surface.
- **Exposure:** ✅ None. Format is blocked at the API boundary.

---

#### GHSA-5x94-69rx-g8h2 — `FontFile.compile()`: `Image.new()` called without `_decompression_bomb_check()`
- **Severity:** HIGH
- **Affected API:** `PIL.FontFile.compile()`
- **Pipeline usage:** The pipeline loads a single bundled TrueType font (`src/fonts/DejaVuSans.ttf`) using `ImageFont.truetype()`. `FontFile` is the base class for PCF and BDF bitmap font formats. TrueType loading does not use `FontFile.compile()`.
- **Exposure:** ✅ None. TrueType path is not affected. The bundled font is a trusted artifact.

---

#### GHSA-8v84-f9pq-wr9x — `PcfFontFile._load_bitmaps()`: decompression bomb protection bypass via PCF font loading
- **Severity:** HIGH
- **Affected API:** `PIL.PcfFontFile._load_bitmaps()`
- **Pipeline usage:** PCF is a bitmap font format. The pipeline only loads `DejaVuSans.ttf` (TrueType). PCF loading is never triggered.
- **Exposure:** ✅ None.

---

#### GHSA-45hq-cxwh-f6vc — `BdfFontFile`: `Image.new()` called without `_decompression_bomb_check()` via BDF font loading
- **Severity:** HIGH
- **Affected API:** `PIL.BdfFontFile`
- **Pipeline usage:** BDF is a bitmap font format. Same rationale as GHSA-8v84-f9pq-wr9x.
- **Exposure:** ✅ None.

---

#### GHSA-fj7v-r99m-22gq — Pillow TGA RLE encoder can serialize up to ~57 KB of adjacent heap data into generated images
- **Severity:** MODERATE
- **Affected API:** TGA format RLE encoder
- **Pipeline usage:** The pipeline outputs exclusively in JPEG format (`OUTPUT_FORMAT = "JPEG"` in `image_processor.py`). TGA encoding is never invoked.
- **Exposure:** ✅ None.

---

#### GHSA-xj96-63gp-2gmr — Heap out-of-bounds write in `ImageFilter.RankFilter` via integer overflow in `ImagingExpand`
- **Severity:** HIGH
- **Affected API:** `PIL.ImageFilter.RankFilter`
- **Pipeline usage:** The pipeline applies no image filters. `ImageFilter` is not imported.
- **Exposure:** ✅ None.

---

#### GHSA-62p4-gmf7-7g93 — Out-of-bounds read via attacker-controlled row stride on Pillow's mmap path (McIdas AREA files)
- **Severity:** HIGH
- **Affected API:** McIdas AREA format image loader (`_binary.py` mmap path)
- **Pipeline usage:** McIdas AREA is a scientific remote sensing format. It is not in the pre-signed URL file extension allowlist. A McIdas file cannot be uploaded through the API surface.
- **Exposure:** ✅ None. Format is blocked at the API boundary.

---

#### GHSA-vjc4-5qp5-m44j — Pillow JPEG2000 tiled decode retains a growing scratch buffer (denial of service)
- **Severity:** MODERATE
- **Affected API:** JPEG2000 tiled decode
- **Pipeline usage:** JPEG2000 (`.jp2`, `.j2k`) is not in the pre-signed URL file extension allowlist. The format cannot enter the pipeline through the standard API path.
- **Exposure:** ✅ None. Format is blocked at the API boundary.

---

### Summary

| Advisory | Severity | Pipeline Exposure |
|---|---|---|
| GHSA-9hw9-ch79-4vh6 | HIGH | None — ImageCms not used |
| GHSA-6r8x-57c9-28j4 | HIGH | Negligible — no user-controlled coordinates |
| GHSA-phj9-mv4w-65pm | HIGH | None — .gd blocked at API boundary |
| GHSA-5x94-69rx-g8h2 | HIGH | None — TrueType path unaffected |
| GHSA-8v84-f9pq-wr9x | HIGH | None — PCF not used |
| GHSA-45hq-cxwh-f6vc | HIGH | None — BDF not used |
| GHSA-fj7v-r99m-22gq | MODERATE | None — JPEG output only |
| GHSA-xj96-63gp-2gmr | HIGH | None — RankFilter not used |
| GHSA-62p4-gmf7-7g93 | HIGH | None — McIdas blocked at API boundary |
| GHSA-vjc4-5qp5-m44j | MODERATE | None — JPEG2000 blocked at API boundary |

**Installed version (12.3.0) is the patched release for all ten advisories. No action required.**

---

## 3. Dead Dependency — `six 1.17.0`

`six` is a Python 2/3 compatibility shim that provides a unified API for code that must run on both Python 2 and Python 3. Python 3.12 does not require `six`. It is not imported by any application code (`handler.py`, `image_processor.py`, `config.py`).

**Security impact:** None. `six` has no known vulnerabilities and handles no user input.

**Operational impact:** `six.py` is a single file bundled in the Lambda package, adding marginal size. More significantly, its presence alongside application source code (`src/`) creates structural confusion about what is first-party code.

**Action:** Remove as part of the `src/vendor/` separation work assigned to the Infrastructure Engineer (PR-02, Task C-01). Not a security remediation — a package hygiene action.

---

## 4. Recommended Automation

To ensure this audit remains current across releases, add `pip-audit` to the CI pipeline as part of the pre-release gate:

```bash
# Install in the test environment (not as a vendored dependency)
pip install pip-audit

# Audit against the vendored requirements
pip-audit --requirement src/requirements.txt
```

`pip-audit` queries the [OSV.dev](https://osv.dev) and [PyPI advisory](https://pypi.org/security) databases. A clean run produces no output and exits `0`. Any finding at MEDIUM or above should block the release.

---

## 5. Re-Audit Schedule

Re-run this audit:
- Before any public release tag
- Whenever a direct dependency is upgraded
- On a 90-day cadence for the production deployment

The Pillow project releases on a quarterly schedule (January 2, April 1, July 1, October 15). Each release frequently contains security fixes. Plan dependency upgrades to align with this schedule.
