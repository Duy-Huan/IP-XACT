# Hosted CI observations over Git transport

Cloud REST API access and Git source transport have different network policies.
The workflows publish their actual required-job conclusions in refs/notes/usp-ci
and source release outcomes in refs/notes/usp-release. These are Git notes, not
additional development branches. Scoped job credentials are process environment
only. Notes are pushed without forcing remote history; concurrent writers retry.

```sh
usp ci status --kind regression --commit HEAD --run-ref refs/heads/main
usp ci status --kind source-release --commit v0.3.0-alpha.4 --run-ref refs/tags/v0.3.0-alpha.4
```

Use --wait-seconds 3600 for a pending run, --repository OWNER/REPO to constrain
repository identity and --no-fetch for an offline reading. Missing notes are
UNOBSERVED. Fetch/shape/hash errors return ERROR; they never imply success.
Every record binds the exact source commit, run/attempt/ref, GitHub run URL,
required job inventory and UTC timestamp. A skipped required job is incomplete.
Regression requires workflow-lint, branch-policy, regression and custom-zephyr;
manual dispatch also requires gem5-se. Failed jobs include bounded actual log
excerpts when GitHub permits retrieval; absent logs remain explicitly unavailable.

Source-release success is recorded only after the publishing job succeeds and
includes actual source asset names, byte counts and SHA-256 hashes. This is a
repository-published report (HOSTED_REPORTED_SUCCESS/FAILURE/INCOMPLETE), not an
independent REST API attestation or proof that a hosted runner cold-built every
native simulator. Local mandatory qualification, hosted minimal jobs, source
assets, SDK images and cold/Podman acceptance remain distinct evidence scopes.
Anyone with repository write access can write notes; use the linked Actions run
for independent review. Never publish simulated test receipts to the real origin.
