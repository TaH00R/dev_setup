# Installed software inventory

Installed reads the device rather than only the application's operation history.
Press F5 or **Refresh installed tools** to scan again. Work runs in a cancellable
background worker. A missing package manager or failed reader does not hide
results from working sources.

| Platform | Inventory sources |
| --- | --- |
| Windows | WinGet JSON export, Scoop JSON export, registered applications in HKLM/HKCU and 32/64-bit views |
| macOS | Homebrew installed formulae and casks |
| Debian/Ubuntu family | APT/DPKG installed packages |
| Arch family | pacman local package database |
| Fedora/RHEL family | local RPM database through the DNF reader |
| openSUSE family | local RPM database through the zypper reader |
| Alpine | APK local installed-package list, with network access disabled |
| Linux with Flatpak | user/system applications, retaining full ref, origin and installation |
| Every supported OS | independently detected catalog executables on PATH, with version-command observations |

Read-only inventory sources are separate from operation inspectors. Adding a
reader does not enable unqualified package installation, upgrades or removal.
Existing native qualification and consent requirements remain in effect.

## Interpreting results

- **Package-manager records** identify installed native packages. Unknown source
  or version stays unknown; a package name alone does not prove repository trust.
- **windows-registry** records are registered applications, including applications
  not matched by WinGet. The row retains its registry scope/key. Shared user views
  are deduplicated. Uninstall commands stored in the registry are never executed.
- **executable / PATH observation** rows report catalog tools found on PATH.
  Version output is an observation, not package ownership or service health.
  Java-style version output on stderr is retained. Windows batch/script shims are
  subject to the existing runner restrictions; presence and version errors remain
  visible. These rows cannot authorize package-manager mutations.

The same application can have separate native, registry and executable records
when reliable identity linking is unavailable. The app does not merge unrelated
packages by display name. RPM records retain architecture, while Flatpak records
retain installation/branch/architecture, preventing ambiguous identity collapse.

WinGet export only covers applications it can match to its selected source and
can fail when a source needs agreement, is unavailable or times out. The app does
not silently accept agreements. Windows registration and PATH observations still
work. The Installed page shows each source error and available results.

This is not an exhaustive scan of portable applications outside PATH, Microsoft
Store packages, arbitrary macOS application bundles, language package managers or
all runtime environments. Chocolatey, MacPorts and Snap remain outside the
implemented backend matrix. Missing native readers do not imply no software is
installed on the device.

## Bounds, cancellation and failures

Native inventory uses an explicit 8 MiB capture bound; ordinary commands retain
their 64 KiB default and UI output snapshots remain capped at 64 KiB. A failed,
cancelled, malformed or truncated read cannot represent an empty installation
snapshot. WinGet writes JSON only into an owned temporary directory and its file
size is checked before parsing. Commands are argument arrays with `shell=False`;
inventory does not install managers, elevate, refresh RPM repositories or accept
arbitrary catalog commands. The Scoop path uses the authorized fixed `-File`
bridge, which dispatches only its new allowlisted inventory/export action.

## Validation

Deterministic tests cover all reader command arrays, versions/scopes, malformed
data, duplicate JSON keys, cancellation, missing managers, source failures,
registry views, independent detection and Installed rendering. Cross-platform CI
runs these tests. Separate opt-in jobs read native Windows/macOS/Ubuntu inventory
and stock Arch, Fedora, openSUSE and Alpine containers without installing packages.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.unit.test_inventory_readers -v
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q
```

On Windows, an explicit read-only local check is:

```powershell
$env:DEV_SETUP_NATIVE_INVENTORY='1'
.\.venv\Scripts\python.exe -m unittest tests.integration.test_inventory_native.NativeInventoryTests.test_host_read_only_inventory -v
```

Local Windows validation read 168 registered applications and detected 10 catalog
tools on PATH. WinGet timed out and Scoop was absent; the resulting 178 rows were
still returned. Disposable native container checks also read 92 APT, 137 pacman,
142 DNF/RPM and 119 zypper/RPM package records. An Alpine-based image returned
23 APK records, including a synthetic package whose version has no revision suffix. Native macOS,
Scoop and Flatpak coverage still needs provisioned hosts; mocked coverage is not
native qualification. No package was installed, upgraded or removed during these
checks. A later WinGet read succeeded with 52 records, and the rebuilt standalone
Windows TUI displayed 225 installed/observed rows in an interactive terminal.
The extracted archive also passed all four no-Python-on-PATH checks.
The [portable evidence record](evidence/inventory-2026-11-18.json) binds the current
archive hash and native image digests. The final full suite ran 576 tests with
three skips, including 19 terminal tests; Ruff and mypy passed.

## Native command references

- [WinGet export and JSON format](https://learn.microsoft.com/windows/package-manager/winget/export)
- [Scoop export implementation](https://github.com/ScoopInstaller/Scoop/blob/master/libexec/scoop-export.ps1)
- [pacman query operations](https://man.archlinux.org/man/pacman.8.en)
- [RPM query formats](https://rpm.org/docs/latest/man/rpm-queryformat.7)
- [APK info](https://github.com/alpinelinux/apk-tools/blob/2.14-stable/doc/apk-info.8.scd)
- [Flatpak list columns](https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-list)
