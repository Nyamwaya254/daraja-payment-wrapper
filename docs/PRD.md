# Daraja Payments API and SDKs — Product Requirements Document

| Field | Value |
| --- | --- |
| Version | 0.3 |
| Date | 15 September 2026 |
| Status | Draft for implementation clarification |
| Product owner | Billy |
| Codebase | `daraja-payment-wrapper` |
| First release | Multi-merchant STK collections, C2B collections, individual B2C payouts, Clerk owner authentication, webhooks, Python/TypeScript SDKs, and template-generated integration snippets and coding-agent prompts |

This document describes **work to be done**, not features already delivered. It incorporates the decision to build APIs, documentation, and SDKs without a developer website, dashboard, or hosted checkout. Clerk-hosted authentication and a minimal session-handoff helper are included for owner API access. Earlier proposals containing a complete developer website or dashboard are superseded.

**Confirmed** means selected in the product discussion. **Proposed** means an implementation recommendation that the owner can change. **Open** means a decision or external dependency remains. Requirement IDs provide references for implementation tasks and acceptance tests.

## 1. Product purpose

Give developers a consistent API for collecting and disbursing M-Pesa payments through Safaricom Daraja. Developers should be able to connect their business's Daraja account, initiate an STK payment, receive a C2B payment, send an individual B2C payout, and reliably determine the outcome through polling or signed webhooks.

The current repository supplies a useful STK foundation but assumes one merchant. Adding two provider endpoints alone would leave credentials, permissions, transaction identity, callback recovery, and payout controls incomplete.

Paystack is the reference for clear APIs, useful documentation, SDK ergonomics, and asynchronous event handling. This product is not intended to reproduce its entire platform or be compatible with its API. Each business brings its own Daraja credentials and eligible shortcodes; money moves through that business's M-Pesa accounts. The service stores payment records and coordinates requests. It does not hold customer funds or provide a pooled merchant balance.

### Intended users and successful outcomes

| User | Job | Release outcome |
| --- | --- | --- |
| Merchant owner | Connect accounts and control integration access | Sign in through Clerk-hosted authentication, then use owner APIs to create a merchant, configure capabilities, issue scoped keys, and set payout limits |
| Integrating developer | Add payments to an existing application | Select payment features and a supported integration target, then use copyable SDK snippets or a tailored coding-agent prompt without reading backend internals |
| Finance or support integrator | Find and reconcile payment outcomes | Search receipts/references, identify unmatched C2B payments, and inspect unresolved operations through authorized APIs |
| Platform operator | Recover failures safely | Inspect quarantined evidence, retry permitted processing, and monitor delivery failures without resending uncertain payouts |

## 2. Scope decisions

### Confirmed for the first release

1. **APIs, documentation, and Python/TypeScript SDKs.** No dedicated website is required.
2. **STK, C2B, and B2C are included for every merchant.** Preserve STK and add both collection notifications and individual payouts. Developers can integrate any or all three without choosing a feature package or requesting a product-access upgrade. Live use requires the corresponding provider-account setup and API-key permissions; B2C also requires owner-configured payout limits.
3. **Each merchant supplies its own Daraja credentials and shortcodes.** Credentials and records must be isolated between merchants.
4. **Self-service onboarding.** Owners sign in through Clerk-hosted authentication and complete merchant administration through APIs with command-line examples. A minimal authentication return helper supplies the session handoff; no merchant dashboard is required.
5. **Automated B2C initiation using a dedicated payout-scoped key and owner-configured limits.** No owner approval is required for every payout.
6. **Record and reconcile C2B payments.** An unknown invoice/reference must not cause an otherwise valid confirmed payment to disappear or be treated as failed.
7. **Individual payouts first.** Bulk payout creation is deferred.
8. **Clerk authentication with hosted login and local merchant roles.** Clerk application `app_3JMnuwEIXsPwtUo5l5zyq9OJPf5` handles human signup, login, verification, recovery, and sessions. Our backend owns merchants, memberships, roles, and scoped integration API keys. Clerk Organizations are not used in this release.
9. **Template-generated SDK snippets and coding-agent prompts.** Manual setup returns copyable integration examples; Agent setup returns a ready-to-paste implementation prompt. Both are assembled from versioned, tested templates, without a runtime AI-generation service.
10. **Selected Python/TypeScript integration targets.** The first release supports plain Python, plain TypeScript, FastAPI, Express, and Next.js server-side integrations. A developer selects STK, C2B, B2C, or any combination to tailor the output; selecting features does not change product access, provider readiness, or key permissions.

### Excluded from this release

- Marketing or developer website, merchant dashboard, visual account administration, hosted checkout, and checkout sessions.
- Custom password authentication, Clerk Organizations, runtime AI-generated integration code, and additional language targets beyond the confirmed Python/TypeScript targets. Next.js is an integration-example target, not a replacement for this FastAPI service.
- Cards, subscriptions, split payments, marketplace settlement, platform custody, and billing merchants for API usage.
- Bulk payouts, scheduled payroll, and automatic refunds or reversals.
- Automatic C2B history backfill through Pull Transaction. Missing-notification recovery must still have a documented operational procedure.
- Arbitrary invoice-management features. Reference matching is supported; a full invoicing product is not.
- Publishing SDKs to public registries or deploying production services merely by accepting this document. Those are later release actions.

## 3. Current implementation and required changes

The current working tree includes the earlier reliability and security fixes. These must be preserved. The README accurately identifies STK as the only implemented payment flow; C2B/B2C enum values do not constitute working integrations.

| Area | Current implementation | Required change |
| --- | --- | --- |
| Merchant context | Global Daraja settings and shared internal API keys | Add merchants, environments, provider accounts, memberships, and scoped keys; enforce ownership throughout |
| Human authentication | No owner signup/session integration | Add Clerk-hosted login, verified Clerk identity bindings, local merchant roles, and lifecycle-event handling |
| Credentials | One configured provider account | Encrypt credentials per account and environment; resolve them for each operation; support rotation |
| Public contract | `/api/v1` STK initiation/status and operator routes | Preserve compatible v1 behavior; introduce a documented v2 contract for collections, transfers, and administration |
| Payment storage | One STK-shaped table; required full phone, short reference/description, globally unique idempotency key and receipt | Separate shared identity from operation-specific details; support masked C2B identities and account-scoped evidence |
| Initiation | Local STK identity committed before the provider call | Preserve that ordering; add durable B2C dispatch and atomic payout-limit reservations |
| Callbacks | Durable PostgreSQL inbox; STK application and quarantine | Add authenticated account routing, C2B validation/confirmation, B2C result/timeout handling, and query correlation |
| Reconciliation | STK checkout query; generic asynchronous responses retained for review | Add evidence-aware B2C recovery and merchant-visible unresolved C2B/STK records |
| Merchant notifications | No outbound merchant webhook system | Add a transactional outbox, signing, retries, delivery history, and authorized replay |
| Administration | Global-key callback inspection/replay | Scope merchant administration and raw evidence access; separate platform operator privileges |
| Developer tooling | Backend package and README | Add versioned OpenAPI, independently installable Python/TypeScript clients, and tested Manual setup / Agent setup templates for the five confirmed targets |

### Implementation map

These are the current files most affected. New modules should follow the existing route/service/repository/provider separation; the names of new modules are implementation proposals.

| Existing source | Change expected |
| --- | --- |
| [Configuration](/Users/billytheegoat/projects/daraja-payment-wrapper/app/config.py), [resources](/Users/billytheegoat/projects/daraja-payment-wrapper/app/resources.py), [dependencies](/Users/billytheegoat/projects/daraja-payment-wrapper/app/dependencies.py) | Retain process-wide connection pools; replace global merchant credentials with an explicitly resolved account context |
| [API authentication](/Users/billytheegoat/projects/daraja-payment-wrapper/app/middleware/api_key.py), [rate limits](/Users/billytheegoat/projects/daraja-payment-wrapper/app/middleware/rate_limit.py) | Separate verified Clerk sessions, local integration keys, public guide endpoints, and callback authentication; enforce local roles and route-appropriate limits |
| [Payment model](/Users/billytheegoat/projects/daraja-payment-wrapper/app/models/payment.py), [repository](/Users/billytheegoat/projects/daraja-payment-wrapper/app/repository/payment_repo.py) | Add tenant ownership, operation-specific constraints, safe idempotency boundaries, and canonical receipt association |
| [STK routes](/Users/billytheegoat/projects/daraja-payment-wrapper/app/routes/stk.py), [schemas](/Users/billytheegoat/projects/daraja-payment-wrapper/app/schemas/stk.py), [service](/Users/billytheegoat/projects/daraja-payment-wrapper/app/services/stk.py) | Preserve v1; introduce v2 schemas and separate callback/operator responsibilities. Correct descriptions implying acceptance proves a PIN prompt was delivered |
| [Provider authentication](/Users/billytheegoat/projects/daraja-payment-wrapper/app/daraja/auth.py), [client](/Users/billytheegoat/projects/daraja-payment-wrapper/app/daraja/client.py), [query adapter](/Users/billytheegoat/projects/daraja-payment-wrapper/app/daraja/query.py) | Resolve per-account credentials; add C2B/B2C adapters and typed status-query evidence; remove unsupported B2C reversal implications |
| [Callback authentication](/Users/billytheegoat/projects/daraja-payment-wrapper/app/security.py), [IP allowlist](/Users/billytheegoat/projects/daraja-payment-wrapper/app/middleware/ip_allowlist.py) | Use dedicated callback secrets for new flows; retain verification for outstanding legacy STK tokens and trusted-proxy handling |
| [Inbox model](/Users/billytheegoat/projects/daraja-payment-wrapper/app/models/inbox.py), [repository](/Users/billytheegoat/projects/daraja-payment-wrapper/app/repository/inbox_repo.py), [processor](/Users/billytheegoat/projects/daraja-payment-wrapper/app/services/inbox.py) | Scope event identity and processing to the account/environment; parse each callback type separately; preserve original evidence |
| [Reconciliation](/Users/billytheegoat/projects/daraja-payment-wrapper/app/services/reconciliation.py), [tasks](/Users/billytheegoat/projects/daraja-payment-wrapper/app/tasks/reconciliation_tasks.py), [Celery setup](/Users/billytheegoat/projects/daraja-payment-wrapper/app/tasks/celery.py) | Add B2C query recovery, outbox delivery, and reservation recovery with explicit task ownership |
| [Audit model](/Users/billytheegoat/projects/daraja-payment-wrapper/app/models/audit.py), [observability](/Users/billytheegoat/projects/daraja-payment-wrapper/app/observability.py) | Record merchant/account/principal context without breaking historical checksum verification; extend redaction to new secrets and payloads |
| [Integrity migration](/Users/billytheegoat/projects/daraja-payment-wrapper/migrations/versions/e4912c8a0137_payment_integrity.py), [package configuration](/Users/billytheegoat/projects/daraja-payment-wrapper/pyproject.toml), [README](/Users/billytheegoat/projects/daraja-payment-wrapper/README.md) | Add subsequent migrations, SDK packaging and documentation delivery; do not rewrite applied migrations to introduce the new product |

## 4. Functional requirements

### 4.1 Merchant onboarding, credentials, and access

| ID | Requirement and acceptance criteria |
| --- | --- |
| TEN-01 | **Merchant ownership.** Every payment, transfer, recipient, provider account, callback, webhook, audit event, and background operation has an explicit merchant/environment context. A credential belonging to merchant A cannot read, list, mutate, replay, or infer merchant B's records through identifiers or pagination. |
| TEN-02 | **Clerk-based self-service.** A new owner signs up, verifies their account, and signs in through Clerk-hosted authentication. A minimal return helper uses Clerk's supported session handling and obtains fresh session tokens for owner API examples; it must not place bearer tokens in application URLs or logs. The verified owner can create a merchant and its initial owner membership atomically through the API. An idempotent creation request cannot create duplicate merchants/memberships. Account verification does not establish ownership of an existing merchant. |
| TEN-03 | **Environment separation.** Test and live keys, provider accounts, recipients, operations, callback routes, and webhook configuration are separate. A test key cannot initiate live payments. Responses and events identify the environment. |
| TEN-04 | **Account capabilities.** Store credentials and shortcodes per provider account; track STK, C2B, B2C, and status-query readiness independently. Unsupported or incomplete capabilities fail before a financial request is sent. Saving credentials is not proof of provider approval. |
| TEN-05 | **Credential protection.** Encrypt retrievable provider secrets using managed encryption keys outside the database. Never return stored secrets through read APIs. Audit creation/rotation, redact logs, and preserve the account's identity across rotation. B2C supports an encrypted `SecurityCredential` without requiring storage of the initiator's plaintext password. |
| TEN-06 | **Scoped integration access.** Our backend issues high-entropy merchant API secrets once, stores only a secure verification digest, and supports listing metadata, expiration, rotation, and immediate revocation. Proposed scopes include `payments:read`, `payments:write`, `transfers:read`, `transfers:write`, `recipients:write`, and limited webhook/reconciliation scopes. Clerk human sessions authorize owner administration; they do not replace integration keys or the dedicated B2C payout key. |
| TEN-07 | **Privilege boundaries.** A collection key cannot pay out, change payout limits, reveal credentials, or create a more privileged key. Callback authentication must not grant merchant API access. Legacy keys acquire no payout privilege automatically. |
| TEN-08 | **All three APIs included.** Every merchant has product access to STK, C2B, and B2C and their integration guides. Onboarding must not require a choice of an STK-only, C2B-only, or B2C-only package. Developers may implement only the flows they need and configure the others later without a product-access upgrade. Provider readiness remains account-specific under TEN-04, and key permissions remain scoped under TEN-06–07, including the dedicated B2C payout key. |
| TEN-09 | **Clerk session verification.** FastAPI verifies owner session tokens through the official `clerk-backend-api` Python SDK, requiring a valid signature, trusted issuer, valid time claims, and configured authorized parties. Bind local users to the verified issuer and Clerk user ID, never to an email match or client-supplied user ID. A development Clerk instance cannot authenticate against the production backend. Clerk instance separation is distinct from Daraja test/live modes: an authorized production owner may administer both Daraja modes. |
| TEN-10 | **Local merchant authorization.** PostgreSQL is authoritative for merchant memberships, local roles, and enabled-user state. Check them on every owner request; a valid Clerk session alone grants no access to another merchant. Membership removal or local user disablement takes effect on the next request. Clerk Organizations and Clerk metadata cannot bypass local permissions. Owner changes to credentials, keys, roles, or payout policy are audited against the verified identity. |
| TEN-11 | **Identity lifecycle.** Verify Clerk webhook signatures over the raw body, reject stale/forged deliveries, durably deduplicate events, and handle out-of-order identity updates without restoring a deleted identity. User deletion disables that person's local access while preserving merchant financial history. Merchant-owned API keys keep their explicit revocation lifecycle. Do not claim immediate Clerk session revocation from offline JWT verification alone; document token expiry and lifecycle-event propagation separately from immediate local authorization checks. |

#### Clerk setup and authentication boundary

The linked Clerk application is **`app_3JMnuwEIXsPwtUo5l5zyq9OJPf5`**. The chosen experience uses Clerk's hosted Account Portal and only the minimal session handoff necessary to call owner APIs. The backend does not store passwords, implement password recovery, or issue a replacement human-session token. [Clerk Account Portal](https://clerk.com/docs/guides/account-portal/getting-started), [Clerk Python SDK](https://github.com/clerk/clerk-sdk-python)

Record the supplied setup sequence as M1 implementation work: check/install or update the Clerk CLI, run `clerk auth login`, then run `clerk init --app app_3JMnuwEIXsPwtUo5l5zyq9OJPf5` from this existing project. Use the official Python integration when framework scaffolding is unavailable; finish with `clerk doctor` and an actual hosted-login-to-FastAPI acceptance test. This PRD revision does not execute those commands. The attachment's Next.js proxy, React components, and shadcn steps apply only to projects using those frameworks; they do not require converting this FastAPI service. [Clerk CLI](https://clerk.com/docs/cli)

Keep `CLERK_SECRET_KEY` server-side, keep actual secrets out of documentation and logs, and configure trusted instance/origin values per deployment. Clerk authenticates this platform's owners; merchant applications consuming the payment SDKs may use their own authentication provider.

### 4.2 Shared payment behavior and STK

| ID | Requirement and acceptance criteria |
| --- | --- |
| PAY-01 | **Stable operation identity.** Financial creation requires an idempotency key. Proposed uniqueness is merchant + environment + operation family + key, with provider-account identity included in the immutable request fingerprint. Identical replays return the same operation; changed parameters or account return 409. Concurrent callers and worker redelivery cannot create a second provider submission. |
| PAY-02 | **Money representation.** Proposed v2 amounts are integer whole KES, with `currency: "KES"` required. Do not use floating-point arithmetic or silently multiply Paystack-style minor units. Validate operation-specific provider limits; do not reuse the current STK maximum for B2C. Parse provider strings such as `"500.00"` exactly; unsupported fractional values enter review instead of being rounded. |
| PAY-03 | **Persist before dispatch.** Record identity, request fingerprint, account, and audit entry before calling Daraja. Financial POSTs must not be retried following an ambiguous send, read timeout, malformed acknowledgement, or provider 5xx. Retrying a client call with the same key retrieves the existing operation. |
| PAY-04 | **Preserve STK integrity.** Continue amount/phone/identifier validation, receipt checks, durable inbox processing, terminal-state protection, and evidence-aware reconciliation. A 202 or provider acknowledgement does not prove that a customer saw a prompt or paid. |
| PAY-05 | **Discoverability.** Provide tenant-scoped list/detail APIs with cursor pagination and filters for status, operation type, reference, receipt, account, and time. Return UTC timestamps, currency, environment, and an actionable unresolved reason without exposing provider secrets. |

### 4.3 C2B collections

C2B records money paid directly to a business Paybill/Till. It is not another STK initiation endpoint.

| ID | Requirement and acceptance criteria |
| --- | --- |
| C2B-01 | **Explicit registration.** An authorized owner registers validation/confirmation URLs for an eligible account using the current C2B registration contract. Store registration state, URLs, account, environment, and provider response. Registration is never an application-startup side effect. Reconfiguration exposes the provider's restrictions rather than claiming a local edit has changed live routing. |
| C2B-02 | **Bounded validation.** When provider validation is enabled, respond synchronously with the documented response shape. Under the confirmed record-and-reconcile policy, do not reject merely because an invoice/reference is unknown. Proposed registration fallback is `Completed`. Proposed application latency target is p95 below two seconds; never call a merchant webhook synchronously to decide validation. |
| C2B-03 | **Confirmation is durable.** Authenticate account routing, enforce body limits, and commit the original confirmation to the inbox before acknowledging it. Duplicate delivery must not create a second collection. Validation alone never creates a completed financial record. Database failure produces a retryable failure rather than a false durable acknowledgement. |
| C2B-04 | **Correct provider data.** Preserve receipt, business shortcode, amount, provider time, reference, and available payer fields. Support full, masked, hashed, or absent payer identity explicitly. A masked MSISDN must not be rejected by the STK phone validator, expanded into an invented number, or used as a unique customer identity. Till references may be absent. |
| C2B-05 | **Reference reconciliation.** Keep financial status separate from matching status: `unmatched`, `matched`, or `review_required`. An authorized integration can associate a confirmed collection with its external reference, including a reason and audit record, without changing receipt/amount. Repeated matches are idempotent; conflicting matches require explicit correction history. |
| C2B-06 | **Receipt association.** A C2B notification and STK callback describing the same verified account/environment/receipt must refer to one financial movement. Preserve both source observations and API operation identities. Do not count two collections or emit two distinct financial-success events. Different amounts or insufficient association evidence enter review; never guess a match from phone and amount alone. |
| C2B-07 | **Recovery visibility.** Expose unmatched collections, invalid evidence, and quarantine reasons through authorized APIs. Document how to investigate a missing confirmation using provider records and controlled evidence recovery. Do not claim automatic history backfill in this release. |

The current official documentation specifies C2B v2 URL registration and describes masked MSISDNs; registration and validation behavior must follow the merchant's actual provider configuration. [Safaricom C2B documentation](https://developer.safaricom.co.ke/apis/CustomerToBusiness)

### 4.4 B2C individual payouts

| ID | Requirement and acceptance criteria |
| --- | --- |
| B2C-01 | **Recipient and request.** A payout-scoped key creates a single transfer to a normalized eligible phone or saved recipient. Support configured `BusinessPayment`, `SalaryPayment`, and `PromotionPayment` capabilities. Snapshot recipient, amount, command, remarks, and account on creation so later recipient edits cannot redirect a queued payout. |
| B2C-02 | **Owner limits.** The owner configures per-transfer and daily aggregate limits by live provider account; proposed accounting timezone is Africa/Nairobi. Until configured, live payouts are disabled. Reserve capacity transactionally before dispatch so concurrent requests cannot bypass a limit. A disabled payout capability or insufficient limit rejects before sending. |
| B2C-03 | **Reservation lifecycle.** Completed transfers consume reserved capacity; definitive failures release it once; unknown/review states retain it until evidence resolves the outcome. Reservations remain tied to their original accounting day across midnight. Limit reductions cannot release existing reservations or re-enable spending above the new limit. Provider funds and these risk limits are distinct. |
| B2C-04 | **Durable dispatch.** Commit the transfer, reservation, dispatch job, and unique `OriginatorConversationID` together. A worker claims dispatch durably and uses that same identifier. A crash after dispatch may have begun results in recovery/review, never a new automatic financial POST. Only a demonstrably unsent request may resume submission. |
| B2C-05 | **Result handling.** Route result and timeout callbacks to the correct account and transfer even when they arrive before the acknowledgement is saved. Provider acceptance only updates submission information. Completion requires a successful financial result and the required receipt, amount, date, recipient, and identifier evidence. Missing or contradictory evidence enters review. Receipt presence alone is not success. |
| B2C-06 | **Uncertain outcomes.** A transport timeout, queue-timeout callback, duplicate-originator response, or missing callback is not proof that no funds moved. Keep the transfer unresolved, retain its limit reservation, and reconcile using the original identifiers. Never create a replacement payout automatically. |
| B2C-07 | **Status-query recovery.** Correlate the query request/response separately from the original transfer. Query acceptance/result success does not itself prove payout completion; inspect transaction-status and financial evidence. Query records are append-only evidence, and conflicting results are quarantined. |
| B2C-08 | **No reversal promise.** This release has no automatic B2C reversal endpoint. Document the supported provider/operator recovery procedure and do not advertise low-level reversal helpers as B2C refund support. |

Use the current B2C v3 request contract, including provider field spelling such as `Occassion`. B2C uses `OriginatorConversationID`; the transaction-status request uses `OriginalConversationID`. Their distinct meanings must appear in adapter tests. [Safaricom B2C documentation](https://developer.safaricom.co.ke/apis/BusinessToCustomer), [transaction-status documentation](https://developer.safaricom.co.ke/apis/TransactionStatus)

### 4.5 Merchant webhooks and operational recovery

Daraja-to-service callbacks and service-to-merchant webhooks are separate channels with separate authentication. Do not claim that Daraja signs incoming payloads using the merchant webhook scheme proposed here.

| ID | Requirement and acceptance criteria |
| --- | --- |
| EVT-01 | **Atomic notification creation.** Commit the financial change, audit record, and outbound event together. If the transaction rolls back, no success event exists. If Redis or a worker is unavailable after commit, a database sweep eventually delivers the event. |
| EVT-02 | **Event contract.** Proposed events include `payment.completed`, `payment.failed`, `payment.review_required`, `transfer.completed`, `transfer.failed`, and `transfer.review_required`. Include stable event ID, schema version, environment, resource ID/version, canonical financial-movement ID when available, and occurrence time. Document at-least-once delivery and possible out-of-order arrival. |
| EVT-03 | **Verification.** Proposed signing is HMAC-SHA256 over the timestamp and exact raw body, using a separate endpoint secret. Include signature version and timestamp; offer constant-time verification helpers with a proposed five-minute acceptance window. Retries use a fresh delivery timestamp but the same event ID/body. Support controlled secret overlap during rotation. |
| EVT-04 | **Delivery and replay.** Treat 2xx as acknowledgement. Proposed retry policy uses exponential backoff with jitter for up to 72 hours, then marks delivery exhausted. Record each attempt and bounded, redacted response details. Authorized replay reuses the event ID and never changes payment state. Revoked/disabled endpoints receive no new attempts. |
| EVT-05 | **Endpoint safety.** Live destinations require HTTPS. Block loopback, private/link-local ranges, metadata endpoints, and unsafe schemes; validate resolved addresses at registration and delivery, pin the validated destination for connection, and disable redirects. Use bounded timeouts/body sizes. Permit local development only through an explicit isolated test configuration. |
| EVT-06 | **Review APIs.** Restrict raw callback evidence and replay to explicit privileges. Tenant users see only their own account's events; platform support uses a separately audited role. Replay reprocesses authentic retained evidence; there is no force-complete or overwrite-receipt endpoint. |

### 4.6 Documentation and SDKs

| ID | Requirement and acceptance criteria |
| --- | --- |
| DX-01 | **API reference.** Maintain a versioned OpenAPI artifact, authentication/scope reference, error catalog, pagination rules, money units, status meanings, idempotency rules, and compatibility policy. Published examples must agree with implemented schemas. |
| DX-02 | **Guides.** Provide executable quickstarts for Clerk-hosted owner login and API onboarding, Daraja capability setup, STK, C2B registration/reconciliation, B2C limits/payouts, signed webhooks, and recovery. Clearly distinguish local simulation, Safaricom sandbox, and live environments. Owner setup and merchant application integration have separate authentication requirements. No developer website build is required. |
| DX-03 | **Python SDK.** Ship a client independent of the FastAPI server package, with typed request/response models, sync/async support as a proposed default, configurable timeouts/base URL, structured exceptions, pagination helpers, and webhook verification. Installing it must not pull PostgreSQL/Celery server dependencies. |
| DX-04 | **TypeScript SDK.** Ship a typed server-side Node.js client with equivalent capabilities, abort/timeout support, documented runtime support, pagination, structured errors, and raw-body webhook verification. Secret keys must never be presented as browser-safe credentials. |
| DX-05 | **Safe retries.** SDKs require the caller's idempotency key for creation and never generate a new one during retry. Financial creation is not automatically retried by default. Any supported retry mode must preserve the identical key and payload; safe read retries are bounded. |
| DX-06 | **Packaging and examples.** Build installable package artifacts and run Python/TypeScript integration examples against the service with a deterministic provider simulator. Verify fresh installation and imports independently of the repository checkout. Registry names and supported runtime versions remain release decisions. |
| DX-07 | **Template generation.** Provide a catalog and renderer for versioned, tested integration templates. Developers select payment features, a supported target, Manual setup or Agent setup, and test/live mode. Both SDKs expose a helper to retrieve the rendered guide. Rendering is stateless documentation generation: it does not require merchant records, enable features, issue keys, register callbacks, or call Daraja. No runtime AI model or free-form project-description generation is required. |
| DX-08 | **Manual setup.** Return copyable SDK snippets, installation/configuration instructions, required scopes, relevant payment calls, signed merchant webhook handling, and local tests. Support plain Python, plain TypeScript, FastAPI, Express, and Next.js server-side integrations. Use existing SDK releases; do not generate a new SDK package per request. |
| DX-09 | **Agent setup.** Return a self-contained coding-agent prompt tailored to the same selected features and target. Include SDK/API versions, configuration placeholders, implementation steps, idempotency/outcome rules, webhook handling, and acceptance tests. Direct the agent to inspect and fit the existing project, preserve existing behavior, and use local/simulated tests. The prompt must not automatically initiate real payments, register provider URLs, create privileged credentials, or install this platform's Clerk application into the merchant's product. |
| DX-10 | **Versioned, safe output.** Pin every template release to compatible API, SDK, and framework/runtime versions and return the resolved versions with its output. A pinned version and normalized selection produce stable content. Unknown versions or unsupported selections return validation errors rather than fabricated examples. Use placeholders for all secrets/account values, retain server-only secret usage, and keep manual examples and agent instructions consistent. |
| DX-11 | **Template validation.** Test all seven nonempty feature combinations across five targets, two output modes, and two environments: 140 render selections per template release. Execute or type-check the manual examples against the supported dependency matrix with mocked provider/network behavior. Check agent prompts against the same contracts and required acceptance instructions. Rendering coverage is not evidence that an arbitrary coding agent will implement a prompt correctly. |

#### Integration output coverage

| Target | SDK | Manual setup output | Agent setup output |
| --- | --- | --- | --- |
| `python` | Python | Standalone Python SDK usage, raw-body webhook verification function, and test fixtures | Python integration prompt using the same methods and fixtures |
| `typescript` | TypeScript | Standalone server-side TypeScript SDK usage, webhook verification function, and tests | TypeScript integration prompt using the same contract |
| `fastapi` | Python | FastAPI routes/dependencies and merchant webhook handler | Prompt to fit the integration into an existing FastAPI application |
| `express` | TypeScript | Express server routes and raw-body merchant webhook handler | Prompt to fit the integration into an existing Express application |
| `nextjs` | TypeScript | Next.js server-side routes and merchant webhook handler; no secret keys in client components | Prompt to fit the integration into an existing Next.js application while preserving the server/client boundary |

Each output includes only the chosen payment flows plus their shared prerequisites. STK includes initiation, polling, and verified completion; C2B includes registration prerequisites, incoming events, reference matching, and receipt deduplication; B2C includes payout-key/limit prerequisites, individual transfers, and uncertain-outcome recovery. Multiple selected flows share one consistent configuration and webhook example. Feature selection tailors documentation only; TEN-08 still grants access to all three APIs.

Both modes include installation, configuration placeholders, authentication scopes, stable idempotency keys, correct money units, asynchronous statuses, signature verification, and error handling. Distinguish the merchant application's webhook from the service's Daraja callbacks. Test output defaults to sandbox configuration and explicit local simulation instructions. Live output explains readiness requirements but never embeds a real credential or executes a live operation. Manual setup and Agent setup are output modes exposed through the API/SDK and documentation, not a requirement to build the screenshot's tabbed website interface.

## 5. Proposed public API contract

Route names and exact schemas below are **proposals**, not descriptions of existing endpoints. Freeze them in OpenAPI during the foundation milestone before implementing the SDKs.

Document separate OpenAPI security schemes for a Clerk owner session and a merchant integration key, both transported through `Authorization: Bearer`. Each protected route explicitly permits its intended principal type. Owner administration requires a verified Clerk session plus local authorization. Financial initiation uses a scoped merchant integration key and requires `Idempotency-Key`. That key fixes merchant/environment context; supplied resource IDs cannot override it. Owner sessions may select only merchants/environments authorized by their local memberships. Public guide endpoints and separately authenticated webhook/callback endpoints are explicit exceptions, not broad authentication bypasses.

| Resource | Proposed operations | Purpose |
| --- | --- | --- |
| Owner identity | `GET /api/v2/me` | Return the verified Clerk identity and accessible local merchant memberships; Clerk hosts signup/login/recovery |
| Merchant onboarding | `POST /api/v2/merchants` | Create a merchant and initial owner membership atomically for the authenticated Clerk user; require an idempotency key |
| Clerk lifecycle | `POST /api/v2/webhooks/clerk` | Receive signed identity lifecycle events through dedicated authentication and durable deduplication |
| Keys and accounts | `/api/v2/api-keys`, `/api/v2/provider-accounts` | Scoped keys, credentials, rotation, and capability configuration |
| C2B registration | `POST /api/v2/provider-accounts/{id}/c2b-registration` | Explicitly register/inspect provider callback configuration |
| Collections | `POST /api/v2/payments`, `GET /api/v2/payments`, `GET /api/v2/payments/{id}` | Initiate STK; read STK/C2B collections. C2B records originate from confirmations |
| Matching | `POST /api/v2/payments/{id}/match` | Associate a confirmed collection with an external business reference |
| Recipients | `/api/v2/recipients` | Create, retrieve, list, update, and deactivate saved payout recipients |
| Transfers | `POST /api/v2/transfers`, `GET /api/v2/transfers`, `GET /api/v2/transfers/{id}` | Individual B2C creation and outcome retrieval |
| Payout policy | `/api/v2/provider-accounts/{id}/payout-policy` | Owner-only configuration of payout capability and limits |
| Webhooks | `/api/v2/webhook-endpoints`, `/api/v2/webhook-deliveries`, `/api/v2/webhook-deliveries/{id}/replay` | Destination configuration, diagnostics, and event redelivery |
| Recovery | `/api/v2/callback-events`, `/api/v2/callback-events/{id}/replay`, `/api/v2/reconciliation-cases` | Permission-controlled evidence inspection and reconciliation |
| Integration catalog | `GET /api/v2/integration-guides/catalog` | List supported targets, feature combinations, modes, and compatible template/API/SDK versions |
| Integration output | `POST /api/v2/integration-guides/render` | Return tailored Manual setup snippets or an Agent setup prompt from a supported template selection |

Provider callback routes are separately authenticated and are not merchant creation APIs. Use account/environment-specific opaque routing credentials and neutral path names that satisfy provider URL rules. Do not let an untrusted shortcode inside a payload select another merchant's credentials.

#### Integration renderer contract

The catalog and renderer are public, rate-limited documentation endpoints. The request accepts only the fields below and rejects unknown fields, including credentials or merchant identifiers. It does not inspect merchant data or fetch user-supplied URLs.

| Field | Accepted values and default |
| --- | --- |
| `features` | Required nonempty set drawn from `stk`, `c2b`, and `b2c`; normalize ordering and duplicate selections |
| `target` | Required: `python`, `typescript`, `fastapi`, `express`, or `nextjs` |
| `mode` | Required: `manual` or `agent` |
| `environment` | `test` or `live`; defaults to `test` |
| `template_version` | Optional supported catalog version; omission selects the current stable template release |

Return 200 with Markdown `content`, the normalized selection, resolved `template_version`, compatible `api_version` and `sdk_version`, and the names of required configuration placeholders. Unknown or incompatible selections return 422. Publishing a new stable template must not silently alter a previously pinned version.

For example, `features: ["stk", "b2c"]`, `target: "fastapi"`, and `mode: "agent"` returns a Python/FastAPI prompt for STK and individual B2C, including shared webhook handling and payout prerequisites. Switching only the mode to `manual` returns corresponding SDK examples. Neither request changes merchant access or account configuration.

### Example: proposed transfer creation

```http
POST /api/v2/transfers
Authorization: Bearer <live-payout-scoped-key>
Idempotency-Key: payout-order-482-attempt-1
Content-Type: application/json

{
  "provider_account_id": "<account-uuid>",
  "recipient_id": "<recipient-uuid>",
  "amount": 500,
  "currency": "KES",
  "command": "BusinessPayment",
  "reference": "ORDER-482",
  "remarks": "Supplier payment"
}
```

The service returns 202 with a stable transfer ID, `pending` status, and `submission_status: "queued"` after durable creation. It does not return a financial-success claim. An idempotent replay returns that transfer's current state. Amount, account, recipient snapshot, command, reference, and remarks participate in the request fingerprint.

Standard errors contain a stable machine-readable code, human-readable message, request ID, field details where relevant, and the existing operation ID for an idempotency conflict. Do not leak raw provider credentials or customer evidence. Use 401 for invalid authentication, 403 for insufficient scope, 404 for inaccessible resources, 409 for conflicts, 422 for validation, 429 for rate limits, and 503 when required infrastructure cannot safely accept work.

## 6. State and evidence model

Keep **financial outcome**, **submission progress**, and **reference matching** separate. For example, a transfer can be `pending` with submission `queued` or `accepted`; a confirmed C2B collection can be `completed` and `unmatched`.

| Financial status | Meaning and permitted consequence |
| --- | --- |
| `pending` | Recorded and awaiting dispatch/outcome; no success claim |
| `unknown` | Submission or outcome is uncertain; no blind resubmission; retain payout reservation |
| `review_required` | Evidence is incomplete/conflicting; expose a reason and recovery path |
| `completed` | Verified successful financial evidence exists; canonical movement and notification are recorded atomically |
| `failed` / `cancelled` | Definitive unsuccessful outcome or proven no-send failure; cancellation is provider-evidenced, not an instruction to undo funds |
| `reversed` | Historical compatibility only; no automatic new reversal workflow in this release |

An authenticated provider confirmation with sufficient operation-specific evidence can establish completion. A successful status query needs transaction evidence; its own `ResultCode: 0` is not sufficient. No timeout proves failure. Ordinary processing cannot overwrite a terminal financial outcome; contradictory evidence creates a review case.

C2B confirmations do not share STK's exact field requirements. Introduce typed completion evidence and conditional database constraints rather than weakening STK checks for every payment. For example, C2B has no STK checkout ID and may not provide a full phone; B2C has conversation identifiers and recipient evidence. Derived normalized fields must retain a link to their original provider evidence.

## 7. Proposed architecture and data changes

Retain **FastAPI, PostgreSQL, Redis, and Celery**. Clerk is authoritative for human authentication and sessions; PostgreSQL owns local identity bindings, merchant authorization, payment identity, outcomes, reservations, callback evidence, and pending work. Redis supplies caching, rate limits, and worker notification; broker availability is not the durability boundary for provider confirmations or merchant events.

Reuse process-wide database/HTTP/Redis pools. Construct lightweight per-operation provider contexts instead of creating an engine or process-global mutable Daraja client for every merchant. Cache/lock keys must include environment, provider-account identity, and credential version where applicable; never share a token based on shortcode alone.

Keep integration templates, their compatibility catalog, and verification fixtures in version control. Share the validated source examples between manual output and coding-agent prompts to reduce drift. The renderer composes documentation only and needs neither an AI provider nor a merchant-specific generation table.

### Minimum data responsibilities

| Entity | Required responsibility |
| --- | --- |
| Local user / Clerk identity binding | Unique verified issuer + Clerk user ID, minimal profile fields, enabled/deleted state, and lifecycle-event tracking; no stored passwords |
| Merchant, owner, membership | Establish local ownership and administration privileges; creation and initial owner assignment are atomic |
| Clerk lifecycle inbox | Signed-event identity, durable deduplication, processing state, and ordering/deletion safeguards, separate from Daraja payment evidence |
| API key | Merchant/environment scope, verification digest, privileges, expiration/revocation |
| Provider account and credential version | Immutable business-account identity, environment, shortcodes/capabilities, encrypted secrets, callback routing and rotation history |
| Payment/transfer operation and typed details | Stable client identity and provider-account ownership; separate STK, C2B, and B2C fields/constraints |
| Provider evidence / canonical movement | Associate receipts and source observations; enforce one financial movement per verified account/environment/receipt |
| Recipient | Merchant/environment-owned destination, with immutable snapshots on transfers |
| Payout policy and reservation | Serialize spending limits and retain unresolved exposure |
| Dispatch job | Durable submission intent, claim state, original originator ID, and evidence of whether sending may have begun |
| Callback inbox and reconciliation case | Immutable source evidence, account-scoped deduplication, processing attempts, unresolved reasons, and review history |
| Webhook endpoint, outbox event, delivery attempt | Encrypted signing secret, immutable event payload, retry scheduling and history |
| Audit event | Identified actor/key, merchant/account, request ID, operation, transition and integrity-chain information |

**Required database changes:**

- Add tenant/environment ownership and constraints that prevent cross-tenant associations, including foreign keys and account consistency checks. Repository methods require tenant context; background tasks revalidate it. PostgreSQL row-level security can add defense in depth if implemented and tested, but is not a substitute for these controls.
- Add unique Clerk issuer/user bindings and local membership constraints. Bind existing merchant ownership through verified assignment; never grant ownership by matching an email address. Preserve identity tombstones so late events cannot recreate a deleted user's access.
- Replace the global idempotency constraint with the scoped rule in PAY-01. Inbound C2B records use provider-event/receipt deduplication rather than a fabricated client idempotency key.
- Move STK-only requirements out of shared mandatory fields. Preserve C2B references without truncating them to STK's current 12-character limit; keep provider wire-length validation in the relevant adapter/schema.
- Move global receipt uniqueness to canonical account/environment evidence. Account identity must survive credential rotation. Prevent the same external account/environment from being connected as unrelated identities to evade deduplication or merchant ownership controls.
- Define how STK-first and C2B-first receipt arrival both resolve to one canonical movement. Existing operation IDs remain usable. Ambiguous legacy or cross-source associations require review, not an automatic destructive merge.
- Extend audit context with a new checksum version if necessary. Continue verifying historical versions; do not recompute old records to conceal ordering or ownership uncertainty.
- Separate new callback secrets, merchant webhook secrets, API secrets, OAuth secrets, and STK passkeys. Preserve legacy callback verification until outstanding operations are resolved.

## 8. Reliability, security, and operational requirements

These are release requirements unless identified as a proposed sizing target.

1. **No duplicate financial dispatch caused by local concurrency or task redelivery.** This is a local guarantee, not a claim of exactly-once processing across the provider network.
2. **Durable acknowledgement.** Confirmation/result callbacks receive a success acknowledgement only after inbox commit. C2B validation has a separate synchronous decision boundary and provider fallback policy.
3. **Bounded resource use.** Retain the current 64 KiB callback body limit unless provider evidence requires a reviewed change. Bound HTTP timeouts, parser work, pagination, webhook response capture, and worker claims.
4. **Recoverable outages.** Demonstrate worker restart, Redis outage, provider timeout, and database rollback behavior. Recovered jobs must not resend an uncertain financial operation.
5. **Data protection.** Encrypt secrets and infrastructure storage/backups; redact phones, names, credentials, signatures, and callback URL capabilities from logs. Restrict raw evidence access separately from ordinary payment reads. Define retention before live release; do not silently delete evidence needed for unresolved cases.
6. **Observability.** Measure per-flow acceptance/rejection, callback lag, oldest unresolved age, quarantine size, outbox backlog, failed deliveries, payout reservations, and worker/database health. Use bounded metric labels; do not put phone numbers, receipts, or unbounded merchant IDs into metric dimensions.
7. **Auditable administration.** Log who changed credentials, keys, payout policies, webhook endpoints, and reference matches. Replay is audited. Infrastructure logs must also exclude callback secrets.
8. **Performance targets need a test profile.** Proposed local targets are p95 below 500 ms for ordinary reads and below one second for durable callback ingestion, excluding provider network time. The C2B validation target is defined in C2B-02. Agree concurrency, data volume, hosting capacity, and availability/recovery objectives before calling these production guarantees.
9. **Documented operations.** Provide deployment/migration, backup/restore, key rotation, dead-letter handling, provider outage, and unresolved-payment runbooks. No force-completion shortcut may bypass evidence checks.

## 9. Compatibility and migration

1. Inventory existing records, configured accounts, API keys, outstanding callbacks, duplicate receipts, and incomplete historical evidence. Assign existing records to an explicit legacy merchant/environment/account; do not guess ownership where ambiguous.
2. Add new tables/columns in migrations after the current integrity revision. Preserve existing IDs, audit records, callback evidence, and v1 response behavior. Test a snapshot upgrade before production use.
3. Backfill ownership only from verified configuration/evidence. Migration conflicts stop with a diagnostic report; no silent deletion, fabricated phone/reference, or automatic merging of money records.
4. Map existing internal keys to the legacy merchant and existing collection/recovery permissions only. Keep v1 `X-API-Key` support during a documented transition. New tenants use v2 scopes; legacy keys must not see them.
5. Keep in-flight legacy STK callback URLs and relevant signing material functional. Credential rotation must not accidentally invalidate callbacks for old operations. C2B registration changes require the provider's actual update procedure.
6. Deploy compatible schema first, then coordinate API/worker upgrades. Drain workers that consume incompatible task argument formats; avoid concurrent old/new consumers when their ownership or dispatch rules differ.
7. Enable each live capability only after account-specific readiness checks and acceptance evidence. Test credentials and fixtures are never promoted automatically.
8. Prefer a documented roll-forward recovery plan. Backups and schema rollback alone cannot undo provider money movement; never restore a snapshot and blindly resend work.

## 10. Delivery milestones and exit criteria

No delivery dates are committed here. Estimate after the open architecture choices and deployment constraints are resolved.

| Milestone | Deliverable | Exit criteria |
| --- | --- | --- |
| M0 — Contract and design | Frozen initial OpenAPI, Clerk/local authorization boundary, ownership model, receipt association design, template catalog contract, migration plan | Record confirmed Clerk and integration-target choices; confirm amount convention, package names, and capability defaults; turn requirement IDs into tracked tasks |
| M1 — Tenant foundation | Clerk-hosted login/session handoff, local roles, identity lifecycle processing, API onboarding, account credentials, scoped keys, v2 STK, compatible migration, shared outbox primitives | Hosted login reaches owner APIs; Clerk token/role/lifecycle checks pass; two merchants and both payment environments pass isolation tests; existing STK integrity and legacy recovery checks pass |
| M2 — C2B | Registration, validation, confirmation, typed evidence, reference matching, STK/C2B receipt association | Registered sandbox flow and deterministic duplicate/out-of-order tests pass; unmatched payments remain visible; masked identities work |
| M3 — B2C | Recipients, limits/reservations, durable dispatch, result/timeout processing, query recovery | Individual sandbox transfer settles with verified evidence; concurrency and ambiguous-send tests prove no automatic duplicate payout |
| M4 — Events and recovery | Signed delivery, retries/replay, recovery APIs, runbooks and observability | Worker/Redis outage loses no committed event; signature, SSRF, tenant-isolation, and replay tests pass |
| M5 — SDKs and release validation | Python/TypeScript packages, Manual setup / Agent setup templates for all five targets, catalog/render APIs and SDK helpers, executable guides, OpenAPI compatibility checks, release artifacts | All 140 template selections render correctly; clean-install manual examples execute/type-check with simulated provider behavior; prompts match the same contracts; all release checks and provider acceptance evidence recorded |

Documentation and regression tests are produced with each milestone. Shared event creation is built early; M4 completes delivery and operational behavior rather than adding notification requirements after payment flows are finished.

## 11. Acceptance and definition of done

### Required scenario coverage

| Scenario | Passing result | Requirements |
| --- | --- | --- |
| Cross-merchant and test/live access | No unauthorized lookup, list leak, mutation, callback attachment, replay, token reuse, or worker credential selection | TEN-01–07 |
| Clerk-hosted onboarding | A verified user completes the hosted login/session handoff and uses owner APIs; repeated merchant-creation requests preserve one merchant/owner assignment | TEN-02, TEN-09–10 |
| Invalid or misplaced identity token | Forged, expired, wrong-issuer/development-instance, or unauthorized-party tokens fail; valid identity without local membership grants no merchant access | TEN-09–10 |
| Local role or user removal | The next owner request loses access even while the Clerk token is otherwise valid; a collection key still cannot administer roles or initiate B2C | TEN-06–07, TEN-10 |
| Clerk lifecycle replay and ordering | Forged events are rejected; duplicate delivery has one effect; late updates do not restore a deleted identity; financial history remains intact | TEN-11 |
| All three APIs available to each merchant | One merchant can configure and use STK, C2B, and B2C without a feature-package selection or product-access upgrade. An account with only STK configured can use STK while C2B/B2C report their missing setup; once configured, the same merchant can use them with the appropriate keys and payout limits | TEN-04, TEN-06–08, B2C-02 |
| Same-key concurrent financial requests | One identity, one reservation when applicable, and at most one local dispatch; changed payload conflicts | PAY-01, PAY-03, B2C-02–04 |
| STK acknowledgement without callback | No premature success; unknown/query-success-without-evidence remains unresolved | PAY-04, §6 |
| C2B duplicate and masked-payer confirmation | One recorded collection; original evidence retained; no full-phone requirement | C2B-03–04 |
| C2B unknown reference | Confirmed financial record remains completed and unmatched until an audited association | C2B-02, C2B-05 |
| STK/C2B receipt observed in either order | One canonical collection and one financial-success event; original operation IDs remain usable | C2B-06, EVT-01–02 |
| Simultaneous payouts near a daily limit | Atomic reservations prevent overspend; no double release; unresolved capacity survives midnight | B2C-02–03 |
| B2C callback races acknowledgement | Callback resolves the precommitted originator ID safely; later acknowledgement cannot overwrite the outcome | B2C-04–05 |
| Crash/timeout after B2C sending starts | No blind resubmission; original identifiers remain available for reconciliation | B2C-04, B2C-06–07 |
| Successful query about unsuccessful/unknown transaction | Query success is not treated as successful money movement | B2C-07, §6 |
| Callback commit/processing failure | No false durable ACK; rollback preserves retryable evidence; terminal conflicts are quarantined | C2B-03, B2C-05, §8 |
| Merchant webhook crash/retry/replay | Committed event survives, signature verifies on raw body, event ID is stable, and replay changes no financial state | EVT-01–06 |
| Unsafe webhook host or DNS change | No request reaches blocked/private destination; redirects cannot bypass controls | EVT-05 |
| Upgrade and secret rotation with in-flight work | Historical IDs/audits still work; old callbacks resolve; no new tenant access or payout privilege leaks | §7, §9 |
| SDK installation and examples | Both clients work from clean environments without backend dependencies and preserve idempotency | DX-01–06 |
| Generated integration coverage | All seven nonempty feature combinations work across five targets, two modes, and two environments; unknown targets/versions fail with 422 | DX-07–11 |
| Manual/agent consistency | Both modes use the same SDK/API versions and selected flow contracts; manual examples execute/type-check, and prompts include matching implementation and acceptance instructions | DX-08–11 |
| Safe, reproducible guide rendering | A pinned selection produces stable output with placeholders only; rendering neither reads merchant records nor calls payment/account-mutation APIs; generated Next.js examples keep secrets server-side | DX-07, DX-09–10 |
| Feature selection and authentication boundary | Tailoring guides never changes TEN-08 access; merchant integration prompts require scoped payment keys and do not install the platform's Clerk application into the consuming project | TEN-06, TEN-08, DX-09 |

### Release completion checklist

- [ ] Every in-scope requirement has implementation and acceptance evidence, or an explicitly approved scope amendment.
- [ ] Backend tests use disposable real PostgreSQL/Redis where transactional behavior matters; provider failures and races use deterministic mocks/simulation.
- [ ] Existing regression coverage is retained; CI continues enforcing at least 90% application line coverage. Coverage is not a substitute for the financial and tenant-isolation scenarios above.
- [ ] Python and TypeScript clients pass integration, signature-verification, error-contract, and fresh-install checks.
- [ ] Clerk-hosted owner login, local merchant roles, identity lifecycle handling, and token/environment boundaries pass their acceptance scenarios.
- [ ] Manual setup snippets and Agent setup prompts are available through the catalog/render APIs and both SDKs for Python, TypeScript, FastAPI, Express, and Next.js server-side integrations.
- [ ] All 140 template render selections pass; manual examples execute/type-check against supported versions with simulated provider/network behavior; agent prompts pass contract/content checks without claiming automatic coding-agent correctness.
- [ ] Lint, format, migration, packaging, dependency, and OpenAPI/example checks pass.
- [ ] Actual Safaricom sandbox acceptance is recorded for supported capabilities. CI never sends real money. Live readiness remains merchant/account-specific.
- [ ] An integrator can complete Clerk-hosted owner authentication, API onboarding, and all three payment flows from the guides without a product dashboard.
- [ ] Every merchant has access to all three APIs without feature-package selection; account readiness and key permissions are explained and enforced separately.
- [ ] Limits, retention, monitoring, callback routing, credentials, and recovery runbooks are configured for the target deployment.
- [ ] Known provider limitations and remaining gaps are stated accurately; no claim of complete Paystack parity or exactly-once provider processing.

The previous remediation run reported 115 passing tests and 95.80% application line coverage using PostgreSQL/Redis and mocked provider behavior. That is a historical baseline from the earlier work, not a new test run for this PRD and not evidence that C2B/B2C are implemented or live-validated.

## 12. Clarifications for the product owner

These questions refine implementation. They do not reopen the confirmed API/SDK scope, merchant-owned credentials, C2B record-and-reconcile policy, individual automated B2C model, Clerk authentication, or template-generation choices. Resolved decisions are retained below so reviewers can distinguish them from remaining questions.

| Decision | Proposed starting point | Why it matters | Owner's answer |
| --- | --- | --- | --- |
| Product and SDK names | Keep the repository name temporarily; choose distinct Python/npm package names before M5 | Names affect imports, examples, registry availability, and release metadata | _To fill in_ |
| Owner authentication | Clerk application `app_3JMnuwEIXsPwtUo5l5zyq9OJPf5`; hosted login and minimal session handoff | Removes custom signup/password/recovery implementation while supporting owner APIs | **Resolved: Clerk-hosted authentication** |
| Merchant roles | Local PostgreSQL memberships/roles and locally issued integration keys | Separates human identity from payment permissions and merchant ownership | **Resolved: local roles; no Clerk Organizations** |
| Integration generation | Versioned, tested templates with Manual setup snippets and Agent setup prompts | Produces consistent feature-specific instructions without runtime AI costs | **Resolved: tested templates, both modes** |
| Integration targets | Python, TypeScript, FastAPI, Express, and Next.js server-side integrations | Defines template fixtures and SDK/example coverage | **Resolved: these five targets** |
| Deployment model | One hosted multi-merchant API with merchant-owned Daraja accounts; preserve local development | Determines TLS endpoints, onboarding access, hosting and operations | _To fill in_ |
| Money contract | Integer whole KES for v2; leave v1 wire format compatible | Prevents unit mistakes across SDKs and provider adapters | _To fill in_ |
| Provider accounts per environment | Data model supports multiple accounts; require explicit account ID for financial creation | Avoids accidentally choosing a shortcode with the wrong capabilities | _To fill in_ |
| Payout limits | Owner supplies per-transfer and daily amounts; live B2C disabled until set; Africa/Nairobi day boundary | No arbitrary safe monetary limit can be inferred from the repository | _To fill in_ |
| Webhook defaults | HMAC-SHA256, five-minute verification window, retries up to 72 hours | Affects SDK verification, storage, retries and operational expectations | _To fill in_ |
| Evidence retention | Set distinct policies for raw payloads, delivery bodies, financial records and audit records; preserve unresolved-case evidence | Required for storage planning, privacy and replay availability | _To fill in_ |
| Runtime support | Python sync/async; server-side TypeScript on agreed supported Node.js versions | Determines SDK scope and CI compatibility matrix | _To fill in_ |
| Hosting and service objectives | Select database/Redis host, encryption key manager, Clerk instance/origin configuration, traffic profile, and recovery targets | Needed for production capacity and cost estimates; the identity provider is already Clerk | _To fill in_ |
| First acceptance accounts | Identify business-owned sandbox/live accounts and enabled STK/C2B/B2C/status-query capabilities | Some provider registration and go-live steps cannot be completed through code alone | _To fill in_ |

Suggested change-request format:

> **Requirement ID:** B2C-02  
> **Keep/change/remove:** Change  
> **Desired behavior:** Add an owner-configured per-recipient daily cap.  
> **Acceptance example:** Two transfers to the same recipient must share that cap even if different API keys submit them.  
> **Release:** First release / later.

## 13. Reference basis and limitations

- The experience reference is Paystack's emphasis on APIs, documentation, and integration tools. No Paystack website implementation is required. [Paystack developers](https://paystack.com/ke/developers), [API overview](https://paystack.com/docs/api/), [developer tools](https://paystack.com/docs/developer-tools/)
- Test/live separation and clear secret-key usage are useful reference patterns. Proposed scopes, route names, amount units, and owner flows in this PRD are this product's choices. [Paystack authentication](https://paystack.com/docs/api/authentication/)
- Webhook behavior is informed by established payment integration patterns, but this PRD's signature scheme is a proposed contract, not Paystack's wire protocol. [Paystack webhooks](https://paystack.com/docs/payments/webhooks/)
- Daraja adapter behavior must follow the current official C2B, B2C, and transaction-status contracts and the actual merchant's enabled capabilities. The official pages were reviewed during the product discussion on 15 September 2026. Recheck them when implementing provider contracts and before live rollout. [C2B](https://developer.safaricom.co.ke/apis/CustomerToBusiness), [B2C](https://developer.safaricom.co.ke/apis/BusinessToCustomer), [transaction status](https://developer.safaricom.co.ke/apis/TransactionStatus)
- Pull Transaction is a possible later recovery capability; it is not committed in this release. [Safaricom Pull Transaction](https://developer.safaricom.co.ke/apis/PullTransaction)
- Clerk's hosted Account Portal and official Python backend SDK provide the authentication integration basis. Local merchant roles and locally issued payment keys are this product's confirmed authorization choices. [Clerk Account Portal](https://clerk.com/docs/guides/account-portal/getting-started), [Clerk Python SDK](https://github.com/clerk/clerk-sdk-python), [Clerk lifecycle webhooks](https://clerk.com/docs/guides/development/webhooks/syncing)
- The supplied Clerk setup attachment identifies application `app_3JMnuwEIXsPwtUo5l5zyq9OJPf5` and the CLI setup sequence. Its web-framework examples are setup guidance, not a request to replace FastAPI or build a developer website. The screenshot's Agent setup / Manual setup labels inform the two output modes; the renderer uses tested templates, not runtime AI generation. [Clerk CLI](https://clerk.com/docs/cli)

## 14. Document change log

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 15 September 2026 | Initial PRD grounded in the STK repository and corrected API/docs/SDK scope; C2B/B2C, tenant isolation, webhooks, migration, acceptance criteria, and owner clarifications defined |
| 0.2 | 15 September 2026 | Confirmed STK, C2B, and B2C access for every merchant without feature-package selection; retained account-specific setup, scoped keys, and payout limits; added acceptance criteria |
| 0.3 | 15 September 2026 | Confirmed Clerk-hosted authentication with local merchant roles; specified Manual setup SDK snippets and Agent setup prompts from tested templates for Python, TypeScript, FastAPI, Express, and Next.js server-side integrations; added API/data requirements, milestone updates, resolved decisions, and acceptance coverage |
