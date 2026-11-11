---
name: update
description: Update platform dependencies and model profiles with reproducible regression.
---

# Update workflow

Implements agent.md §§3–6. Read agent.md, docs/roadmap.md and the compatibility
boundary first. Work on `dev`, preserve user changes, and use the existing checkout.
No automatic updates, pushes, releases, or company data uploads.

1. Identify the exact target: Python dependency, schema, simulator, IP model, firmware
   or OS source. Record old/new immutable identities, licenses and API changes using
   authoritative version-matched source. Never trust version claims in sample docs.
2. Keep the existing working profile. Evaluate candidate source/build outputs in
   ignored scratch or external cache. Preserve TLS, signatures and checksums.
3. For Python changes, edit pyproject deliberately, resolve `uv.lock`, then prove
   `uv sync --frozen --extra dev` works. For schema changes, retain notices and update
   every provenance checksum, positive and negative XSD/semantic tests.
4. For engine changes, update a candidate project profile and inspect actual upstream
   machine/device semantics, IRQ/reset/map and image loading. Rebuild affected native
   outputs. Record source SHA/patches/flags and actual binary identity. Do not merely
   rewrite a version string to silence mismatch errors.
5. Run `bash scripts/regression.sh`, real relevant engine/firmware checks and any
   compatibility migration tests. Failed checks require diagnosis; missing required
   tools block promotion. No synthetic timing, boot or cross-engine-equivalence claims.
6. Update changelog, capability/limitations and compatibility docs. Commit on dev;
   promote with `scripts/promote.sh` or a checked dev→main PR. Hosted branch protection
   is an administrator setting, not enforced by text alone.

Rollback selects a prior immutable commit/profile; never rewrite published tags.
Report current-machine validation separately from hosted CI and environment
snapshot publication. Keep proprietary IP, keys and firmware outside public Git.

## Generated artifact identity and source-backed evidence

The current custom path hashes compiler modules, templates, catalog, inputs and
outputs. Regenerate into a fresh directory after any such identity changes and
rebuild affected native artifacts. Never rewrite manifest checksums manually to
silence a mismatch. Valid native compilation outputs can be reused by an explicit
rebuild when their actual generated source/configuration inputs remain unchanged.

Require the real custom Zephyr timer/PLIC test before promotion. Linux userspace,
shared gem5 SE workload and TLM/RTL bridge each have independent acceptance scopes.
Report SE as SE, transport time as annotated, and unsupported generic hierarchy or
security semantics as unsupported. Keep external source licenses and clean tracked
upstream state. Raw attached company objectives must not be copied into public Git.
