# Native qualification: milestone 16 remains open

The interface migrated to a [terminal application](tui.md) on 2026-11-18.
The native evidence below retains its original tested core snapshots.
Application support reporting and the production qualification gate are
implemented. Native APT/Homebrew qualification is **not complete**. After starting
the installed Docker Desktop, real APT smoke, upgrade, controlled failure,
cached-origin, repository URL, signing-key drift and fresh-index authentication checks
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
of native changes, including selected-package removal, reviewed dependencies and
private signed-index refreshes in the authentication profile.
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
  The separate `--apt-dpkg` profile runs in its own fresh container/job. It pauses
  a verified owned DPKG child during CMake unpack, requests application
  cancellation and checks partial-state/recovery refusal. It deliberately leaves
  native package state requiring repair; discard that container after collecting
  its report. Never append this profile to a reusable smoke container sequence.
  The separate `--apt-dpkg-repair` profile additionally approves configuring
  pending native packages and reinstalling the original exact CMake candidate,
  followed by explicit recovery acknowledgement and fresh install/remove checks.
  The two DPKG modes use separate fresh containers through the manual job matrix.
  The `--apt-origins` profile copies the native list cache into a temporary
  directory, redirects native APT reads there and tests altered cached origins.
  Its runner refuses all mutation dispatch, even with approval. It requires
  CMake to be absent so the initial plan represents a genuine installation.
  The `--apt-sources` profile additionally copies source manifests and changes
  their URLs without refreshing the copied lists. Its runner also refuses
  mutations, and CMake must be absent.
  The `--apt-keys` profile copies the Ubuntu archive public key and rewrites only
  private manifests to reference the copy. It tests key content/reference drift
  against unchanged cached candidate metadata, refusing all mutation dispatch.
  The `--apt-auth` profile downloads fresh noble/main amd64 indexes into isolated
  caches over HTTPS. It tests current-key acceptance and removed-key rejection,
  then serves altered signed metadata from a loopback-only fixture to test
  `BADSIG` rejection. It also corrupts a previously authenticated cached release
  to check fresh-plan/stale-approval rejection and byte restoration without a
  refresh. Decoded package indexes must match signed SHA-256 and sizes. A controlled
  reader clock also checks signed time bounds without changing the system clock.
  There is no package installation or reuse of a preexisting host cache.
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

## Retained APT signing-key drift evidence (2026-11-16)

A native probe showed that changed `Signed-By` input could leave the old cached
candidate review unchanged. The core now includes native source manifests and
referenced key contents in the install/upgrade review fingerprint, with snapshots
before and after inspection. Execution's existing fresh-review comparison rejects
changed trust inputs before dispatch. The bounded reader and supported input
forms are documented in [operation services](operations.md).

The [key report](evidence/apt-ubuntu24.04-x86_64-keys-2026-11-16.json) records real
Ubuntu 24.04 x86_64/APT 2.8.4 checks using private manifests, lists and public-key
copies. Both changing key bytes at the same path and selecting an identical key
copy at another path invalidated stale approval before mutation dispatch. The
cached candidate version and transaction effects stayed unchanged. Restoring
the private inputs restored the original review fingerprint.

Fresh planning can describe the changed trust identity; fingerprinting does not
authenticate the replacement key or validate cached release signatures. Inline
keys, global trust, fingerprint-only selectors and paths outside the reviewed
roots fail closed. This is drift detection, not complete signature qualification.
No original key, source manifest or cached list was edited.

The same source snapshot passed the native
[CMake install/remove smoke](evidence/apt-ubuntu24.04-x86_64-smoke-trust-2026-11-16.json),
[altered cached-origin check](evidence/apt-ubuntu24.04-x86_64-origins-trust-2026-11-16.json)
and [repository URL check](evidence/apt-ubuntu24.04-x86_64-sources-trust-2026-11-16.json).
The latter now rejects during source identity inspection with
`verification_failed`; earlier evidence retained the native `command_failed`.
All reports remain unqualified, and production mutation gates stay disabled.

## Retained APT fresh-index authentication evidence (2026-11-16)

The [authentication report](evidence/apt-ubuntu24.04-x86_64-auth-2026-11-16.json)
records a root Ubuntu 24.04 x86_64/APT 2.8.4 run on the pinned image. Two exclusive,
initially empty private scopes use the same official HTTPS noble/main amd64
source and different public keyrings from the image's installed Ubuntu keyring
package. No original sources, keys or cached indexes are edited.

- With `ubuntu-archive-keyring.gpg`, native `apt-get --error-on=any update`
  authenticated fresh indexes. The application planner then produced a review
  for CMake `3.28.3-1build7` using those indexes.
- With only `ubuntu-archive-removed-keys.gpg`, APT exited 100 with `NO_PUBKEY`.
  No usable package index was retained and application planning was blocked.
  Network failures, truncated output and a zero exit cannot count as this check.

Each scope supplies an `APT_CONFIG` bootstrap that redirects configuration
fragments, main config, sources, trust directories, state, caches and logs into
that new temporary directory. The DPKG status database is read only. This avoids
loading the container's update hooks and prevents fallback to its trusted keys
or existing lists. Public fixtures are readable by APT's normal sandbox user.
The native executable, bootstrap, manifest and key identities are bound before
explicit command approval. No TLS or signature bypass option is provided.

Only this opt-in qualification profile refreshes private indexes. Normal
application search/planning still uses existing caches and never refreshes them
silently. No package mutation runs in this profile. Temporary scopes and the
container were removed after testing. This earlier report covers current-key
acceptance and removed-key rejection during refresh. The extended run below adds
payload-tampering evidence; authentication of previously cached metadata remains open.

## Retained APT signed-payload tampering evidence (2026-11-16)

The [extended authentication report](evidence/apt-ubuntu24.04-x86_64-auth-tamper-2026-11-16.json)
records the same root Ubuntu 24.04 x86_64/APT 2.8.4 scope on the pinned image.
Current-key acceptance and removed-key rejection passed again. A third, initially
empty private cache tested payload integrity with the correct current key.

The fixture takes the freshly authenticated noble `InRelease` bytes and changes
one description byte. Origin, codename and all signature bytes stay unchanged.
A server bound to `127.0.0.1` inside the disposable container serves only
`/dists/noble/InRelease`, directly from memory. It cannot list directories, read
paths, proxy requests or serve packages. Its ephemeral port is not published to
the host or retained in evidence. This local HTTP fault fixture does not change
TLS settings for the official HTTPS source or disable signature verification.

Native APT exited 100 with `BADSIG`, fetched no other fixture path and retained no
usable package index. Application planning against that failed cache was blocked.
`NO_PUBKEY`, network failures, truncated output and exit zero cannot satisfy the
bad-signature check. The server socket and worker thread stopped, temporary
scopes were removed and the owned container was removed after verification.

Six offline regressions cover the exact byte change, preserved signature,
unsupported payload rejection, bounded loopback serving, error classification
and private configuration. No package mutation ran. The report remains
`qualified: false`; authentication of already-cached metadata, native elevation,
DPKG interruption and macOS/Homebrew qualification remain open.

## Retained APT cached-release authentication evidence (2026-11-16)

A native probe reproduced a ready plan with an unchanged review after one cached
`InRelease` description byte was altered. Install/upgrade inspection now verifies
cached releases offline with the explicitly configured binary keyrings and native
`gpgv`. The release, verifier and key content identities join the review
fingerprint. Snapshots before and after review prevent a changed release from
retaining stale approval. Supported layouts and trust limits are documented in
[operation services](operations.md).

The [cached-release report](evidence/apt-ubuntu24.04-x86_64-cached-auth-2026-11-16.json)
records Ubuntu 24.04 x86_64, APT 2.8.4 and gpgv 2.4.4 on the pinned image.
Corrupting an authenticated private cached release blocked both new planning and
execution of a previously reviewed plan before any package command. There was no
install exit code and no refresh during the corrupted-cache check. Restoring the
original signed bytes restored the same native review. Fresh authentication,
removed-key rejection and loopback signed-payload rejection also passed.

The same tested snapshot passed the native
[CMake smoke](evidence/apt-ubuntu24.04-x86_64-cached-smoke-2026-11-16.json),
[key drift](evidence/apt-ubuntu24.04-x86_64-cached-keys-2026-11-16.json),
[origin](evidence/apt-ubuntu24.04-x86_64-cached-origins-2026-11-16.json) and
[source URL](evidence/apt-ubuntu24.04-x86_64-cached-sources-2026-11-16.json) checks.
Invalid key bytes now block a fresh plan as well as stale approval; an identical
key copy at a different declared path still requires a new review. Original
system caches/configuration were not corrupted, and the owned container was
removed after testing.

That snapshot authenticated cached release signatures only. The next report
adds package-index checksum linkage; freshness and independent key
expiry/revocation policy remain open.
The GnuPG verifier assumes the explicitly supplied keys are trusted. No backend
scope is fully qualified and no production mutation gate is enabled.

## Retained APT package-index integrity evidence (2026-11-17)

A native probe against the previous snapshot reproduced a ready plan after one
cached CMake description byte changed. Release signatures remained valid: they
authenticate checksums, so the decoded package indexes also need verification.
Review now requires the exact uncompressed metadata key, SHA-256 and decoded
size from an already authenticated release. Native APT 2.8.4 `apt-helper cat-file`
decodes the input; bounded streaming hashing adds no Python dependency or shell
pipeline. Helper and input identities are checked around execution.

The [index authentication report](evidence/apt-ubuntu24.04-x86_64-index-auth-2026-11-17.json)
records corruption rejection for new plans and stale approval without mutation
dispatch or cache refresh. Restoring the original bytes restored the same review.
The fault changes one description byte in the isolated uncompressed noble/main
amd64 index. Fresh signed-index authentication, removed-key rejection, cached
release corruption and loopback signed-payload tampering also passed.

The same source snapshot passed
[CMake install/remove smoke](evidence/apt-ubuntu24.04-x86_64-index-smoke-2026-11-17.json),
[key drift](evidence/apt-ubuntu24.04-x86_64-index-keys-2026-11-17.json),
[origin](evidence/apt-ubuntu24.04-x86_64-index-origins-2026-11-17.json) and
[source URL](evidence/apt-ubuntu24.04-x86_64-index-sources-2026-11-17.json) checks.
The smoke uses the image's normal compressed LZ4 package indexes. Reports remain
bounded, output-free and explicitly unqualified. Tests use an owned disposable
container without mounts or ports; host packages and personal WSL are untouched.

Freshness, vendor-key provenance, independent expiry/revocation policy, native
elevation, DPKG interruption and macOS/cask qualification remain open. There is
still a race against external changes after the final snapshot. Production
mutations remain disabled and milestone 16 remains unchecked.

## Retained APT signed-time evidence (2026-11-17)

Cached review now requires an authenticated `Date` and enforces `Valid-Until`
when declared. Future timestamps have the ten-second allowance documented in
the [APT configuration manual](https://manpages.ubuntu.com/manpages/noble/man5/apt.conf.5.html).
Checks run after signature verification, after index decoding and during execution
review. Passing time is not included in the review fingerprint, so an unchanged
valid review remains identical while expired metadata still blocks execution.
Duplicate, malformed, folded or inverted time fields fail closed. No package,
source, user preference or catalog entry can disable this fixed review policy.
The initial normal-cache run exposed space-padded single-digit hours in signed
Ubuntu backports/security metadata. The parser and deterministic tests now cover
that native format, including space-padded days, before the final snapshot runs.

The [signed-time authentication report](evidence/apt-ubuntu24.04-x86_64-time-auth-2026-11-17.json)
uses real signed noble/main metadata with an injected reader clock eleven seconds
before its signed creation date. New planning and stale approval were rejected
without refresh or mutation dispatch. Restoring the real clock restored the same
review. Cached release/index corruption, fresh signature authentication, removed
keys and signed-payload tampering also passed in this profile.

The native static archive omits `Valid-Until`; the report records that absence.
Expiry boundaries and expiry during decoding are deterministic unit tests, not
native expiry evidence. Static releases without an expiry remain usable with no
maximum age. Replay policy for such archives, independent clock trust, vendor-key
provenance and key expiry/revocation policy remain open.

The same tested snapshot passed the
[CMake install/verify/remove smoke](evidence/apt-ubuntu24.04-x86_64-time-smoke-2026-11-17.json)
against normal compressed caches. Reports remain explicitly unqualified; no
production mutation gate is enabled. The owned Ubuntu 24.04 x86_64 container has
no mounts or ports and was removed after testing; host packages and personal WSL
are untouched. Native elevation,
DPKG interruption and macOS/cask qualification still prevent completing step 16.

## Retained APT DPKG interruption evidence (2026-11-17)

The opt-in `--apt-dpkg` profile exercises cancellation during real CMake unpack,
using only the reviewed Ubuntu mapping and command-bound consent. A fixed
`Dpkg::Use-Pty=0` option exposes native output. After `Unpacking cmake (` is
observed, the fixture verifies a direct, live DPKG child of its owned APT process,
including process group, session and start identity. A Linux process descriptor
pins that child before a controlled `SIGSTOP`; the application cancellation path
then terminates the owned native scope. No shell hooks or package scripts are
added. Ordinary unit tests mock signals and native operations.

The [DPKG fault report](evidence/apt-ubuntu24.04-x86_64-dpkg-2026-11-17.json)
records cancellation without a success event, no remaining live process-group
members, and native `half-installed` package status on Ubuntu 24.04 x86_64/APT
2.8.4/DPKG 1.22.6. The application rejects partial
inventory. The cancellation and recovery requirement survive SQLite reopening;
recovery inspection reports `unavailable`, explicit acknowledgement is refused,
and another approved execution remains blocked. This verifies existing core
behavior rather than adding an automatic repair operation.

The [DPKG state definitions](https://manpages.ubuntu.com/manpages/noble/man1/dpkg.1.html)
distinguish an incomplete installation from an installed/configured package.
No repair, rollback or post-repair retry is claimed. This is a controlled stopped
unpack fault, not coverage of every unpack/configure/script or graceful
termination case. Native elevation, repair qualification, Homebrew/macOS and
repository/key-policy gates remain open. The report is explicitly unqualified
and production mutations remain disabled.

The manual workflow has an isolated `apt-dpkg` job so its intentionally partial
state cannot affect the other native profiles. To repeat locally, prepare a
**new** marked Ubuntu container with Python/certificates and the tracked source
as below, then run only:

```sh
python3 -m scripts.qualify_native --backend apt --apt-dpkg --approve-native-changes --output /tmp/native-apt-dpkg.json
```

Copy the bounded report before disposing of the container, including on failure.
This run used no host mounts or published ports, and the owned container was
removed after retaining evidence. Host packages and personal WSL were untouched.
Hosted workflow execution remains untested here.

## Retained APT post-repair recovery evidence (2026-11-18)

The [post-repair report](evidence/apt-ubuntu24.04-x86_64-dpkg-repair-2026-11-18.json)
extends the controlled stopped-unpack case. The separate `--apt-dpkg-repair`
profile first proves partial inventory and blocked recovery exactly as before.
Its explicit approval additionally covers `dpkg --configure --pending` and a
reinstallation of the original reviewed CMake version. Both commands are bound
to native executables and require command-specific elevation approval; no shell,
catalog hooks, package-manager bootstrap or force flags are added. Configuring
pending packages can run native dependency configuration and package hooks.
This is a fixed disposable-fixture procedure, not a desktop repair facility.

The tested sequence restores the reviewed version in inventory while retry
remains blocked by the original durable record. Fresh recovery inspection reports
`changed`: DPKG inventory does not prove historical repository provenance.
Acknowledgement is still required and preserves the original cancelled outcome.
After acknowledgement, fresh reviewed removal, actual installation and removal
all verify successfully. The fresh installation must succeed rather than skip.
Native repair itself never automatically acknowledges the recovery record.

The profile uses a separate disposable Ubuntu 24.04 x86_64 container. To repeat
after the same isolated preparation, source copy and guard setup:

```sh
python3 -m scripts.qualify_native --backend apt --apt-dpkg-repair --approve-native-changes --output /tmp/native-apt-repair.json
```

The original `--apt-dpkg` profile still performs no repair. The manual job matrix
gives each mode a fresh container; hosted execution has not been dispatched here.
The [DPKG configuration manual](https://manpages.ubuntu.com/manpages/noble/man1/dpkg.1.html)
describes pending-package configuration. This does not establish general repair,
rollback, graceful/configure/script interruption, elevation or macOS qualification.
Repository/key-policy gaps remain open and production mutations remain disabled.
The local run used APT 2.8.4/DPKG 1.22.6, no host mounts or published ports, and
the owned container was removed after retaining its report. Host packages and
personal WSL were untouched.

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
    docker exec --env DEV_SETUP_SOURCE_COMMIT=$qualificationCommit --workdir /work $qualificationContainer python3 -m scripts.qualify_native --backend apt --apt-keys --approve-native-changes --output /tmp/native-apt-keys.json
    if ($LASTEXITCODE -ne 0) { throw 'APT key checks failed; inspect their bounded report.' }
    docker exec --env DEV_SETUP_SOURCE_COMMIT=$qualificationCommit --workdir /work $qualificationContainer python3 -m scripts.qualify_native --backend apt --apt-auth --approve-native-changes --output /tmp/native-apt-auth.json
    if ($LASTEXITCODE -ne 0) { throw 'APT authentication checks failed; inspect their bounded report.' }
} finally {
    try {
        docker cp "${qualificationContainer}:/tmp/native-apt.json" build/native-apt.json
        docker cp "${qualificationContainer}:/tmp/native-apt-upgrade.json" build/native-apt-upgrade.json
        docker cp "${qualificationContainer}:/tmp/native-apt-failures.json" build/native-apt-failures.json
        docker cp "${qualificationContainer}:/tmp/native-apt-origins.json" build/native-apt-origins.json
        docker cp "${qualificationContainer}:/tmp/native-apt-sources.json" build/native-apt-sources.json
        docker cp "${qualificationContainer}:/tmp/native-apt-keys.json" build/native-apt-keys.json
        docker cp "${qualificationContainer}:/tmp/native-apt-auth.json" build/native-apt-auth.json
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
   A controlled DPKG unpack fault now checks owned-scope termination, partial
   inventory refusal and durable blocked recovery. A separate controlled native
   repair now verifies fresh inspection, acknowledgement and actual install/remove
   afterward. Broader repair, configure/script interruption and graceful
   cancellation remain open.
4. Qualify Homebrew casks separately; formula success does not qualify casks.
5. Inspect source/version identity, altered-source/candidate rejection and
   verification failure on those hosts. APT now has controlled exit-zero
   verification-failure and private cached-origin rejection evidence. Repository
   URL and signing-key drift against an existing cache also passed. Fresh signed
   indexes were accepted with the current key and rejected with removed keys.
   One-byte signed-payload tampering was rejected with `BADSIG` before package
   index retrieval. Cached release signatures now reject altered bytes before
   planning/dispatch. Decoded package indexes now match authenticated checksums
   and sizes; corrupted indexes block fresh/stale plans. Signed creation/expiry
   bounds are checked again before dispatch. Replay policy without expiry, key policy and
   Homebrew cases remain open.
   Resolve any real parser/output limits.
6. Review evidence per backend, source, OS/release, architecture, native version
   and action before adding any production enablement. Update the support matrix
   and leave unsupported scopes disabled.

The [GitHub runner image list](https://github.com/actions/runner-images#available-images)
documents the selected OS/architecture labels. Native command behavior is
documented in the [Homebrew manual](https://docs.brew.sh/Manpage) and
[Ubuntu noble APT manual](https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html).
The reviewed [APT source format](https://manpages.ubuntu.com/manpages/noble/man5/sources.list.5.html)
defines explicit keyring paths and the two source-manifest formats.
The [APT authentication manual](https://manpages.ubuntu.com/manpages/noble/man8/apt-secure.8.html)
describes release authentication; [APT configuration order](https://manpages.ubuntu.com/manpages/noble/man5/apt.conf.5.html)
documents the early `APT_CONFIG` bootstrap used for private refreshes.
These references describe environments and commands; they are not project test
evidence. No dependency or release-publishing change is introduced.

## Pre-pivot local validation (2026-11-18; historical)

`.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q`
ran **619 tests OK, one POSIX-only skip on Windows**. Qualification unit tests
use fake managers; the separate real runs are recorded above. Production GUI tests confirm
disabled mutation controls, explanatory reasons and enabled read-only verify.
Ruff lint/format passed (189 files), mypy passed (112 source files), and catalog
validation passed (14 packages/39 mappings).

The same source snapshot ran **198 tests OK** on Ubuntu/Python 3.12.3:

```sh
TMPDIR=/tmp/dev-setup-core-tests python3 -W error::ResourceWarning -m unittest tests.unit.test_services tests.unit.test_inspection tests.unit.test_apt_trust tests.unit.test_apt_cache tests.unit.test_apt_indexes tests.unit.test_apt_freshness tests.unit.test_lifecycle tests.unit.test_native_qualification tests.unit.test_native_support tests.unit.test_apt_failure_checks tests.unit.test_apt_dpkg_checks tests.unit.test_apt_repair_checks tests.unit.test_apt_origin_checks tests.unit.test_apt_auth_checks tests.unit.test_apt_tamper_checks tests.integration.test_command_runner tests.integration.test_native_coordination tests.integration.test_state_recovery tests.integration.test_core_import -q
```

These core tests use synthetic mutations; they complement the nine real APT profiles.
The dedicated temporary directory isolates their native coordination locks.
Qt and the full Linux dependency matrix were not exercised by this subset.
