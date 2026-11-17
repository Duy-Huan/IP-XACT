# Local CI before publishing

The owner requires relevant local checks to pass before another push. Commit the
candidate locally, record its SHA, run workflow lint, the hosted minimal Debian
job and the required native gate, then publish only that tested tree. Local
commits and checked local promotion do not publish. Hosted status is a separate
observation; unavailable GitHub logs must not be called reviewed or successful.

## Workflow contracts

scripts/ci_lint.sh uses actionlint v1.7.9 and ShellCheck 0.10.0 plus Bash syntax
checks. The workflow-lint job installs the pinned module and Debian packages.
The project configuration declares the real soc-eda self-hosted label; dynamic
provisioned environment sources are annotated individually, not globally ignored.
All jobs explicitly use Bash with pipefail, Ubuntu hosted jobs use ubuntu-24.04,
and Debian jobs use the Containerfile's verified immutable base digest.

Checkout v5.0.1 and upload-artifact v6.0.0 are pinned to commits whose action.yml
was inspected for Node.js 24. Upload-artifact v5.0.0 still uses Node.js 20 and
would not fix that warning. Self-hosted runners need the current Node-24-capable
Actions runner. Updating actions or runner labels does not explain a failing
test/build without its actual error output.

## Reproduce the minimal hosted job

Activate the frozen Python environment to emit the actual run blocks directly
from .github/workflows/regression.yml. The generator deliberately supports only
regression/custom-zephyr and never emits publish/upload actions. Checkout is
represented by the committed source archive; the package tests do not depend on
that archive containing the source repository's .git metadata.

```sh
source .venv/bin/activate
ci_result="$PWD/results/ci-basic-new"
mkdir -p "$ci_result"
python scripts/emit_ci_job.py regression > "$ci_result/job.sh"
git archive HEAD | podman run --rm -i --volume "$ci_result:/results:Z" \
  debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 \
  bash /results/job.sh > "$ci_result/output.log" 2>&1
```

The script retains exit-code, JUnit and available build logs, including failures.
Use Docker's ordinary volume syntax without :Z on a Docker host. A managed proxy
may require mounting its supplied public CA bundle and configuring PIP_CERT /
SSL_CERT_FILE; keep certificate verification enabled. That local trust setup is
not a diagnosis of a hosted failure.

The clean Debian reproduction found a missing procps/ps dependency for process
cleanup tests. The clean custom source build also failed QEMU configuration
because bzip2 was missing; source-build jobs and the SDK now declare it explicitly.
It also exposed a FreeRTOS negative test depending on the root
repository's .git; that test now constructs a real independent wrong-revision
checkout. The latter archive issue is distinct from GitHub's actual checkout.
Minimal CI intentionally skips native gates whose builds it does not provision;
its result is not complete platform acceptance.

## Required native promotion gate

After activating the documented provisioned toolchain environment, set every
verified native root required by scripts/promote.sh, including both generated
native SoCs, their newly built native gem5 engine and USP_COHERENT_BUILD. Run:

```sh
bash scripts/ci_lint.sh
bash scripts/promote.sh
```

Promotion requires a clean dev checkout, enforces all native flags and runs the
complete suite before merging locally into main. Required native tests must not
skip. Retain raw logs, JUnit, failed attempts and exact tested source/artifact
identities. `usp soc accept` must execute the actual new QEMU/gem5 instance graph
with the same ELF/IR; older fixed-profile passes cannot qualify it. Git push is a
separate action after the local gate, not an automatic side effect of a script.

The native topology/model upgrade additionally requires USP_REQUIRE_NATIVE_SMP,
USP_REQUIRE_NATIVE_EXTERNAL, USP_NATIVE_AI_SOC_SMP_BUILD,
USP_NATIVE_AIZ_SOC_SMP_BUILD, USP_NATIVE_MAX_HART_BUILD and
USP_NATIVE_EXTERNAL_ROOT. scripts/native_soc_bringup.sh builds the source models,
SMP/Linux artifacts and external example needed for its three native test modules.
The referenced source-IP model root must already be verified; this script does
not implicitly download any private or public source tree.
