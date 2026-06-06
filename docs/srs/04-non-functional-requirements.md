## 4. Non-Functional Requirements

### 4.1 Performance (NFR-P)

- **NFR-P-1**: Chat round-trip (user message to first streamed token) shall be under 2 seconds for typical queries
- **NFR-P-2**: WebSocket latency between client and agent shall be <300ms p95 from US West Coast
- **NFR-P-3**: WMS tile response from QGIS Server shall be <1 second p95 for typical layer combinations
- **NFR-P-4**: A small-domain flood model (≤200 km² at 30m resolution) shall complete end-to-end in under 15 minutes from user prompt to map display
- **NFR-P-5**: Time scrubbing shall feel responsive (≤500ms between user drag and new tiles displayed) for typical temporal layers
- **NFR-P-6**: Vector search over the event corpus shall return results in <2 seconds for corpora up to 100k events

### 4.2 Reliability (NFR-R)

- **NFR-R-1**: A failed tool execution shall surface as a failure in the pipeline strip with diagnostic logs accessible; the session shall remain usable
- **NFR-R-2**: WebSocket disconnection shall trigger automatic reconnection with state recovery from the agent
- **NFR-R-3**: Solver job cancellation shall complete within 30 seconds of user request
- **NFR-R-4**: QGIS Server instances shall be stateless and replaceable; loss of an instance shall not impact in-flight sessions beyond a brief tile-loading delay

### 4.3 Portability (NFR-PO)

- **NFR-PO-1**: Web client shall run on current Chrome, Firefox, Safari, Edge
- **NFR-PO-2**: Mobile-responsive layout is in scope; specifically-optimized mobile UX is deferred
- **NFR-PO-3**: All cloud infrastructure shall be deployable via infrastructure-as-code (Terraform or equivalent) to support reproducible environments

### 4.4 Security (NFR-S)

- **NFR-S-1**: WebSocket connections shall use WSS with TLS 1.2+
- **NFR-S-2**: Google Cloud credentials shall be managed via service accounts and Workload Identity; never embedded in client code
- **NFR-S-3**: MongoDB connection strings shall be stored in Google Secret Manager
- **NFR-S-4**: User-supplied URLs (for news fetching) shall be validated against a domain allowlist or sandboxed appropriately
- **NFR-S-5**: GCS bucket access shall be scoped by service account; no public buckets except for shared snapshot assets

### 4.5 Infrastructure budget (NFR-C)

These targets cover infrastructure spend on the deployment side; they are *not* per-run cost figures surfaced to users. User-facing cost estimation is deferred indefinitely (see FR-AS-8).

- **NFR-C-1**: Cloud idle cost (no active sessions) shall remain under $100/month for the default deployment footprint, including:
  - Cloud Run minimum instances for agent and QGIS Server
  - MongoDB Atlas (M10 cluster or smaller)
  - GCS storage (≤100GB)
  - Cloud Workflows base
- **NFR-C-2**: Solver workers shall use Cloud Run Jobs with no minimum instances (scale to zero between jobs)
- **NFR-C-3**: LLM token usage shall be minimized via deterministic workflows; common queries shall not require full atomic-tool reasoning loops

### 4.6 Licensing (NFR-L)

- **NFR-L-1**: The project repository shall include an OSI-approved open source license file at the repository root, detectable by GitHub's license detection
- **NFR-L-2**: All third-party dependencies shall be tracked and license-compatible
- **NFR-L-3**: News-derived model runs shall cite source articles in user-facing output

---

