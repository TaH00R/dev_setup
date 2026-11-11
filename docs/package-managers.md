# Package-manager adapters and safe execution

Step 4 adds root-level `package_managers/`, `system/commands.py`, and
`system/runner.py`. These modules do not import Qt, the legacy installers,
or third-party packages. No dependency was added. Importing them does not
probe the host, start processes, inspect package inventories, or use the network.

Step 5 extends the same contract with pacman, DNF, zypper, apk and Flatpak.
See the current [support matrix](support-matrix.md) for capabilities, source
scope, deferred backends, and native-operation limitations. The four-adapter
table below records the initial step 4 implementation.

## Adapter contract

`PackageManager` exposes `availability()`, `capabilities`, `version()`,
`search(query)`, `install(spec)`, `uninstall(spec)`, `upgrade(spec)`,
`list_installed()`, `show(spec)`, and `build(action, ...)`. These methods build
immutable `Command` records. Availability only locates required executables on
compatible platforms; version checking is a separate explicit request.

`execute(action, runner, ...)` rebuilds a validated request before invoking the
runner. It accepts cancellation and output observers. Package actions require
an exact `InstallSpec`, explicit reviewed source identity, matching OS/distro,
validated native identifier, and a supported version/channel selection.
Unknown capabilities fail with `AdapterError.error` rather than guessing.
Source/bucket installation, global upgrades, URL/path manifests, wildcard
installs, caller-provided options, and shell strings are not adapter inputs.

`adapters_for(platform)` selects compatible adapters without executing them.
Only the Debian-family APT adapter is currently selected for Linux. Package
selection through APT is initially restricted to reviewed Ubuntu 24.04 mappings;
other native adapters and source mappings belong to step 5.

| Adapter | Initial scope | Version selection | Native source choice |
| --- | --- | --- | --- |
| WinGet | Windows; version/search/install/uninstall/upgrade/list/show | Install, upgrade, show; no channels | Exact ID plus `--source winget`; Store and manifest inputs excluded |
| Scoop | Windows; same action family through the reviewed bridge | Explicit version/channel selection unsupported | Reviewed main/extras bucket; exact token, configured remote and installed origin checked |
| Homebrew | macOS; same action family | Explicit version/channel selection unsupported; versioned formula tokens remain exact IDs | Core formula versus cask is explicit; external taps and URLs excluded |
| APT | Debian-family cache/inventory; Ubuntu 24.04 package selection | Install, upgrade, show; no channels | Selected noble release and configured APT repositories; no source modifications |

Search/list/show results are bounded native output at this stage, not parsed
domain inventory or hybrid discovery results. A non-zero backend status is
preserved with a structured command-failure error and sanitized output; it is
not guessed to be a network/permission error from localized message text.
The adapters do not claim install verification or dependency resolution.

## Preview, approval, and privilege boundaries

`Command` holds a fixed argument tuple, target OS, source ID, mutation flag,
privilege requirement, timeout, and process-local environment overrides.
`preview()` returns redacted display arguments. Actual command/environment
values are excluded from the command's default representation.

An application must obtain actual user confirmation, then create `Approval`
for that exact immutable request. The runner refuses mutations without this
record, refuses mismatched commands, and requires `elevation=True` when native
elevation is possible/required. Changing arguments, source, privilege, timeout,
or environment invalidates the approval match. This record is a consent
boundary for trusted application code, not authentication of arbitrary Python
callers or an implemented GUI dialog. A future service must never manufacture
approvals without user confirmation.

WinGet retains native elevation behavior. Source/package agreements are not
silently accepted; an unconfigured agreement can cause a native refusal until
the user handles it through the manager. Homebrew never runs with sudo and
disables automatic updates, install cleanup, and analytics for these requests.
Cask privilege requirements remain explicit in the selected mapping.

APT changes require root or native `pkexec --disable-internal-agent` elevation.
Without root/Polkit the request is rejected. No passwords are read, stored, or
sent through stdin. Only the selected package is requested, with native
dependency handling; upgrade uses `install --only-upgrade`, removal does not
purge, and no automatic repository refresh or system-wide upgrade is added.
The noble release option is not cryptographic origin pinning. The future
planner must inspect current candidate origins, dependencies and simulations
before presenting an install plan, especially on systems with extra repos.

## Reviewed Scoop exception

The user explicitly authorized this narrow exception to the original no-shell
adapter wording. `scoop.py` locates the existing native PowerShell executable
and installed `scoop.ps1`; it runs the bundled `scoop_bridge.ps1` using `-File`,
`-NoProfile`, and `-NonInteractive`. Both script paths are visible in the
preview. The adapter never supplies `-Command`, changes execution policy, pipes
downloaded code into a shell, installs Scoop, or adds a bucket.

The bridge validates actions and tokens again before dispatch. It uses the
installed manager's read-only JSON export to verify the configured bucket's
reviewed HTTPS remote. Mutations also reject ambiguous/global installations
and installed packages from a different bucket. The manager itself cannot be
selected as a package. Install/show use an exact qualified token; upgrade and
uninstall retain that selection while checking installed origin first.
Native exit status is preserved and redirected metadata is UTF-8.

Native Scoop can refresh itself/configured buckets during an app upgrade;
this is included in the upgrade preview description. Native manifests can
also have dependencies and PATH/shortcut/service effects. These must be
included in the later plan and consent review. Normal Scoop source and script
trust still applies: a malicious installed manager is not sandboxed by this
bridge. System policy can refuse `-File`; the application reports failure
instead of bypassing the policy.

Script discovery searches explicit PATH directories for `scoop.ps1` even when
Windows PATHEXT omits `.ps1`; it does not fall back to a batch shim or the
current directory. Mutation requests retain the selected mapping's privilege
requirement in their exact approval record.

## Runner behavior and limits

`CommandRunner.run()` is deliberately blocking and must be called by a worker,
never by a Qt widget's GUI-thread slot. It uses `Popen` argument arrays with
`shell=False` and stdin disconnected. Raw `.cmd`, `.bat`, and `.ps1` launchers
are refused; the authorized Scoop bridge has an explicit native host. The
runner rejects execution on a different target OS.

Both output pipes drain concurrently in 4 KiB chunks through a bounded queue.
Each stream retains at most 65,536 bytes by default; capture continues draining
after truncation so verbose tools cannot deadlock on full pipes. Results expose
timestamps, status, bounded text, truncation, and structured failures. Optional
observers receive replacement snapshots per stream, not append-only chunks.
Observer errors stop monitoring with a structured failure and process cleanup.

Explicit secrets, custom environment override values, known credential fields,
bearer tokens and URL credentials are redacted. Explicit secrets split across
reads are masked even in incomplete snapshots. Public process-control flags
and locale constants remain usable; no environment dictionary is logged.
Redaction is not a general data-loss-prevention system: callers must supply
known secrets and must not persist arbitrary output as audit/history records.
The runner writes no log files and hides output from default result/event repr.

Cancellation and timeouts request termination, then bounded forced shutdown
where permitted. POSIX requests use a process session/group; Windows terminates
the direct child. Detached native installers or privileged descendants may
continue, and access restrictions may prevent termination. Results describe
partial changes; no rollback or universal process-tree cancellation is claimed.
Process creation itself may not be interruptible immediately on every OS.

## Validation and follow-up

Adapter command tests use injected executable locations and a mocked runner;
they do not run package operations. Runner tests execute only disposable Python
children. Bridge tests use a temporary fake `scoop.ps1`, never installed Scoop.
On systems without PowerShell, those bridge tests skip; native policy refusal
also skips rather than relaxing execution policy. Windows CI exercises the
bridge where available. Real manager versions, native installers, elevation,
and clean-machine paths still need disposable integration environments.

Step 5 adds staged backend/catalog coverage. Step 6 adds parsed APT/Homebrew
inventory, source/candidate checks, dependency/conflict planning, exact-plan
consent, native verification and operation-state integration. Additional backend
inspection remains gated explicitly; command support alone does not enable
application orchestration. See [operation services](operations.md). The desktop
worker/consent UI is implemented in step 7; see the [desktop guide](desktop.md).
The legacy CLI retains its installer paths.

Command syntax was reviewed against primary sources:

- [WinGet install](https://learn.microsoft.com/en-us/windows/package-manager/winget/install), [search](https://learn.microsoft.com/en-us/windows/package-manager/winget/search), and [uninstall](https://learn.microsoft.com/en-us/windows/package-manager/winget/uninstall).
- [Homebrew manual](https://docs.brew.sh/Manpage).
- [APT commands](https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html), [cache queries](https://manpages.ubuntu.com/manpages/noble/man8/apt-cache.8.html), and [dpkg-query](https://manpages.ubuntu.com/manpages/noble/man1/dpkg-query.1.html).
- [Polkit pkexec reference](https://polkit.pages.freedesktop.org/polkit/pkexec.1.html).
- [Scoop command reference](https://github.com/ScoopInstaller/Scoop/wiki/Commands), [export implementation](https://github.com/ScoopInstaller/Scoop/blob/master/libexec/scoop-export.ps1), [installed-app selection](https://github.com/ScoopInstaller/Scoop/blob/master/lib/core.ps1), and [native update behavior](https://github.com/ScoopInstaller/Scoop/blob/master/libexec/scoop-update.ps1).

Local validation on Windows/CPython 3.14.7, 2026-11-11:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -q` | 168 tests passed; all six fake-Scoop bridge tests executed and passed. |
| `.\.venv\Scripts\python.exe -m catalog validate` | 10 packages and 26 reviewed mappings valid. |
| `.\.venv\Scripts\python.exe -m ruff check .` | Passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 40 files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 25 source files. |
| `git diff --check` | Passed. |

No real package installation, removal, upgrade, package-manager bootstrap,
elevation prompt, or registry search was executed. Remote cross-platform CI,
native command compatibility on disposable machines, and detached-installer
cancellation remain integration work. This step is one commit per the updated
user instruction; the reviewed Scoop bridge authorization is also recorded in
the local AGENTS.md and progress tracker.
