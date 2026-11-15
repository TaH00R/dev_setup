# Native qualification: milestone 16 remains open

Application support reporting and the production qualification gate are
implemented. Native APT/Homebrew qualification is **not complete**. No native
mutation has been run for this milestone on the developer's Windows workstation.
Docker's Linux engine was unavailable during the local check, and no macOS
qualification host was connected. Existing personal WSL distributions were not
used for package installation tests.

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

- APT: a disposable `ubuntu:24.04` job container on an `ubuntu-24.04` hosted runner.
  The visible image-preparation step refreshes repositories and installs Python,
  certificates and Git. The application smoke itself never refreshes repositories
  or bootstraps a package manager. It selects the existing reviewed CMake mapping.
- Homebrew: a `macos-15` hosted ARM64 runner with its existing Homebrew. It selects
  the reviewed ripgrep core formula; no manager/tap bootstrap is provided. Hosted
  images contain preinstalled software, so this is not clean-machine evidence.
- Both: `python -m scripts.qualify_native --backend <apt|homebrew>
  --approve-native-changes --output <new-report.json>`. The guard requires
  `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted`. APT additionally
  requires a root Ubuntu 24.04 container; Homebrew requires a non-root macOS ARM64
  host. These are explicit test-environment checks, not authentication against a
  hostile caller forging environment facts. Everyday/self-hosted runs are refused.

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

Even an exit-zero smoke report says `qualified: false` and
`smoke_passed_qualification_pending`. Latest-to-latest upgrade review is not a real
upgrade and is recorded as untested. The current runner does not qualify native
elevation prompts, mid-command cancellation or casks. A failure can leave package
changes on the disposable host; no native rollback is claimed.

## Remaining acceptance gates

1. Run and inspect the hosted smoke jobs. Neither workflow has been dispatched
   from this workspace. Retain the source commit and actual native versions.
2. Reproduce install/update/remove on clean disposable Ubuntu 24.04 and macOS
   ARM64 images, with controlled older/newer versions from reviewed sources.
   A current-version no-op must never count as upgrade evidence.
3. Qualify native elevation/consent and cancellation during real native work,
   including post-cancellation inventory, lifecycle recovery and retry rules.
4. Qualify Homebrew casks separately; formula success does not qualify casks.
5. Inspect source/version identity, altered-source/candidate rejection and
   verification failure on those hosts. Resolve any real parser/output limits.
6. Review evidence per backend, source, OS/release, architecture, native version
   and action before adding any production enablement. Update the support matrix
   and leave unsupported scopes disabled.

The [GitHub runner image list](https://github.com/actions/runner-images#available-images)
documents the selected OS/architecture labels. Native command behavior is
documented in the [Homebrew manual](https://docs.brew.sh/Manpage) and
[Ubuntu noble APT manual](https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html).
These references describe environments and commands; they are not project test
evidence. No dependency or release-publishing change is introduced.

## Local validation (2026-11-15)

`.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q`
ran **535 tests OK, one POSIX-only skip on Windows**. Qualification runner tests
use fake managers; they are not native evidence. Production GUI tests confirm
disabled mutation controls, explanatory reasons and enabled read-only verify.
Ruff lint/format passed (166 files), mypy passed (99 source files), and catalog
validation passed (14 packages/39 mappings). The hosted workflow was reviewed
locally but has not been dispatched or validated by a native run.
