# Native qualification: milestone 16 remains open

Application support reporting and the production qualification gate are
implemented. Native APT/Homebrew qualification is **not complete**. After starting
the installed Docker Desktop, real APT smoke, upgrade, controlled failure,
cached-origin and repository URL checks
passed in disposable Ubuntu 24.04 x86_64 containers. Package changes were confined
to those containers, with no host mounts or ports, and they were removed after
testing. Existing personal WSL distributions were not used. No macOS
qualification host is connected.

## Support gate

`services/support.py` separates command-only, read-only, unqualified and
unsupported workflows. `DesktopServices` defaults to requiring native
qualification. It installs the same policy in `Planner` and `OperationService`,
so Explore, Installed, presets and previously prepared plans cannot bypass the
gate. Read-only inventory and verification remain available. Manager absence,
unsupported action and pending qualification have distinct explanations.

The source currently contains no reviewed native mutation qualification records.
Nothing claims a fully qualified action. Direct core APIs retain their existing
experimental behavior when a caller supplies no policy. Tests and the guarded
smoke runner explicitly compose `DesktopServices` with
`require_native_qualification=False`; the normal desktop exposes no such toggle.
A report is evidence for review, never an executable configuration or automatic
permission to enable production actions.

## Disposable smoke workflow

`.github/workflows/native-qualification.yml` is separate from release publishing.
It has only a manual `workflow_dispatch` trigger and requires affirmative approval
of native changes, including selected-package removal and reviewed dependencies.
It does not run on pushes, pull requests or ordinary unit-test discovery.

- APT: a digest-pinned Ubuntu 24.04 job container on an `ubuntu-24.04` hosted runner.
  The visible image-preparation step refreshes repositories and installs Python,
  certificates and Git. The application smoke itself never refreshes repositories
  or bootstraps a package manager. The smoke selects the reviewed CMake mapping.
  The separate `--apt-upgrade` profile selects reviewed Git, lists available native
  versions, uses `dpkg --compare-versions` to find an older candidate, and validates
  its provenance through the actual APT inspector. It installs that exact version,
  upgrades to the current reviewed candidate, verifies the changed version in
  inventory and application refresh, and removes the selected package. Missing
  older versions or a no-op fail the upgrade profile instead of counting as success.
  The separate `--apt-failures` profile uses CMake to test cancellation during an
  active download, durable recovery and a controlled exit-zero verification race.
  Containers use `--init` so adopted native helpers are reaped.
  The `--apt-origins` profile copies the native list cache into a temporary
  directory, redirects native APT reads there and tests altered cached origins.
  Its runner refuses all mutation dispatch, even with approval. It requires
  CMake to be absent so the initial plan represents a genuine installation.
  The `--apt-sources` profile additionally copies source manifests and changes
  their URLs without refreshing the copied lists. Its runner also refuses
  mutations, and CMake must be absent.
- Homebrew: a `macos-15` hosted ARM64 runner with its existing Homebrew. It selects
  the reviewed ripgrep core formula; no manager/tap bootstrap is provided. Hosted
  images contain preinstalled software, so this is not clean-machine evidence.
- Both: `python -m scripts.qualify_native --backend <apt|homebrew>
  --approve-native-changes --output <new-report.json>`. Hosted runs require
  `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted`. APT additionally
  requires a root Ubuntu 24.04 x86_64 container; an explicitly marked local
  disposable container (`DEV_SETUP_DISPOSABLE_CONTAINER=1`) is also accepted for
  APT only. Homebrew still requires a non-root hosted macOS ARM64 runner. These
  are explicit test-environment checks, not authentication against a hostile caller
  forging environment facts. Unmarked local, ordinary host and self-hosted macOS
  runs are refused.

The runner selects only its fixed reviewed package/source. Explicit approval
includes removing that test package if preinstalled on the disposable runner;
it never uses force/dependency bypasses. It exercises inventory, candidate review,
missing-consent refusal, cancellation before dispatch, install, verify, application
inventory refresh, removal, and rejection of a plan made stale by real removal.
It uses the actual application planner, command runner and operation verifier.
Unexpected/truncated native output, candidate drift or unsupported host/source
fails the run. No native command or arbitrary package ID is taken from inputs.

The runner creates a new bounded JSON report exclusively, without overwriting an
existing file. It records the actual OS/version, architecture, parsed manager
version, source/native ID, bounded candidate version, catalog digest, source
commit, per-check status and failure phase. It excludes raw output, local paths,
environment dumps and arbitrary exception messages. APT installed inventory does
not prove historical origin; the report records that limitation explicitly.
Approved manual CI jobs retain these metadata artifacts; nothing uploads from the
desktop or ordinary tests.

Even an exit-zero report says `qualified: false` and
`smoke_passed_qualification_pending`. Latest-to-latest upgrade review in the default
smoke is not a real upgrade and stays untested. Only the explicit APT upgrade
profile can record a real version change as passed. The failure profile covers
download cancellation only, not interruption during DPKG unpack/configure. Native
elevation prompts and casks remain unqualified. A failure can leave package
changes on the disposable host; no native rollback is claimed.

## Retained APT evidence (2026-11-15)

| Scope | Observed result |
| --- | --- |
| Environment | Ubuntu 24.04, x86_64, Python 3.12.3, APT 2.8.4, root container |
| Base image | `ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5` |
| CMake | `3.28.3-1build7` installed, verified, refreshed and removed |
| Git | `1:2.43.0-1ubuntu7` installed; upgraded to `1:2.43.0-1ubuntu7.4`; verified, refreshed and removed |
| Safety checks | Missing consent, pre-dispatch cancellation and stale removal plans rejected |
| Remaining limits | Historical installed origin not proved by DPKG; native elevation, in-flight cancellation, altered-source/failure injection and macOS/casks not qualified |

Reviewed bounded reports: [CMake smoke](evidence/apt-ubuntu24.04-x86_64-smoke-2026-11-15.json)
and [Git upgrade](evidence/apt-ubuntu24.04-x86_64-upgrade-2026-11-15.json).
Both retain `qualified: false`; they support these observed cases only.
Their `source_commit` identifies the base commit before the tested working changes.
`source_tree_digest` binds that working snapshot: SHA-256 of compact UTF-8 JSON
containing sorted `(relative tracked filename, SHA-256 content)` pairs under
`app/`, `application/`, `models/`, `system/`, `package_managers/`, `runtimes/`,
`persistence/`, `services/`, `catalog/`, `scripts/`, `tests/`, plus `pyproject.toml`.
The snapshot excludes symlinks, local environment files, caches and host paths.
No generated archive or raw native output is committed.

## Retained APT failure evidence (2026-11-16)

The [CMake failure report](evidence/apt-ubuntu24.04-x86_64-failures-2026-11-16.json)
records a separate root Ubuntu 24.04 x86_64 run with APT 2.8.4, Python 3.12.3 and
the same pinned image. It retains `qualified: false`.

- A fixed process-local download rate limit made cancellation observable while
  a direct, live `apt-get` child was downloading. The owned child was reaped and
  no live process remained in its group. No cancellation during DPKG unpack or
  configure is claimed. Docker init reaps adopted helpers.
- The cancelled lifecycle record survived closing and reopening SQLite. Retry
  was refused until explicit native inspection found the prior inventory and
  the user acknowledgement was supplied. Reconciliation preserved the cancelled
  outcome. A fresh reviewed plan using normal APT then installed and removed CMake.
- A controlled native removal ran after a genuine exit-zero install and before
  verification. The install correctly failed with `verification_failed`, retained
  exit code zero and emitted no `PackageInstalled` event. This was deliberate
  fault injection, not a spontaneous native package-manager failure.

The approved failure scope includes cleanup and that controlled removal. It
does not edit repository configuration or persist the rate limit. Native
elevation, altered configured origins, macOS and casks remain unqualified.

## Retained APT cached-origin evidence (2026-11-16)

The [origin report](evidence/apt-ubuntu24.04-x86_64-origins-2026-11-16.json)
records a root Ubuntu 24.04 x86_64/APT 2.8.4 run on the same pinned image.
The native `apt-cache` reader observed altered Origin fields in a private copy
of cached InRelease files. Both fresh planning and execution of a previously
reviewed plan rejected the unreviewed origin. Execution failed before any native
mutation dispatch, with no exit code. Restoring the copied bytes restored the
original native review. Original lists and repository configuration were not edited.

APT reads explicitly select the private lists and disable shared binary caches,
preventing stale binary metadata from hiding the injected change. The temporary
copies are removed afterward, including on failure. No update, download, install
or signature-check bypass runs in this profile. Image preparation remains the
separate visible container step.

This deliberately corrupts already-cached metadata. It tests application origin
checks, not signature verification, repository URL changes, key replacement or
APT's handling of a newly published repository. Those broader source checks
remain open. The report remains `qualified: false` and does not enable mutations.

## Retained APT repository URL evidence (2026-11-16)

The [source report](evidence/apt-ubuntu24.04-x86_64-sources-2026-11-16.json)
records a root Ubuntu 24.04 x86_64/APT 2.8.4 run on the same pinned image.
APT reads selected private copies of both cached lists and source manifests.
Only `.list` and `.sources` manifests are copied; auth files and key material
are excluded. Existing `Signed-By` references retain the original native keys.

After a successful initial install review, the test changed copied repository
URLs to a reserved invalid domain, with no list refresh or network request.
Native `apt-cache policy` then stopped exposing the old Ubuntu repositories.
Fresh planning and previously reviewed execution both failed before mutation
dispatch. In this environment the native selected-release query exited nonzero;
the operation preserved `command_failed`, with no mutation exit code. The harness
also accepts a parsed `verification_failed` rejection. Restoring the original
copied manifests restored the original review.

The runner refuses every mutation dispatch, including approved commands. Original
source manifests and cached lists were not edited. All changes were confined to
temporary copies inside a disposable container, removed after testing. This tests
URL changes against an existing cache; signing-key changes and newly downloaded
repository metadata remain unqualified. The report remains `qualified: false`.

## Repeat the local APT profiles

From a reviewed committed checkout with Docker's Linux engine running, the
following PowerShell recipe explicitly approves package changes only inside the
new disposable container. It copies a tracked archive and uses no host mounts.
Repository preparation is visible and confined to that container. Review native
dependency effects before using this test scope.

```powershell
New-Item -ItemType Directory -Force build | Out-Null
git archive HEAD -o build/native-source.tar
$qualificationCommit = (git rev-parse HEAD).Trim()
$qualificationImage = 'ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5'
docker pull $qualificationImage
$qualificationContainer = (docker run --detach --rm --init --env DEV_SETUP_DISPOSABLE_CONTAINER=1 $qualificationImage sleep 7200).Trim()
if ($qualificationContainer -notmatch '^[a-f0-9]{64}$') { throw 'Container creation failed.' }
try {
    docker exec $qualificationContainer apt-get update
    docker exec --env DEBIAN_FRONTEND=noninteractive $qualificationContainer apt-get install --yes python3 ca-certificates
    docker cp build/native-source.tar "${qualificationContainer}:/tmp/native-source.tar"
    docker exec $qualificationContainer mkdir /work
    docker exec $qualificationContainer tar -xf /tmp/native-source.tar -C /work
    docker exec --env DEV_SETUP_SOURCE_COMMIT=$qualificationCommit --workdir /work $qualificationContainer python3 -m scripts.qualify_native --backend apt --approve-native-changes --output /tmp/native-apt.json
    if ($LASTEXITCODE -ne 0) { throw 'APT smoke failed; inspect its bounded report.' }
    docker exec --env DEV_SETUP_SOURCE_COMMIT=$qualificationCommit --workdir /work $qualificationContainer python3 -m scripts.qualify_native --backend apt --apt-upgrade --approve-native-changes --output /tmp/native-apt-upgrade.json
    if ($LASTEXITCODE -ne 0) { throw 'APT upgrade failed; inspect its bounded report.' }
    docker exec --env DEV_SETUP_SOURCE_COMMIT=$qualificationCommit --workdir /work $qualificationContainer python3 -m scripts.qualify_native --backend apt --apt-failures --approve-native-changes --output /tmp/native-apt-failures.json
    if ($LASTEXITCODE -ne 0) { throw 'APT failure checks failed; inspect their bounded report.' }
    docker exec --env DEV_SETUP_SOURCE_COMMIT=$qualificationCommit --workdir /work $qualificationContainer python3 -m scripts.qualify_native --backend apt --apt-origins --approve-native-changes --output /tmp/native-apt-origins.json
    if ($LASTEXITCODE -ne 0) { throw 'APT origin checks failed; inspect their bounded report.' }
    docker exec --env DEV_SETUP_SOURCE_COMMIT=$qualificationCommit --workdir /work $qualificationContainer python3 -m scripts.qualify_native --backend apt --apt-sources --approve-native-changes --output /tmp/native-apt-sources.json
    if ($LASTEXITCODE -ne 0) { throw 'APT source checks failed; inspect their bounded report.' }
} finally {
    try {
        docker cp "${qualificationContainer}:/tmp/native-apt.json" build/native-apt.json
        docker cp "${qualificationContainer}:/tmp/native-apt-upgrade.json" build/native-apt-upgrade.json
        docker cp "${qualificationContainer}:/tmp/native-apt-failures.json" build/native-apt-failures.json
        docker cp "${qualificationContainer}:/tmp/native-apt-origins.json" build/native-apt-origins.json
        docker cp "${qualificationContainer}:/tmp/native-apt-sources.json" build/native-apt-sources.json
    } finally {
        docker stop $qualificationContainer
    }
}
```

The committed-checkout recipe records its exact commit. The retained local
reports additionally bind a modified source snapshot using the digest defined
above. Native repository availability can change; unavailable older versions must
fail the upgrade profile rather than bypass source checks or substitute a package.

## Remaining acceptance gates

1. Run and inspect the hosted jobs, especially macOS. The local APT container runs
   above passed; the hosted workflow itself has not been dispatched here.
2. Reproduce install/update/remove on clean disposable Ubuntu 24.04 and macOS
   ARM64 images, with controlled older/newer versions from reviewed sources.
   APT Git now has real root-container upgrade evidence. A current-version no-op
   must never count as upgrade evidence, and macOS remains untested.
3. Qualify native elevation/consent and cancellation beyond APT downloads,
   including DPKG transaction interruption and equivalent Homebrew recovery.
   APT download cancellation, post-cancellation inventory, lifecycle reopening,
   reconciliation and fresh retry passed in the retained failure run.
4. Qualify Homebrew casks separately; formula success does not qualify casks.
5. Inspect source/version identity, altered-source/candidate rejection and
   verification failure on those hosts. APT now has controlled exit-zero
   verification-failure and private cached-origin rejection evidence. Repository
   URL changes against an existing cache also passed. Signing-key changes,
   newly downloaded source metadata and Homebrew cases remain open.
   Resolve any real parser/output limits.
6. Review evidence per backend, source, OS/release, architecture, native version
   and action before adding any production enablement. Update the support matrix
   and leave unsupported scopes disabled.

The [GitHub runner image list](https://github.com/actions/runner-images#available-images)
documents the selected OS/architecture labels. Native command behavior is
documented in the [Homebrew manual](https://docs.brew.sh/Manpage) and
[Ubuntu noble APT manual](https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html).
These references describe environments and commands; they are not project test
evidence. No dependency or release-publishing change is introduced.

## Local validation (2026-11-16)

`.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q`
ran **554 tests OK, one POSIX-only skip on Windows**. Qualification unit tests
use fake managers; the separate real runs are recorded above. Production GUI tests confirm
disabled mutation controls, explanatory reasons and enabled read-only verify.
Ruff lint/format passed (170 files), mypy passed (101 source files), and catalog
validation passed (14 packages/39 mappings).

The same source snapshot ran **133 tests OK** on Ubuntu/Python 3.12.3:

```sh
python3 -W error::ResourceWarning -m unittest tests.unit.test_services tests.unit.test_inspection tests.unit.test_lifecycle tests.unit.test_native_qualification tests.unit.test_native_support tests.unit.test_apt_failure_checks tests.unit.test_apt_origin_checks tests.integration.test_command_runner tests.integration.test_native_coordination tests.integration.test_state_recovery tests.integration.test_core_import -q
```

These core tests use synthetic mutations; they complement the five real APT profiles.
Qt and the full Linux dependency matrix were not exercised by this subset.
