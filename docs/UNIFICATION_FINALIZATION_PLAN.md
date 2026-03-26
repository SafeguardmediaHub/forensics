# SafeguardMedia — Unification Finalization Plan

A living checklist for turning the current engines into one usable product.

Status markers:
- `[ ]` not started
- `[~]` in progress
- `[x]` done
- `[-]` deferred

---

## Objective

Ship one stable forensic product for container use, with:
- one public Python service: `safeguardmedia`
- one public contract for the Node.js backend
- existing engines preserved behind wrappers:
  - `VFF/` for video forensics
  - `AFF/` for audio forensics
  - `work/` for image forensics and frame analysis

Core rule:
- do not deeply rewrite the engine internals during finalization
- wrap and orchestrate them instead

---

## Target Architecture

Public entrypoint:
- `safeguardmedia-api`

Private execution services:
- `engine-vff`
- `engine-aff`
- `engine-work`

Background execution:
- `safeguardmedia-worker`
- `redis`

Routing map:
- `video` -> `engine-vff`
- `audio` -> `engine-aff`
- `image` -> `engine-work`
- `frames` -> `engine-work`

Non-goals for this phase:
- no deep shared-engine refactor
- no engine logic rewrite for cleanliness
- no direct Node.js calls to engine scripts

---

## Phase 0 — Freeze Direction

Goal: lock the integration strategy before more code changes are made.

### 0.1 Decisions To Lock

These decisions should be treated as architecture decisions, not temporary guesses.

- [x] Decision: `safeguardmedia` is the only public Python service
- [x] Decision: Node.js talks only to `safeguardmedia`
- [x] Decision: `VFF`, `AFF`, and `work` remain private engine implementations
- [x] Decision: engines are wrapped and orchestrated, not deeply merged
- [x] Decision: the first shippable product favors behavior preservation over internal cleanup
- [x] Decision: one public contract matters more than one shared runtime

### 0.2 Service Boundary Approval

- [x] Approve `safeguardmedia-api` as the only public API surface
- [x] Approve `safeguardmedia-worker` as the only async execution layer
- [x] Approve `engine-vff` as private/internal only
- [x] Approve `engine-aff` as private/internal only
- [x] Approve `engine-work` as private/internal only
- [x] Approve Redis as the initial job-state backend

### 0.3 Explicit Non-Goals For Finalization

These items should not block shipping unless the team explicitly reopens scope.

- [x] Non-goal confirmed: no deep engine cleanup before stabilization
- [x] Non-goal confirmed: no attempt to make all engine internals look the same
- [x] Non-goal confirmed: no direct Node.js invocation of Python scripts in production flow
- [x] Non-goal confirmed: no new public engine-specific APIs
- [x] Non-goal confirmed: no frontend polish work before the backend path is stable

- [x] Confirm that `safeguardmedia` is the only public Python-facing product surface
- [x] Confirm that Node.js will call only `safeguardmedia`, never `VFF`, `AFF`, or `work` directly
- [x] Confirm that engine internals will remain isolated behind wrappers
- [x] Confirm that the finalization path is wrapper/orchestrator-based, not deep engine merging
- [x] Confirm the target service layout:
  - `safeguardmedia-api`
  - `safeguardmedia-worker`
  - `engine-vff`
  - `engine-aff`
  - `engine-work`
  - `redis`

Exit criteria:
- the team agrees on the architecture and stops making conflicting structural changes

---

## Phase 1 — Define Contracts

Goal: fix the external and internal interfaces before implementation spreads.

### 1.0 Contract Decisions To Lock

- [x] Decide whether the public API is single-endpoint (`POST /api/v1/analyze`) or per-media-type
- [ ] Decide whether all requests become async, or only heavy media types
- [x] Decide whether uploads are sent from Node.js as multipart form-data
- [ ] Decide whether internal engine services accept uploaded files or shared file paths
- [x] Decide whether job results live in Redis only for v1, or need database persistence now

Recommended v1 choices:
- [x] Recommended: one public submit endpoint
- [x] Recommended: async for `video` and `frames`
- [x] Recommended: allow `audio` to be async if runtime is variable
- [x] Recommended: image may stay sync only if it is consistently fast
- [x] Recommended: Redis-only result storage for v1

### 1.1 Public API Approval Checklist

- [x] Approve request field: `media_type`
- [x] Approve request field: uploaded `file`
- [x] Approve optional request field: `options`
- [x] Approve response field: `job_id`
- [x] Approve response field: `status`
- [x] Approve response field: `media_type`
- [x] Approve response field: `result`
- [x] Approve response field: `error`

### 1.2 Unified Result Schema Approval Checklist

These are the minimum normalized fields `safeguardmedia` should always return.

- [x] Approve `engine`
- [x] Approve `verdict`
- [x] Approve `verdict_label`
- [x] Approve `probability`
- [x] Approve `confidence`
- [x] Approve `findings`
- [x] Approve `summary`
- [x] Approve `file.filename`
- [x] Approve `file.sha256` when available
- [x] Approve `engine_detail` for raw engine-specific output

### 1.3 Job State Approval Checklist

- [x] Approve `pending`
- [x] Approve `running`
- [x] Approve `completed`
- [x] Approve `failed`
- [x] Approve required timestamps: `submitted_at`
- [ ] Decide whether `started_at` is required in v1
- [ ] Decide whether `completed_at` is required in v1

### 1.4 Internal Wrapper Contract Approval Checklist

- [ ] Approve internal VFF endpoint shape
- [ ] Approve internal AFF endpoint shape
- [ ] Approve internal image endpoint shape
- [ ] Approve internal frames endpoint shape
- [ ] Approve minimum internal success payload structure
- [ ] Approve minimum internal failure payload structure
- [ ] Approve timeout expectations per engine
- [ ] Approve file cleanup responsibility between caller and engine wrapper

### 1.1 External API Contract
- [x] Define `POST /api/v1/analyze`
- [x] Define `GET /api/v1/jobs/{job_id}`
- [x] Define `GET /api/v1/health`
- [ ] Decide whether all media types are async, or only heavy ones
- [x] Define the top-level result schema returned to Node.js

Required top-level fields:
- [x] `job_id`
- [x] `status`
- [x] `media_type`
- [x] `engine`
- [x] `verdict`
- [x] `verdict_label`
- [x] `probability`
- [x] `confidence`
- [x] `findings`
- [x] `summary`
- [x] `file`
- [x] `engine_detail`
- [x] `error`

### 1.2 Internal Engine Contract
- [ ] Define the private internal endpoint for `engine-vff`
- [ ] Define the private internal endpoint for `engine-aff`
- [ ] Define the private internal endpoint(s) for `engine-work`
- [ ] Decide whether internal wrappers receive uploaded files or file paths
- [ ] Standardize internal error payloads enough for `safeguardmedia` to map them cleanly

Exit criteria:
- API contract is written down and stable enough for Node.js and Python work to proceed independently

---

## Phase 2 — Build Regression Baseline

Goal: preserve current working behavior before wrapping and containerizing.

### 2.0 Baseline Strategy Decisions To Lock

- [ ] Decide the minimum number of sample files per engine for v1 regression coverage
- [x] Decide where baseline outputs will be stored
- [x] Decide whether baseline outputs are committed to the repo or kept externally
- [x] Decide which output differences are acceptable versus release-blocking
- [ ] Decide who signs off when wrapped output is "close enough"

Recommended v1 baseline scope:
- [ ] Recommended: at least 3 representative files for `VFF`
- [ ] Recommended: at least 3 representative files for `AFF`
- [ ] Recommended: at least 3 representative files for image forensics
- [ ] Recommended: at least 3 representative files for frame analysis
- [ ] Recommended: include at least one authentic and one suspicious/tampered sample per engine where possible

### 2.1 Regression Corpus Approval Checklist

- [x] Approve VFF happy-path sample file(s)
- [ ] Approve VFF edge-case sample file(s)
- [x] Approve AFF happy-path sample file(s)
- [x] Approve AFF edge-case sample file(s)
- [x] Approve image happy-path sample file(s)
- [ ] Approve image edge-case sample file(s)
- [x] Approve frame-analysis happy-path sample file(s)
- [ ] Approve frame-analysis edge-case sample file(s)

### 2.2 Baseline Capture Approval Checklist

For each approved sample:
- [x] Record how the current engine is run directly
- [x] Record the raw output
- [x] Record the normalized comparison fields
- [x] Record expected verdict
- [ ] Record expected top findings
- [ ] Record expected module scores where relevant
- [ ] Record expected skipped modules where relevant
- [ ] Record expected file hash where relevant

### 2.3 Comparison Rules Approval Checklist

- [x] Decide which fields must match exactly
- [x] Decide which fields may differ slightly
- [x] Decide acceptable float tolerances for scores/probabilities
- [x] Decide how to compare findings when ordering changes
- [x] Decide how to compare outputs for engines with non-deterministic fields like timestamps or IDs
- [x] Decide what constitutes a regression severe enough to stop cutover

### 2.4 Regression Workflow Checklist

- [x] Define a repeatable process for capturing baseline outputs
- [x] Define a repeatable process for running wrapped outputs
- [x] Define where comparison results will be recorded
- [ ] Define who reviews failures
- [ ] Define how baseline updates are approved if engine behavior legitimately changes

### 2.1 Test Corpus
- [x] Select known-good video files for VFF
- [x] Select known-good audio files for AFF
- [x] Select known-good image files for image forensics
- [x] Select known-good video files for frame analysis
- [x] Record where these files live in the repo or shared test storage

### 2.2 Baseline Outputs
- [x] Capture current direct-run output for VFF
- [x] Capture current direct-run output for AFF
- [x] Capture current direct-run output for image forensics
- [ ] Capture current direct-run output for frame analysis

Track at least:
- [ ] verdict
- [ ] confidence/probability
- [ ] key findings
- [ ] module scores where available
- [ ] file hash where available
- [ ] skip behavior where available

### 2.3 Comparison Rules
- [x] Decide what counts as an acceptable match between old and wrapped outputs
- [x] Decide which fields must match exactly
- [x] Decide which fields may vary slightly

Exit criteria:
- the team can prove whether wrapping broke existing behavior

---

## Phase 3 — Wrap Engines Without Rewriting Them

Goal: make each engine callable in a stable, isolated way.

### 3.0 Wrapper Strategy Decisions To Lock

- [x] Decide wrapper style for VFF
- [x] Decide wrapper style for AFF
- [x] Decide wrapper style for `work`
- [ ] Decide whether wrapper input is file upload or file path
- [ ] Decide whether wrapper output is raw engine output or lightly structured raw output
- [ ] Decide timeout and resource expectations per wrapper

Preferred order of execution strategies:
- [x] Preferred: use the least invasive method that preserves current behavior
- [x] Preferred: direct import only if reliable and isolated
- [x] Acceptable: subprocess invocation if import isolation is risky
- [x] Preferred: avoid deep code movement into `safeguardmedia` during this phase

### 3.1 Wrapper Responsibility Checklist

Each wrapper should only do these things:
- [ ] parse request/input
- [ ] create temp working path if needed
- [ ] invoke the existing engine
- [ ] collect structured output
- [ ] map runtime failures into a stable error shape
- [ ] clean up wrapper-owned temp files

Each wrapper should not do these things:
- [ ] not reimplement forensic logic
- [ ] not normalize the final public schema
- [ ] not own job orchestration
- [ ] not expose a public-facing product contract to Node.js

### 3.2 VFF Wrapper Approval Checklist

- [x] VFF wrapper execution mode chosen
- [x] VFF wrapper input shape approved
- [x] VFF wrapper output shape approved
- [ ] VFF wrapper temp-file handling approved
- [ ] VFF wrapper error behavior approved
- [x] VFF wrapper passes baseline comparison on approved sample files

### 3.3 AFF Wrapper Approval Checklist

- [x] AFF wrapper execution mode chosen
- [x] AFF wrapper input shape approved
- [x] AFF wrapper output shape approved
- [ ] AFF wrapper temp-file handling approved
- [ ] AFF wrapper error behavior approved
- [x] AFF wrapper passes baseline comparison on approved sample files

### 3.4 Work Wrapper Approval Checklist

- [x] `work` wrapper execution mode chosen
- [x] image wrapper input shape approved
- [x] image wrapper output shape approved
- [x] frames wrapper input shape approved
- [x] frames wrapper output shape approved
- [ ] `work` wrapper temp-file handling approved
- [ ] `work` wrapper error behavior approved
- [x] image wrapper passes baseline comparison on approved sample files
- [ ] frames wrapper passes baseline comparison on approved sample files

### 3.5 Isolation and Stability Checklist

- [ ] Verify wrapper can run without depending on `safeguardmedia` internals
- [ ] Verify engine-specific dependencies remain isolated
- [ ] Verify wrapper can fail without crashing the whole integration path
- [ ] Verify wrapper logs enough detail for debugging
- [ ] Verify wrapper startup assumptions are documented

### 3.6 Exit Gate For Wrapper Completion

- [x] `engine-vff` callable in isolation
- [x] `engine-aff` callable in isolation
- [x] `engine-work` callable in isolation
- [ ] all wrappers pass agreed regression checks
- [ ] no wrapper has introduced engine-logic drift that the team has not approved

### 3.1 VFF Wrapper
- [x] Decide VFF execution mode:
  - direct import
  - subprocess
  - internal HTTP wrapper
- [x] Build thin VFF wrapper
- [ ] Ensure wrapper handles temp files safely
- [x] Ensure wrapper returns structured JSON
- [x] Validate wrapper against regression corpus

### 3.2 AFF Wrapper
- [x] Decide AFF execution mode
- [x] Build thin AFF wrapper
- [ ] Ensure wrapper handles temp files safely
- [x] Ensure wrapper returns structured JSON
- [x] Validate wrapper against regression corpus

### 3.3 Work Wrapper
- [x] Decide `work` execution mode
- [x] Build image forensics wrapper endpoint
- [x] Build frame analysis wrapper endpoint
- [ ] Ensure wrapper handles temp files safely
- [x] Ensure wrapper returns structured JSON
- [ ] Validate wrapper against regression corpus

Exit criteria:
- each engine works behind a stable wrapper without changing core forensic logic

---

## Phase 4 — Turn SafeguardMedia Into the Orchestrator

Goal: make `safeguardmedia` coordinate engines instead of hosting fragile deep integrations.

### 4.0 Orchestrator Scope Decisions To Lock

- [x] Decide that `safeguardmedia` owns request validation
- [x] Decide that `safeguardmedia` owns routing and dispatch
- [x] Decide that `safeguardmedia` owns public response normalization
- [x] Decide that `safeguardmedia` owns public error mapping
- [x] Decide that `safeguardmedia` does not own engine-specific forensic logic

### 4.1 Request Intake Approval Checklist

- [x] Approve how `media_type` is validated
- [x] Approve file-extension validation rules
- [x] Approve file-size validation rules
- [x] Approve request rejection behavior for bad input
- [x] Approve how optional `options` are passed through

### 4.2 Routing Approval Checklist

- [x] Approve routing logic for `video`
- [x] Approve routing logic for `audio`
- [x] Approve routing logic for `image`
- [x] Approve routing logic for `frames`
- [ ] Approve behavior when a target engine is unavailable

### 4.3 Normalization Approval Checklist

- [x] Approve normalized VFF mapping
- [x] Approve normalized AFF mapping
- [x] Approve normalized image mapping
- [x] Approve normalized frame-analysis mapping
- [x] Approve preserved raw output under `engine_detail`
- [x] Approve public summary field generation strategy

### 4.4 Public Error Mapping Approval Checklist

- [x] Approve invalid-request error shape
- [x] Approve unsupported-media error shape
- [x] Approve file-too-large error shape
- [ ] Approve unavailable-engine error shape
- [x] Approve engine-runtime-failure error shape
- [x] Approve timeout error shape

### 4.5 Orchestrator Exit Gate

- [x] `safeguardmedia` can accept one request format for all media types
- [x] `safeguardmedia` routes correctly to all target engines
- [x] `safeguardmedia` returns one public result shape
- [x] Node.js no longer needs engine-specific knowledge to consume results

### 4.1 Routing and Dispatch
- [x] Accept one unified analysis request format
- [x] Route `video` requests to `engine-vff`
- [x] Route `audio` requests to `engine-aff`
- [x] Route `image` requests to `engine-work`
- [x] Route `frames` requests to `engine-work`

### 4.2 Response Normalization
- [x] Map VFF raw output into the unified result schema
- [x] Map AFF raw output into the unified result schema
- [x] Map image raw output into the unified result schema
- [x] Map frame analysis raw output into the unified result schema
- [x] Preserve full raw output under `engine_detail`

### 4.3 Error Handling
- [x] Standardize engine failure mapping
- [x] Standardize unsupported-file responses
- [x] Standardize size-limit responses
- [ ] Standardize unavailable-engine responses

Exit criteria:
- Node.js can call one service and get one schema regardless of engine used

---

## Phase 5 — Add Async Job Execution

Goal: move heavy work out of the request path.

### 5.0 Async Model Decisions To Lock

- [x] Decide which media types are async in v1
- [x] Decide whether any media type remains synchronous
- [ ] Decide maximum acceptable request time for synchronous work
- [ ] Decide worker retry policy
- [ ] Decide timeout policy per media type

Recommended v1:
- [x] Recommended: `video` async
- [x] Recommended: `frames` async
- [ ] Recommended: `audio` async if runtime varies materially
- [x] Recommended: `image` sync only if consistently fast and simple

### 5.1 Celery Ownership Checklist

- [x] Approve that only `safeguardmedia` owns Celery
- [x] Approve that engine wrappers do not own public async workflows
- [ ] Approve worker queue names or queue structure
- [x] Approve task payload format
- [x] Approve result payload format stored after completion

### 5.2 Job Lifecycle Approval Checklist

- [x] Approve transition: `pending` -> `running`
- [x] Approve transition: `running` -> `completed`
- [x] Approve transition: `running` -> `failed`
- [ ] Approve retry behavior after transient failure
- [x] Approve user-visible behavior for terminal failures

### 5.3 Result Storage Approval Checklist

- [x] Approve Redis as job-state store
- [ ] Approve TTL for completed jobs
- [ ] Approve TTL for failed jobs
- [x] Approve what metadata is stored with jobs
- [ ] Approve whether raw engine output is stored or only normalized output

### 5.4 Async Exit Gate

- [x] heavy media types no longer block the API
- [x] job polling works reliably
- [x] failure states are visible and stable
- [ ] worker behavior is predictable enough for container deployment

### 5.1 Job Model
- [x] Define `pending`
- [x] Define `running`
- [x] Define `completed`
- [x] Define `failed`
- [x] Define required job metadata fields

### 5.2 Worker Execution
- [x] Add one Celery layer in `safeguardmedia` only
- [x] Queue `video` analysis
- [x] Queue `frames` analysis
- [ ] Decide whether `audio` should also be queued
- [x] Decide whether `image` remains sync or is also queued

### 5.3 Job Storage
- [x] Store job state in Redis
- [x] Store normalized result in Redis
- [x] Store failure payload in Redis
- [ ] Add TTL/expiry rules for old jobs

### 5.4 Polling
- [x] Implement `GET /api/v1/jobs/{job_id}`
- [x] Ensure Node.js can poll until completion
- [x] Ensure failed jobs return useful error payloads

Exit criteria:
- long-running analyses no longer block the public API

---

## Phase 6 — Standardize File Handling

Goal: make uploads, temp files, and cleanup predictable across services.

### 6.0 File Lifecycle Decisions To Lock

- [x] Decide whether uploaded files are forwarded or shared by path
- [x] Decide where uploads land first
- [x] Decide which service owns the original temp upload
- [x] Decide which service owns derived artifacts
- [x] Decide cleanup responsibility for success
- [x] Decide cleanup responsibility for failure

### 6.1 Upload Handling Approval Checklist

- [x] Approve upload directory strategy
- [x] Approve file naming strategy
- [x] Approve collision-avoidance strategy
- [x] Approve path traversal protections
- [x] Approve max-size enforcement point

### 6.2 Worker/Engine Access Approval Checklist

- [x] Approve how workers access uploaded files
- [x] Approve how engine wrappers access uploaded files
- [ ] Approve whether a shared volume is required
- [x] Approve whether per-job output directories are required

### 6.3 Cleanup Approval Checklist

- [x] Approve cleanup after successful sync execution
- [x] Approve cleanup after successful async execution
- [x] Approve cleanup after failed sync execution
- [x] Approve cleanup after failed async execution
- [x] Approve orphan-file cleanup strategy

### 6.4 File Handling Exit Gate

- [x] file ownership is unambiguous
- [x] cleanup behavior is documented and implemented
- [x] workers and engines can access the files they need without ad hoc hacks

- [x] Choose shared temp storage strategy
- [x] Decide whether API uploads are passed by file path or forwarded by file upload
- [x] Standardize temp file naming
- [x] Standardize cleanup on success
- [x] Standardize cleanup on failure
- [x] Ensure workers can access the files they need
- [x] Ensure old temp files can be cleaned safely

Exit criteria:
- file lifecycle is reliable across API, worker, and engine services

---

## Phase 7 — Containerize the Stack

Goal: make the product deployable and usable from the other application.

### 7.0 Container Decisions To Lock

- [ ] Decide base image strategy for `safeguardmedia`
- [ ] Decide base image strategy for `engine-vff`
- [ ] Decide base image strategy for `engine-aff`
- [ ] Decide base image strategy for `engine-work`
- [ ] Decide whether all services share a common build base or not
- [ ] Decide which services are exposed publicly

### 7.1 Service Packaging Approval Checklist

- [ ] Approve Dockerfile for `safeguardmedia-api`
- [ ] Approve Dockerfile for `safeguardmedia-worker`
- [ ] Approve Dockerfile for `engine-vff`
- [ ] Approve Dockerfile for `engine-aff`
- [ ] Approve Dockerfile for `engine-work`
- [ ] Approve Redis service definition

### 7.2 Runtime Wiring Approval Checklist

- [ ] Approve internal network layout
- [ ] Approve shared volume strategy
- [ ] Approve environment variable layout
- [ ] Approve health checks per service
- [ ] Approve service startup dependencies

### 7.3 Local Stack Exit Gate

- [ ] full stack boots locally with Docker Compose
- [ ] internal engine services are reachable only where intended
- [ ] public request path works end-to-end in containers
- [ ] worker path works end-to-end in containers

### 7.1 Service Definition
- [ ] Create container definition for `safeguardmedia-api`
- [ ] Create container definition for `safeguardmedia-worker`
- [ ] Create container definition for `engine-vff`
- [ ] Create container definition for `engine-aff`
- [ ] Create container definition for `engine-work`
- [ ] Add Redis service

### 7.2 Network and Storage
- [ ] Put engine services on internal network only
- [ ] Expose only the intended public service(s)
- [ ] Configure shared storage if required
- [ ] Configure environment variables per service

### 7.3 Local Orchestration
- [ ] Add Docker Compose for the full stack
- [ ] Verify local boot order
- [ ] Verify health checks

Exit criteria:
- the full stack runs locally in containers end-to-end

---

## Phase 8 — End-to-End Verification

Goal: prove the unified product works before cutover.

### 8.0 Verification Decisions To Lock

- [ ] Decide which tests are release-blocking
- [ ] Decide which regressions are tolerated temporarily
- [ ] Decide who signs off on product readiness
- [ ] Decide what evidence of parity is required before cutover

### 8.1 Release-Blocking Verification Checklist

- [ ] wrapped VFF parity confirmed
- [ ] wrapped AFF parity confirmed
- [ ] wrapped image parity confirmed
- [ ] wrapped frame-analysis parity confirmed
- [ ] unified API contract verified
- [ ] async job flow verified
- [ ] containerized flow verified

### 8.2 Operational Verification Checklist

- [ ] concurrent requests behave correctly
- [ ] one engine failure does not cascade across the stack
- [ ] failed jobs remain inspectable
- [ ] cleanup works under error conditions
- [ ] logs are sufficient for debugging production incidents

### 8.3 Cutover Exit Gate

- [ ] parity with existing engine behavior is acceptable
- [ ] Node.js integration path is verified
- [ ] containerized deployment path is verified
- [ ] known unacceptable regressions are zero or explicitly signed off
- [ ] team agrees cutover can proceed

### 8.1 Engine Preservation
- [ ] Compare wrapped VFF output to baseline
- [ ] Compare wrapped AFF output to baseline
- [ ] Compare wrapped image output to baseline
- [ ] Compare wrapped frame analysis output to baseline

### 8.2 Product Flow
- [ ] Test frontend -> Node.js -> `safeguardmedia` -> engine -> result flow
- [ ] Test synchronous path if any remain
- [ ] Test asynchronous path
- [ ] Test concurrent requests
- [ ] Test corrupted-file handling
- [ ] Test oversized-file handling

### 8.3 Operational Checks
- [ ] Confirm logs are sufficient for debugging
- [ ] Confirm failures in one engine do not crash the whole stack
- [ ] Confirm cleanup works after success and failure

Exit criteria:
- the unified stack reproduces old engine behavior closely enough and is stable under normal use

---

## Phase 9 — Node.js Cutover

Goal: switch the other product onto the unified backend.

### 9.0 Cutover Decisions To Lock

- [ ] Decide whether cutover is all-at-once or staged
- [ ] Decide rollback strategy
- [ ] Decide fallback path if one engine is temporarily unstable
- [ ] Decide who owns cutover validation on the Node.js side

### 9.1 Node.js Integration Approval Checklist

- [ ] Approve Node.js request format to `safeguardmedia`
- [ ] Approve Node.js job polling behavior
- [ ] Approve Node.js error handling for failed jobs
- [ ] Approve Node.js timeout behavior
- [ ] Approve Node.js handling of sync vs async result paths

### 9.2 Cutover Execution Checklist

- [ ] switch Node.js backend to `safeguardmedia`
- [ ] remove direct engine-script invocation from Node.js flow
- [ ] remove engine-specific assumptions from Node.js response handling
- [ ] verify frontend flows through Node.js using the unified backend
- [ ] verify rollback path remains available until stabilization period ends

### 9.3 Post-Cutover Exit Gate

- [ ] Node.js uses only the unified service
- [ ] old direct engine invocation path is no longer required in normal operation
- [ ] cutover issues are tracked and triaged
- [ ] the system is stable enough to begin post-stabilization cleanup

- [ ] Point Node.js backend to `safeguardmedia` only
- [ ] Remove direct engine-specific terminal/script calls from Node.js flow
- [ ] Update Node.js to use the final job polling contract
- [ ] Validate frontend behavior through Node.js
- [ ] Confirm no remaining dependency on old direct engine endpoints

Exit criteria:
- the other product uses the unified service successfully in containerized form

---

## Deferred Until After Stabilization

- [ ] Revisit whether `engine-work` should later be split into image and frames services
- [ ] Revisit whether any engine can safely move from wrapper isolation into tighter integration
- [ ] Deep refactor of engine internals
- [ ] Splitting `engine-work` into separate image and frames services
- [ ] PDF/report generation improvements
- [ ] Advanced auth/rate limiting unless immediately required
- [ ] Performance optimization beyond obvious bottlenecks
- [ ] UI polish

---

## Current Recommended Focus

Work on these phases first:
- [ ] Phase 0 — Freeze Direction
- [ ] Phase 1 — Define Contracts
- [ ] Phase 2 — Build Regression Baseline
- [ ] Phase 3 — Wrap Engines Without Rewriting Them
- [ ] Phase 4 — Turn SafeguardMedia Into the Orchestrator

Do not start container polish or frontend polish before these are stable.
