# SafeguardMedia — Compliance & Security Reference

**Document type:** SOC 2 Audit Reference / Security & Compliance Documentation
**Audience:** External auditors, compliance officers, information security teams
**Classification:** Internal — Auditor Distribution
**Version:** 1.0
**Date:** 2026-03-20

---

## 1. Executive Summary

SafeguardMedia is a unified digital media forensics platform that analyses video,
audio, and image files for evidence of tampering, manipulation, or synthetic
generation. It is used by investigators, legal teams, and content verification
workflows to produce evidence-grade forensic analysis reports.

The platform integrates four independent forensic engines behind a single REST
API. It is designed around the principles of non-destructive analysis, evidence
integrity, auditability, and transparent scoring.

This document describes the platform's security architecture, data handling
practices, access controls, and compliance posture as they relate to SOC 2 Trust
Service Criteria.

---

## 2. SOC 2 Trust Service Criteria Mapping

### 2.1 Security (Common Criteria)

| Control Area | SafeguardMedia Implementation |
|---|---|
| **Access control** | API access is mediated through a single FastAPI gateway. CORS policies restrict permitted origins. All endpoints enforce file-type allowlists and file-size limits before any processing begins. Engine availability is verified at request time; unavailable engines return HTTP 503 rather than failing silently. |
| **Input validation** | Every endpoint validates file extension against a strict allowlist before reading the file body. File size is checked against configurable per-media-type limits (video: 500 MB, audio: 200 MB, image: 50 MB). Rejected files receive structured HTTP error responses (422, 413) and are never written to disk. |
| **Error handling** | All exceptions are caught by a layered handler chain: custom `SafeguardMediaException` subclasses (with typed error codes) and a global unhandled-exception handler that returns a structured JSON error body. Raw stack traces are never exposed to the client. All errors are logged server-side with full context. |
| **Secure file handling** | Uploaded files are written to a temporary directory with a randomised UUID filename (preventing path traversal and filename collision). Working copies are used for all analysis; original files are never modified. Temporary files are deleted in a `finally` block immediately after analysis completes, regardless of success or failure. |
| **Network security** | The API binds to a configurable port (default 8000) and supports CORS origin restriction. In production, `cors_origins` is configured to a specific allowlist (not `*`). Only `GET`, `POST`, and `OPTIONS` methods are permitted. |
| **Dependency isolation** | The three engine codebases (VFF, AFF, work) use overlapping module names. The engine registry uses `sys.path` isolation and `sys.modules` namespacing to load each engine in a sandboxed namespace, preventing cross-engine import collisions or unintended code execution. |

### 2.2 Availability

| Control Area | SafeguardMedia Implementation |
|---|---|
| **Health monitoring** | `GET /health` returns server status, API version, environment label, and an ISO 8601 timestamp. This endpoint is always available and responds independently of engine load. It is suitable for use as a load-balancer or orchestrator health check. |
| **Engine availability checks** | Each analysis endpoint checks engine availability before accepting work. If an engine failed to load at startup, the endpoint returns HTTP 503 ("Service Unavailable") with a descriptive message rather than accepting the file and failing during processing. |
| **Graceful degradation** | Individual forensic modules within an engine can skip gracefully when their preconditions are not met (e.g., no speech detected for the voice module, no mains hum for ENF analysis). Skipped modules are disclosed in the response, and their analytical weight is redistributed to the remaining modules. The system produces a result rather than failing. |
| **Resource limits** | File size limits are enforced before the file reaches the engine, preventing resource exhaustion from oversized uploads. Temporary files are cleaned up immediately after use. Configurable cleanup policies (max age, interval) govern residual file lifecycle. |
| **Startup validation** | At startup, the application validates all configuration via Pydantic Settings (type-checked, environment-variable-backed). Missing or malformed configuration causes a hard startup failure with a descriptive error, not a silent misconfiguration. |

### 2.3 Processing Integrity

| Control Area | SafeguardMedia Implementation |
|---|---|
| **Non-destructive analysis** | The platform never modifies the original uploaded file. All processing is performed on a working copy written to a dedicated temporary directory. The original file's SHA-256 hash is recorded before any analysis begins (VFF engine). |
| **Cryptographic integrity verification** | The VFF engine computes and records both SHA-256 and MD5 hashes of the submitted file prior to processing. These hashes are included in the analysis output, enabling independent verification that the file was not altered by the platform. |
| **Deterministic output** | Given identical input files, the platform produces identical hashes and consistent analysis scores. This has been verified through repeated submission of the same file. |
| **Multi-module corroboration** | No single forensic module determines the final verdict. All engines use a fusion layer that combines scores from multiple independent modules, applying corroboration weighting. When multiple modules independently flag the same anomaly, confidence is increased. When modules produce contradictory results, this is disclosed explicitly (`has_conflict`, `conflict_description`). |
| **Transparent scoring** | Every response includes per-module scores, the fused probability, the verdict, and the individual findings that contributed to the conclusion. There are no opaque or black-box outputs. Every verdict is traceable to specific measurements. |
| **Calibration disclosure** | All responses include a `calibration_note` field disclosing when default (pre-calibration) thresholds are in use. This prevents over-reliance on uncalibrated results and is a standard forensic disclosure practice. |
| **Unique case identification** | Each analysis is assigned a unique identifier (UUID). For audio analysis, the `case_id` is included in the response. For video analysis, the case object carries a unique identifier through the pipeline. This supports traceability and cross-referencing with external case management systems. |

### 2.4 Confidentiality

| Control Area | SafeguardMedia Implementation |
|---|---|
| **Data minimisation** | The platform stores only what is needed for the duration of analysis. Uploaded files are written to a temporary directory, processed, and deleted immediately after analysis completes. No long-term storage of submitted media files occurs by default. |
| **Temporary file lifecycle** | Files are written with randomised UUID filenames (e.g., `vff_<uuid>.mp4`) to a configurable upload directory. A `finally` block ensures deletion after processing, regardless of whether analysis succeeded or failed. A background cleanup policy (configurable via `CLEANUP_MAX_AGE_HOURS` and `CLEANUP_INTERVAL_SECONDS`) acts as a secondary safeguard against orphaned files. |
| **No external data transmission** | The platform does not transmit submitted files, analysis results, or any derived data to external services. All processing occurs locally on the host where the API is deployed. |
| **Structured error responses** | Error responses return typed error codes and human-readable messages. Internal implementation details, stack traces, file system paths, and engine internals are never exposed in client-facing responses. Detailed error context is logged server-side only. |
| **Output directory isolation** | Image and frame analysis results are written to job-specific output directories (`<output_dir>/<job_id>/`), preventing cross-job data leakage. |

### 2.5 Privacy

| Control Area | SafeguardMedia Implementation |
|---|---|
| **No PII extraction or storage** | The platform analyses media files for technical forensic signals (compression artefacts, noise patterns, frequency analysis). It does not perform facial recognition, speaker identification against a database, or any form of PII extraction. Voice analysis is limited to acoustic continuity checking (formant consistency), not identity matching. |
| **No user tracking** | The API does not require authentication tokens, session cookies, or user accounts in its current phase. No user behaviour is tracked, profiled, or stored. |
| **Metadata handling** | EXIF and file metadata are read and reported as part of the forensic analysis. This metadata may contain location data, device identifiers, or timestamps embedded by the originating device. This data is included in the analysis response but is not separately stored, indexed, or processed beyond the scope of the individual analysis request. |

---

## 3. System Architecture — Security View

### 3.1 Request Lifecycle

```
Client Request
    │
    ▼
┌─────────────────────────────┐
│  FastAPI Gateway             │
│  ├─ CORS enforcement        │
│  ├─ File extension allowlist │
│  ├─ File size limit check   │
│  └─ Engine availability check│
└─────────────┬───────────────┘
              │ (only validated requests proceed)
              ▼
┌─────────────────────────────┐
│  Temp File Write             │
│  UUID-randomised filename    │
│  Isolated upload directory   │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Forensic Engine             │
│  ├─ Hash computation (pre)  │
│  ├─ Multi-module analysis   │
│  ├─ Fusion & corroboration  │
│  └─ Structured result       │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Response Serialisation      │
│  Structured JSON, no traces  │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Cleanup (finally block)     │
│  Temp file deleted           │
│  Output dir cleaned          │
└─────────────────────────────┘
```

### 3.2 Endpoint Summary

| Endpoint | Method | Input | Validation | Size Limit |
|---|---|---|---|---|
| `/health` | GET | None | None | N/A |
| `/api/v1/video/analyze` | POST | Video file | `.mp4`, `.mov`, `.avi`, `.mkv`, `.mts`, `.m4v` | 500 MB |
| `/api/v1/audio/analyze` | POST | Audio file | `.wav`, `.mp3`, `.aac`, `.m4a`, `.ogg`, `.flac`, `.mp4`, `.3gp`, `.amr` | 200 MB |
| `/api/v1/image/analyze` | POST | Image file | `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tiff`, `.tif` | 50 MB |
| `/api/v1/frames/analyze` | POST | Video file | `.mp4`, `.mov`, `.avi`, `.mkv`, `.mts`, `.m4v` | 500 MB |

### 3.3 HTTP Status Codes

| Code | Condition | Response Body |
|---|---|---|
| `200` | Analysis completed successfully | Structured JSON analysis result |
| `413` | File exceeds size limit | `{"error": "File too large (...)", "code": "FILE_TOO_LARGE"}` |
| `422` | Unsupported file type or missing file | `{"error": "Unsupported file type: ...", "code": "UNSUPPORTED_FILE_TYPE"}` |
| `500` | Engine processing error | `{"error": "Internal server error", "code": "INTERNAL_ERROR"}` |
| `503` | Engine not available | Descriptive detail of unavailable engine |

---

## 4. Data Handling & Retention

### 4.1 Data Flow

| Stage | Data | Storage | Duration | Deletion Mechanism |
|---|---|---|---|---|
| Upload | Raw media file | Temp directory (`/tmp/safeguardmedia/uploads/`) | Duration of analysis only | `finally` block (immediate) |
| Processing | Working copy, intermediate artefacts | Temp directory, output directory | Duration of analysis only | `finally` block + scheduled cleanup |
| Response | Analysis result (JSON) | In-memory only | Duration of HTTP response | Not persisted |
| Logs | Structured log entries | Server stdout/log files | Per host log rotation policy | Host-level log management |

### 4.2 What Is NOT Stored

- Original media files are not retained after analysis.
- Analysis results are not persisted to a database in the current phase.
- No user credentials, session tokens, or authentication data are stored.
- No PII is extracted, stored, or indexed.

### 4.3 Configurable Retention Controls

| Setting | Default | Purpose |
|---|---|---|
| `UPLOAD_DIR` | `/tmp/safeguardmedia/uploads` | Location for temporary file storage |
| `OUTPUT_DIR` | `/tmp/safeguardmedia/outputs` | Location for engine output artefacts |
| `CLEANUP_MAX_AGE_HOURS` | 24 | Maximum age for orphaned temp files |
| `CLEANUP_INTERVAL_SECONDS` | 3600 | Interval between cleanup sweeps |

---

## 5. Evidence Integrity & Chain of Custody

These controls support the use of SafeguardMedia output in investigative,
legal, and evidentiary contexts.

### 5.1 Pre-Analysis Hashing

The VFF engine computes SHA-256 and MD5 hashes of the original file before
any processing begins. These hashes are included in the analysis response,
enabling independent verification that:

- The file analysed is the file that was submitted.
- The platform did not modify the file during analysis.
- The same file, if submitted again, produces the same hash (deterministic).

### 5.2 Non-Destructive Processing

All analysis is performed on a working copy. The original file is never
opened for writing, modified, re-encoded, or altered in any way. This
preserves the forensic integrity of the evidence.

### 5.3 Audit Trail (VFF Engine)

The VFF engine maintains a tamper-evident audit trail for each case:

- Every operation performed on the file is logged with timestamps.
- The identity of each analysis module run is recorded.
- The outcome of each processing step is captured.
- Audit logs are signed with HMAC to detect post-hoc modification.
- Each case is assigned a unique UUID linking the audit log, analysis result,
  and any generated reports.

### 5.4 Traceability

Every finding in a SafeguardMedia report is traceable to:

- The specific forensic module that produced it.
- The measurement or signal that triggered the finding.
- The severity classification applied.
- The confidence level of the individual finding.
- The temporal location within the file (where applicable, with start/end
  timestamps to sub-second precision).

### 5.5 Verdict Transparency

No verdict is opaque. Every response includes:

- Per-module scores showing what each forensic technique found.
- The fused probability (the numerical basis for the verdict).
- The corroboration factor (how much inter-module agreement boosted or
  reduced confidence).
- The list of skipped modules and the reasons for skipping.
- A calibration note disclosing threshold status.
- A conflict flag and description when modules disagree.

---

## 6. Forensic Engine Security Controls

### 6.1 Engine Isolation

The platform loads three independent engine codebases (VFF, AFF, work)
that share overlapping internal module names (`core`, `modules`). To prevent
cross-engine code injection or namespace collision:

- Each engine is loaded in sequence with isolated `sys.path` manipulation.
- After loading, each engine's `sys.modules` entries are namespaced
  (e.g., `core.*` becomes `vff.core.*`) and the original namespace is freed.
- The `sys.path` is restored after each engine loads.
- Engine references are stored as module-level globals and accessed only
  through the engine registry.

### 6.2 Module Graceful Degradation

Forensic modules that cannot run due to preconditions (e.g., no mains hum
for ENF analysis, no speech for voice analysis, insufficient frames for
PRNU) skip gracefully rather than raising exceptions. This behaviour:

- Prevents denial-of-service through specially crafted input files designed
  to trigger module failures.
- Ensures the platform always returns a result, even for unusual inputs.
- Discloses skipped modules explicitly in the response so that consumers
  understand the scope of the analysis performed.

### 6.3 File Type Enforcement

File type validation is performed at the router level using extension
allowlists, before the file body is written to disk. This provides a
first line of defence against:

- Arbitrary file upload attacks.
- Attempts to execute code via disguised file uploads.
- Resource exhaustion from unsupported file types that the engines
  cannot process efficiently.

Each endpoint has an independent allowlist appropriate to its media type.

---

## 7. Logging & Monitoring

### 7.1 Structured Logging

All log output follows a consistent format:

```
YYYY-MM-DD HH:MM:SS | LEVEL    | module.name | message
```

Log entries include:

- Startup configuration (environment, directories, engine load status).
- Per-request processing events (file received, analysis started, analysis
  completed or failed).
- Exception details with full context (logged server-side, never exposed
  to clients).
- Engine registry status on startup (which engines loaded, which failed).

### 7.2 Noisy Logger Suppression

Third-party loggers (`uvicorn.access`, `celery`) are suppressed to WARNING
level to prevent log flooding and ensure that SafeguardMedia application
logs are the dominant signal in the log stream.

### 7.3 Error Logging

All unhandled exceptions are captured by the global exception handler and
logged with `logger.exception()`, which includes the full stack trace in
the server log while returning only a sanitised error response to the client.

---

## 8. Configuration Management

### 8.1 Environment-Based Configuration

All configuration is managed through environment variables, read via
Pydantic Settings with type validation. This ensures:

- No hardcoded secrets or credentials in source code.
- Configuration is validated at startup — missing or malformed values
  cause an immediate, descriptive startup failure.
- A documented `.env.example` file lists all configurable settings.
- Configuration changes require a server restart, preventing runtime
  configuration injection.

### 8.2 Configuration Parameters

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `ENV` | string | `development` | Environment label (development/production) |
| `PORT` | integer | `8000` | API bind port |
| `REDIS_URL` | string | `redis://localhost:6379` | Redis connection for async task queue |
| `UPLOAD_DIR` | string | `/tmp/safeguardmedia/uploads` | Temporary upload storage |
| `OUTPUT_DIR` | string | `/tmp/safeguardmedia/outputs` | Engine output storage |
| `MAX_VIDEO_SIZE_MB` | integer | `500` | Maximum video upload size |
| `MAX_AUDIO_SIZE_MB` | integer | `200` | Maximum audio upload size |
| `MAX_IMAGE_SIZE_MB` | integer | `50` | Maximum image upload size |
| `CLEANUP_MAX_AGE_HOURS` | integer | `24` | Max age for orphaned temp files |
| `CLEANUP_INTERVAL_SECONDS` | integer | `3600` | Cleanup sweep interval |
| `CORS_ORIGINS` | list | `["*"]` | Allowed CORS origins (restricted in production) |

---

## 9. Testing & Validation

### 9.1 Test Coverage

The platform has been validated through:

- **Automated unit tests** — health endpoint tests verifying response shape,
  HTTP status, and timestamp format (pytest + httpx async client).
- **Manual integration tests** — all four analysis endpoints tested with
  real media files across happy paths, edge cases, negative tests, and
  response quality checks. Full results documented in `TESTING_GUIDE.md`.

### 9.2 Test Categories

| Category | Scope | Coverage |
|---|---|---|
| Happy path | Standard files in all accepted formats | All four endpoints |
| Response validation | Correct top-level keys, value types, ranges | All four endpoints |
| Edge cases | Short files, large files, no audio/speech, WhatsApp-compressed files, AI-generated content | All four endpoints |
| Negative tests | Wrong file types, empty files, corrupted files, missing files, oversized files | All four endpoints |
| Response quality | Verdict accuracy for known-authentic and known-tampered files | All four endpoints |
| Cross-endpoint | HTTP behaviour, concurrent requests, server stability, health endpoint | Platform-wide |

### 9.3 Validation Artefacts

- `docs/TESTING_GUIDE.md` — full manual testing checklist (all items passed)
- `tests/test_health.py` — automated health endpoint tests (3/3 passing)
- `tests/conftest.py` — async test client fixture for httpx/ASGI testing

---

## 10. Known Limitations & Disclosure

The following limitations are disclosed in all formal uses of the platform's
output and are relevant to the scope of any compliance assertion:

1. **Pre-calibration thresholds.** Default thresholds have not been tuned to
   a specific corpus of known authentic and tampered media. Results produced
   with default thresholds should be treated as investigative leads rather
   than definitive conclusions. This is disclosed in every response via the
   `calibration_note` field.

2. **No authentication layer (Phase 1).** The current phase does not
   implement user authentication, API keys, or role-based access control.
   Access control is expected to be provided by the deployment environment
   (network segmentation, reverse proxy, API gateway). Authentication is
   planned for a future phase.

3. **No encryption at rest.** Temporary files are stored unencrypted on the
   local filesystem. The short retention period (deleted immediately after
   analysis, with a 24-hour cleanup backstop) mitigates this risk.
   Encryption at rest should be provided by the host environment (e.g.,
   encrypted volumes) for deployments handling sensitive media.

4. **No TLS termination.** The FastAPI application serves HTTP. TLS must be
   provided by a reverse proxy (e.g., nginx, Caddy, cloud load balancer)
   in production deployments.

5. **Results are probabilistic.** All verdicts are probabilistic assessments,
   not absolute determinations. Confidence levels and caveats are included
   in every response. Expert interpretation is required for formal or legal
   use.

6. **AI-generated content detection is evolving.** Sophisticated generative
   models may produce content that scores as authentic on current metrics.
   The platform's detection capability is expected to improve with
   calibration and model updates.

---

## 11. Incident Response Considerations

| Scenario | Platform Behaviour |
|---|---|
| Malformed file upload | Rejected at validation layer. File never written to disk. HTTP 422 returned. |
| Oversized file upload | Rejected after size check. HTTP 413 returned. |
| Engine crash during analysis | Exception caught, logged with full context. HTTP 500 with sanitised message returned. Temporary file cleaned up via `finally` block. Server remains operational for subsequent requests. |
| Engine unavailable at startup | Engine marked as unavailable in registry. Subsequent requests to that engine's endpoint return HTTP 503. Other engines continue to function. |
| Concurrent request interference | Each request operates on an independent UUID-named temporary file and (where applicable) a job-specific output directory. No shared mutable state between requests. |
| Orphaned temporary files | Background cleanup policy deletes files older than `CLEANUP_MAX_AGE_HOURS`. |

---

## 12. Glossary of Security-Relevant Terms

| Term | Definition |
|---|---|
| **Allowlist** | A strict list of permitted values (file extensions, CORS origins). Anything not on the list is rejected. |
| **Chain of custody** | The documented, unbroken trail of handling for a piece of evidence, proving it was not altered between collection and presentation. |
| **CORS** | Cross-Origin Resource Sharing — a browser security mechanism that restricts which origins can make requests to the API. |
| **HMAC** | Hash-based Message Authentication Code — a cryptographic mechanism used to verify that audit logs have not been tampered with. |
| **Non-destructive analysis** | Analysis that does not modify the original evidence file in any way. |
| **SHA-256** | A cryptographic hash function producing a 256-bit (64 hex character) digest. Used for file integrity verification. |
| **UUID** | Universally Unique Identifier — a 128-bit identifier used to prevent filename collisions and enable case traceability. |

---

## 13. Document Control

| Field | Value |
|---|---|
| Document title | SafeguardMedia — Compliance & Security Reference |
| Version | 1.0 |
| Date | 2026-03-20 |
| Classification | Internal — Auditor Distribution |
| Owner | SafeguardMedia Engineering |
| Review cycle | Updated with each major platform phase |
| Related documents | `FEATURE_DOCUMENT.md`, `IMPLEMENTATION_PLAN.md`, `TESTING_GUIDE.md` |

---

_SafeguardMedia — Built for forensic professionals. Designed for auditability._
