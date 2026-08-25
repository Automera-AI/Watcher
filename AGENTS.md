# AGENTS.md — Watcher build loop

This repo is built with an AI-agent-assisted loop (Claude Code / Codex implement; the founder writes specs,
reviews, integrates, and tests against real WhatsApp traffic). Read this before implementing.

## Sources of truth (read first, every time)

1. `docs/build-spec-addendum.md` — engineering decisions, data model, and the resolution of the v1.2
   identity-resolution contradiction. **Do not re-derive architecture; it's settled here.**
2. `DESIGN-SPEC.md` — every control-page screen is built against this. Never invent styles per screen; never
   hardcode a color — reference a token. Propose new tokens in the spec first.
3. The MVP v1.2 PDF — product/strategy intent (the "why").

## Rules

- **One language server-side: Python.** TypeScript only in `apps/control-page` (framework constraint, v1.2 §11).
- **Pydantic v2 schemas are the single source of truth** — the same models back the LLM structured output, the
  DB row, and the REST contract. Build them first.
- **Eval-tool-first discipline:** no prompt change merges without an eval run. CI blocks merges that drop overall
  accuracy >2pp (v1.2 §12).
- **Multi-tenancy is non-negotiable:** every business table carries `tenant_id` with RLS. Never write a query
  that can cross tenants.
- **Self-hosted constraint:** no runtime external CDN/font/icon calls anywhere — the regulated tier forbids data
  egress (v1.2 §10).
- **Nothing from the marketing website** is copied into this repo.

## Build order

See `docs/build-spec-addendum.md` §18. Briefly: schemas → webhook receiver → classifier + eval baseline →
control page → media pipeline → identity resolution → rules → dedup sweep.

## Spec-per-deliverable

Each deliverable starts from a 1–3 page spec naming the module boundary, the input/output schema, the error
cases, and the test fixtures. Vague specs cost three iterations; precise specs land in one (v1.2 §14).

# Repository Instructions

## Code Review Rules

### Tenant isolation

- Flag any database query, cache operation, background job, or outbound message operation that accesses tenant-owned data without explicitly scoping it to the resolved tenant.
  Safe path: resolve the tenant from a trusted authenticated identity or the verified WhatsApp Phone Number ID, then include `tenant_id` in every relevant query and operation.

- Do not trust a `tenant_id` received directly from webhook data, request bodies, query parameters, or other user-controlled input.
  Safe path: derive the tenant from the authenticated principal or an enabled `channel_configs` endpoint mapping.

### Webhook idempotency

- Meta may deliver the same webhook more than once. Flag any change that can enqueue, process, or reply to the same WhatsApp message multiple times.
  Safe path: deduplicate using the Meta message ID before performing side effects, preferably with a durable database uniqueness constraint.

- Repeated delivery of an already accepted webhook must return a successful response without sending another reply.

### Authentication and authorization

- Flag every new or modified administrative endpoint that does not verify both authentication and authorization for the affected tenant or resource.

- Authentication alone is insufficient. A logged-in user must not be able to access another tenant by changing an ID in the URL, request body, or query parameters.
  Safe path: verify resource ownership against the authenticated tenant before reading or modifying it.

### Database migrations

- Flag migrations that remove or rename columns, change existing meanings, add non-nullable columns without a safe backfill, or otherwise break the currently deployed application.
  Safe path: use an expand, migrate, contract sequence so old and new application versions can run during deployment.

- Flag migrations or application changes that can leave related database writes partially completed.
  Safe path: use an explicit transaction where the operation must succeed or fail as one unit.

### API compatibility

- Flag changes that remove, rename, or change the type or meaning of an existing public API field without an explicit versioning or migration plan.
  Safe path: prefer additive, backwards-compatible changes and retain existing fields until consumers have migrated.

- Changes to webhook acknowledgement behaviour must continue returning a prompt successful response after the event has been safely accepted.

### Sensitive data and logging

- Flag logs containing access tokens, authorization headers, database passwords, app secrets, raw credentials, or complete webhook payloads.

- Flag unnecessary logging of customer phone numbers, message contents, or other personal data.
  Safe path: use structured logs containing internal identifiers, event types, masked values, and error codes.

### Tests

- Flag behaviour changes to webhook parsing, tenant resolution, deduplication, message state transitions, queue processing, or outbound message delivery when no corresponding tests are added or updated.

- Bug fixes should include a regression test that fails before the fix and passes after it.
