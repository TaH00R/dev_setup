# Reviewed support matrix

This matrix describes implemented command contracts and reviewed catalog scope.
Native installation/elevation has not been tested on disposable Linux/macOS
machines. Metadata matches alone do not establish backend availability or a
complete installation plan. All mutations retain command-bound approval.

## Backend capabilities

| Backend | Host selection | Version / search / inventory / show | Install / remove | Individual upgrade | Reviewed package sources |
| --- | --- | --- | --- | --- | --- |
| WinGet | Windows | Implemented | Implemented | Implemented | WinGet community source |
| Scoop | Windows; authorized fixed bridge | Implemented | Implemented | Implemented with native refresh caveat | Main/extras remote and installed-origin checks; no bundled Scoop entries yet |
| Homebrew | macOS | Implemented | Implemented | Implemented | Core formulas and casks |
| APT | Debian family | Implemented | Implemented | Implemented | Bundled Ubuntu 24.04 mappings |
| pacman | Arch family | Implemented | Implemented | Unsupported | Extra on Arch; no derivative-distro substitution |
| DNF | Fedora/RHEL family | Implemented | Implemented | Implemented | Fedora 43, configured fedora/updates repositories |
| zypper | SUSE family | Implemented | Implemented | Implemented | Tumbleweed OSS via the existing repo-oss alias |
| apk | Alpine family | Implemented | Implemented | Unsupported | Alpine 3.22 main, reviewed patch levels 3.22.0 through 3.22.8 |
| Flatpak | Known Linux families, x86_64/ARM64 | Implemented | Implemented | Implemented | Existing per-user Flathub; stable full application refs |
| Chocolatey | Windows | Deferred | Deferred | Deferred | No adapter or reviewed mappings shipped |
| MacPorts | macOS | Deferred | Deferred | Deferred | No adapter or reviewed mappings shipped |
| Snap | Linux | Deferred | Deferred | Deferred | No adapter or reviewed confinement/license flow shipped |

The schema recognizes later backend names, but that does not advertise an
implemented adapter. Later adapters must use the same `PackageManager`
capability/build/approval contract and exact-command tests; no empty adapter
classes or invented command support are shipped.

Native version selection remains unsupported for the five new adapters.
Queries return bounded native output. Step 6 adds APT/Homebrew native inspection,
dependency plans and verified operation services; the other adapters' application
orchestration remains explicitly unsupported until reviewed parsers are added.
See [operation services](operations.md) for the distinction between command
capabilities and enabled workflows. Live discovery belongs to step 8.
Compatible family detection can expose read-only
commands on derivatives, but package mutations require the exact reviewed
distribution, release and source. Manager availability is detected separately.

## Source and operation limits

- pacman reads existing sync databases and installs exact `extra/<name>` targets.
  It never adds `-Sy` or performs `-Syu`. The host must already be coherently
  updated. Individual upgrade is refused rather than implementing a partial or
  silent full-system upgrade.
- DNF uses cache-only reads. Mutations select fedora/updates and disable native
  orphan dependency cleanup on removal. No full-system upgrade, repository
  configuration, plugin installation, or signature-check bypass is requested.
- zypper uses non-interactive, no-refresh requests and an existing repo-oss alias.
  Normal native trust checks stay enabled. Selected-package updates do not
  replace Tumbleweed's distribution-update workflow; native dependency effects
  must be reviewed by the later planner.
- apk reads cached metadata and limits selected package operations to the
  reviewed HTTPS main repository using process-local repository options. It
  does not edit `/etc/apk/repositories`. Its reviewed upgrade command is not
  scoped to a named package, so the adapter refuses upgrade.
- System mutations require root or a native Polkit agent; missing elevation is
  an explicit error. The new adapters reject URLs, paths, local RPM/APK archive
  filenames, wildcards, groups and option/version expressions as package IDs.
- Flatpak uses per-user scope and an exact app/ID/architecture/stable reference.
  Before selected package operations, `execute()` checks the configured Flathub
  URL; before removal/update it checks installed origin. Truncated checks fail
  closed. Remote addition, system scope, permission overrides, data deletion
  and explicit commit/version selection are not exposed. Native runtime and
  extension changes/cleanup are called out in the mutation preview and must
  be included in the later dependency/consent review. Search may refresh native
  appstream metadata, but cannot bootstrap a manager or add a remote.

Repository aliases are not cryptographic origin pinning. The future planner
must inspect current candidate origins, installed provenance and transaction
effects before approving execution, including systems with altered native repo
configuration. Use adapter `execute()` for source preflights, not the low-level
runner directly. Process cancellation remains best effort as documented in
[the runner guide](package-managers.md).

## Catalog coverage

The catalog now contains **14 packages and 39 reviewed mappings**. Existing
Windows x86_64 and macOS ARM64 scopes remain deliberately narrow. Git gains
Arch Extra, Fedora 43, openSUSE Tumbleweed git-core, and Alpine 3.22 mappings;
rolling Arch/Tumbleweed mappings use exact distro IDs without invented release
numbers. VS Code gains a community Flathub mapping for Ubuntu 24.04 x86_64.
It is separate from the Microsoft APT repository and requests broad host,
network, device and SSH-agent permissions in the reviewed manifest.

All ten roadmap groups now have starter coverage:

| Group | Reviewed examples |
| --- | --- |
| Languages/runtimes | Python, Node.js, OpenJDK 21 |
| Compilers/build/debugging | GCC C/C++, CMake |
| Editors | Visual Studio Code |
| Databases | PostgreSQL |
| Containers | Docker Desktop and Docker Engine as separate packages |
| Cloud/DevOps | GitHub CLI |
| Git/API/networking | Git |
| Python/JavaScript/data tooling | uv |
| Mobile | Android SDK Platform-Tools (ADB/Fastboot) |
| Terminal/productivity | ripgrep |

These are reviewed representatives rather than the entire aspirational package
list. uv tooling is distinct from Python runtime provisioning. Android platform
tools do not include Android Studio, a complete SDK, drivers or an emulator;
their vendor terms remain explicit. The narrowly reviewed `adb version`
detection subcommand is allowed, while device/shell/flash commands remain
outside the detection schema. GitHub authentication is a separate user action.

CUDA/driver compatibility, full mobile SDK component selection, commercial IDE
license flows, additional servers/services and the remaining catalog candidates
are intentionally deferred until mappings and their system changes are reviewed.
No package substitution is performed for unsupported platforms.

## Review sources

Each added mapping records its evidence URL and review date in the JSON entry.
Native command semantics were checked against primary references:

- [pacman manual](https://man.archlinux.org/man/pacman.8.en).
- [DNF reference](https://dnf.readthedocs.io/en/latest/command_ref.html) and [DNF5 reference](https://dnf5.readthedocs.io/en/latest/dnf5.8.html).
- [zypper manual](https://manpages.opensuse.org/Tumbleweed/zypper/zypper.8.en.html).
- [Alpine package documentation](https://docs.alpinelinux.org/user-handbook/0.1a/Working/apk.html) and [apk upgrade contract](https://git-old.alpinelinux.org/apk-tools/tree/doc/apk-upgrade.8.scd?id=0867d629b96d3f5e312d9670f51581e7225e3aad).
- [Flatpak command reference](https://docs.flatpak.org/en/latest/flatpak-command-reference.html) and [community VS Code manifest](https://github.com/flathub/com.visualstudio.code/blob/master/com.visualstudio.code.yaml).
- [Released Alpine 3.22 images](https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/x86_64/) establish the exact reviewed patch-level list.

## Step 5 validation

Local Windows/CPython 3.14.7 checks on 2026-11-11:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -q` | 177 passed, including exact new backend commands, source/origin refusals, capabilities and category coverage. |
| `.\.venv\Scripts\python.exe -m catalog validate` | 14 packages, 39 reviewed mappings valid. |
| `.\.venv\Scripts\python.exe -m ruff check .` | Passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 43 files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 27 source files. |
| `git diff --check` | Passed. |

Native package operations are mocked; package registry/documentation reads were
performed only for manual source review. No software was installed, removed or
upgraded. Remote OS CI, actual native-version compatibility, elevation, repository
configuration and clean-machine integration remain unverified locally. No new
dependency was introduced. These are the historical step 5 results; step 6
inventory/planning/verification scope is documented in [operation services](operations.md).
