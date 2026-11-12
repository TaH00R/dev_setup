# Desktop builds and release preparation

The source version is currently `0.1.0.dev0`, an unsigned preview. The repository
can build native desktop bundles; it does not yet have an approved production
update origin, pinned signing key or published stable release. macOS/Linux native
execution, production signing/notarization and clean-machine installation/upgrade
validation require external release runs. CI configuration is not evidence that
those runs have passed.

The local Windows preview build on 2026-11-12 passed
`python scripts/build_desktop.py --smoke-test --archive`, including both frozen
checks. The resulting `DevSetup-0.1.0.dev0-windows-x86_64.zip` was 58,731,157 bytes;
its ZIP CRCs, metadata size/SHA-256 and bundled entry point/catalog/Scoop bridge
were checked. It is unsigned. This was a developer-machine preview check, not a
clean-machine installation, signed release or macOS/Linux validation.

Final local checks used `.\.venv\Scripts\python.exe` on Windows/CPython 3.14.7:

```text
python -W error::ResourceWarning -m unittest discover -s tests -t . -q
python -m app catalog validate
python -m ruff check .
python -m ruff format --check .
python -m mypy
git diff --check
```

The suite passed 404 tests, including 52 GUI tests. Catalog validation passed for
14 packages and 39 reviewed mappings; Ruff passed with 117 formatted files and
mypy passed for 66 source files. Local documentation links and diff checks passed.

## Build a preview

Use Python 3.10 or newer in an isolated environment on the target OS:

```text
python -m pip install -r requirements-gui.txt -r requirements-security.txt "pyinstaller>=6.22,<7"
python scripts/build_desktop.py --smoke-test --archive
```

PyInstaller builds the Qt desktop from `scripts/desktop_entry.py`. The bundle
includes the curated JSON catalog and reviewed Scoop bridge; starter presets are
typed application data. Legacy installers are excluded. Build output, work files
and the PyInstaller cache stay under the repository's `dist/` and `build/`
directories. The builder checks resolved destinations and refuses symlink or
junction paths before allowing PyInstaller cleanup. Archive/sidecar leaves also
refuse symlinks, reparse points and multiple hard links. Archives are prepared in a
private temporary directory and published by replacing directory entries.

| Platform | Application | Portable archive |
| --- | --- | --- |
| Windows | `dist/DevSetup/DevSetup.exe` with its adjacent files | ZIP |
| macOS | `dist/DevSetup.app` | ZIP made with `ditto` |
| Linux | `dist/DevSetup/DevSetup` with its adjacent files | tar.gz |

Artifacts contain the embedded version, OS and architecture in their filename.
The adjacent `.json` file records their SHA-256, size and native signing status.
Unsigned preview metadata always says `signed: false`. Keep the entire Windows
or Linux application directory together; moving just the executable breaks Qt
and the bundled catalog. Extract archives before starting the application.

`--self-check` validates the bundled catalog and all ten starter presets without
Qt, native commands or state writes. `--smoke-test` opens and closes the desktop
with automatic manager refresh disabled and ephemeral services. The build helper
uses Qt's offscreen platform and a 60-second process limit. Failures return a
nonzero status; unattended checks do not open a frozen Python exception dialog.
Neither check installs or upgrades any developer tool. These smoke checks do not
prove clean-machine compatibility, native consent/elevation or package operation
success.

`.github/workflows/build.yml` runs the preview build and bounded checks on
Windows, macOS and Ubuntu 22.04. It uploads CI artifacts and does not publish a
release. Linux Qt requires EGL/OpenGL, xkbcommon and supporting XCB libraries;
the workflow installs those only on its disposable runner. Build Linux releases
on the oldest supported glibc baseline and test newer supported distributions;
PyInstaller does not make a glibc build portable to Alpine/musl.

## Signed release hosts

Choose a stable version by changing `app.__version__` in a reviewed commit and
creating its matching `vVERSION` tag. Signed builds refuse `.dev` versions and
version/tag mismatches. No environment variable can replace the embedded version.

```text
python scripts/build_desktop.py --signed --release-version VERSION --smoke-test --archive
```

The command fails before building when required signing configuration is absent.
Provision signing credentials outside the repository on dedicated release hosts:

| Platform | Required configuration |
| --- | --- |
| Windows | Windows SDK `signtool` on PATH; signing certificate/private key in the current user's certificate store; `DEV_SETUP_WINDOWS_CERT_SHA1` exact certificate thumbprint; `DEV_SETUP_TIMESTAMP_URL` reviewed HTTPS timestamp authority |
| macOS | Developer ID Application identity in an unlocked signing keychain; `DEV_SETUP_MAC_SIGN_IDENTITY`; pre-provisioned `notarytool` keychain profile named by `DEV_SETUP_MAC_NOTARY_PROFILE`; Apple command-line tools |
| Linux | Native bundle validation; authenticity is supplied by the detached signed release manifest |

Windows signs the application executable with SHA-256, obtains a SHA-256 timestamp
and runs Authenticode verification. macOS delegates nested binary/bundle signing
and hardened runtime to PyInstaller, verifies the expected Developer ID, submits
the archive to Apple's notary service, waits for acceptance, staples and validates
the ticket, and runs Gatekeeper assessment. The final archive is rebuilt after
stapling. Its intermediate, unnotarized metadata remains `signed: false`.
Signing passwords and private key material are never placed in command arguments
by these helpers. Existing certificate/keychain provisioning and rotation remain
release-owner responsibilities. No signing/notarization run has been demonstrated
by the local unit tests.

The manual `.github/workflows/release.yml` uses the protected `release`
environment and dedicated self-hosted `release-signing` runners for each OS.
Provide Python 3.10+ and the native prerequisites on those hosts. Environment
secrets supply certificate/profile identifiers, and the timestamp URL is an
environment variable. The workflow requires the matching stable tag and prepares
artifacts; it never publishes or changes repository contents. Configure protected
environments, signing-host isolation, credentials and approval policy before
enabling this workflow. Do not run untrusted pull-request code on signing hosts.

## Signed update metadata

`services.updates.UpdateService()` has no default release channel and returns
`disabled` without network access. A release owner must first approve and pin a
32-byte Ed25519 public key, exact HTTPS manifest/signature URLs and HTTPS artifact
directory prefixes in trusted application configuration. No production key or
origin is invented by the code. Key rotation requires a reviewed trust update.

After collecting all three native artifacts and their metadata in one directory,
provide a protected raw 32-byte Ed25519 private key as hex in
`DEV_SETUP_RELEASE_SIGNING_KEY`, then run:

```text
python scripts/release_manifest.py --directory release-artifacts --version VERSION --sequence SEQUENCE --base-url https://YOUR-REVIEWED-ORIGIN/releases/VERSION/
```

The placeholder URL is documentation, not a configured channel. The producer
requires Windows/macOS native signing metadata and all three platforms, recomputes
archive hashes, signs the exact manifest bytes and verifies the detached signature
before writing `release.json` and `release.sig`. The sequence must increase with
every release. Keep the private key in protected secret storage; do not commit it,
place it in artifact files or use test keys for production. Archive metadata is a
build-stage handoff; release owners must verify native signatures independently
when accepting artifacts from outside their protected pipeline.

The consumer verifies Ed25519 before interpreting metadata, rejects duplicate
JSON keys, requires a compatible stable release and prevents version/sequence
downgrades using its pinned minimum and caller-supplied last accepted sequence.
Callers must persist their accepted sequence before enabling a production channel;
there is currently no GUI update-channel configuration. HTTPS uses the normal OS
trust store. Redirects are checked against the pinned locations, transfers are
bounded by size/time and cancellation, and artifacts must match their signed
SHA-256 and size. The transport checks its deadline between available-byte reads;
an in-flight read can take up to its 10-second socket timeout.

Downloads require approval of the exact reviewed offer, refuse to overwrite an
existing file and publish only after verification. They are never executed,
unpacked or installed automatically. App update verification is distinct from
native package-manager upgrades. The optional `cryptography>=46,<51` dependency
implements Ed25519 verification and is imported lazily, preserving Qt/crypto-free
core imports. There is no production update channel to exercise end to end yet.

## Release acceptance checklist

Before calling a version production ready, retain evidence for each OS and
architecture actually advertised:

1. Build on the native host and pass frozen self-check/desktop smoke tests.
2. Extract and launch on a fresh disposable VM without Python or this repository.
   Confirm catalog/presets load, Qt renders, and missing managers are explained.
3. Review and approve one supported install, inspect its progress and verification,
   then exercise cancellation and one native failure without corrupting history.
4. Change settings, complete an operation, restart, and verify persisted preferences
   and output-free history. Upgrade a version-1 fixture and confirm audit retention.
5. Replace the previous application bundle while it is closed, keeping the app-data
   directory. Verify settings/history survive. Back up state first; older apps must
   refuse future-schema databases without deleting them.
6. Verify Windows Authenticode or macOS notarization/Gatekeeper on the clean machine.
   Verify the detached manifest and artifact hash on Linux. Do not bypass OS prompts.
7. Serve an approved test channel and exercise signature failure, checksum failure,
   rejected redirects, interrupted downloads and version/sequence replay. Validate
   key/origin pinning independently of the producer.

Unit tests cover SQLite upgrades/rollback, signature/checksum failures, pinned
origins, consent, cancellation and packaging boundaries using temporary data and
ephemeral signing keys. Run them with:

```text
python -m unittest tests.unit.test_persistence tests.unit.test_updates tests.unit.test_diagnostics tests.integration.test_packaging
```

Native clean-machine runs, production signing credentials, approved origins/keys,
and real update publication remain external gates. Passing these deterministic
tests alone does not complete the production distribution phase.

## Troubleshooting

If a portable build cannot find its catalog or Qt plugin, extract the complete
archive and keep its files together. Use `--self-check` to separate bundled-data
problems from GUI initialization. On Linux, check the documented Qt system
libraries and glibc baseline. On macOS or Windows, use the platform's signature
inspection tools and report an unexpected security prompt to the release owner;
do not disable security checks. For database failures, retain a private backup and
follow [persistence.md](persistence.md). Diagnostic exports omit native output and
environment values, and must be explicitly selected and reviewed before sharing.

Implementation references: [PyInstaller signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing),
[Microsoft SignTool](https://learn.microsoft.com/windows/win32/seccrypto/signtool),
[Apple notarization](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow),
and [Ed25519](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).
