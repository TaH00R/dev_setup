# Dev Setup --- Improvement Roadmap

> Consolidated improvement backlog from the repository review. Items are
> ordered by recommended implementation priority, not by difficulty.
> Some recommendations are investigations or validation tasks rather
> than confirmed defects.

## Phase 1 --- Reliability, safety, and core architecture

1.  **Refactor oversized GUI modules.**
    -   Break large files such as `app/ui/main_window.py` and
        `app/ui/environments.py` into focused pages, components,
        dialogs, workers, shell/navigation, and theme modules.
    -   Keep page rendering separate from application/service
        orchestration.
    -   Ensure individual pages can be changed and tested without
        affecting unrelated workflows.
2.  **Introduce a thin application orchestration layer.**
    -   Separate user-facing commands, queries, and workflows from
        domain logic and native adapters.

    -   Suggested structure:

        ``` text
        application/
          commands/
          queries/
          workflows/
        ```

    -   Reuse existing domain models and services; avoid introducing an
        unnecessarily complex CQRS framework.
3.  **Harden operation lifecycle management.**
    -   Give every operation a durable unique ID and explicit lifecycle
        state.
    -   Record the reviewed plan identity, pre-operation inventory,
        execution state, exit code, verification outcome, and
        partial-failure details.
    -   Distinguish `CANCEL_REQUESTED`, `CANCELLED`, `INTERRUPTED`,
        `FAILED`, `PARTIAL_SUCCESS`, and `SUCCEEDED`.
    -   Do not describe cancellation as rollback; package-manager
        changes may already have occurred.
4.  **Add interrupted-operation recovery.**
    -   On startup, detect operations that were active when the previous
        session ended.
    -   Mark their outcome as unknown until the current native inventory
        is inspected.
    -   Offer an inspect/reconcile workflow.
    -   Never automatically rerun an interrupted installation or update.
5.  **Add concurrency protection for native mutations.**
    -   Enforce mutation locks in the service layer, not only in GUI
        controllers.
    -   Use a global lock where package managers cannot safely mutate
        concurrently, plus per-backend locks where appropriate.
    -   Coordinate inventory reads with mutations.
    -   Consider cross-process locking so two Dev Setup instances cannot
        unknowingly mutate the same package manager simultaneously.
    -   Make retries idempotency-aware; never blindly retry a timed-out
        mutation.
6.  **Strengthen executable identity and process-launch security.**
    -   Review time-of-check/time-of-use risks between command review
        and execution.
    -   Capture and revalidate executable path and identity where
        practical.
    -   Test executable replacement and PATH manipulation scenarios.
    -   Pay particular attention to elevation helpers and
        platform-specific process launch behavior.
    -   Continue using argument arrays and `shell=False`; do not allow
        catalog metadata to supply arbitrary executable hooks.
7.  **Expand command-runner failure testing.**
    -   Test large stdout/stderr streams, output backpressure, timeouts,
        cancellation, child-process trees, and cleanup.
    -   Add Windows process-tree termination tests.
    -   Verify observer failures cannot leak processes or corrupt
        operation state.
    -   Treat process output as sensitive even after redaction; generic
        keyword redaction cannot guarantee every credential is hidden.
8.  **Improve safe diagnostics for startup and internal failures.**
    -   Preserve useful, sanitized diagnostic context when broad
        exception handling is necessary.
    -   Avoid exposing arbitrary exception text, local paths,
        credentials, or environment values in user-facing errors.
    -   Provide a clear route to inspect or export safe diagnostics.
9.  **Add structured application events and logging.**
    -   Define typed events such as `PackageInstalled`,
        `PackageRemoved`, and `OperationFailed`.
    -   Add correlation IDs and operation IDs.
    -   Keep structured metadata separate from raw process output.
    -   Support bounded/rotating local diagnostic logs and opt-in
        diagnostic export.
    -   Never upload logs automatically.
10. **Introduce a Qt-independent application event bus.**
    -   Let Dashboard, Installed, History, Queue, and notifications
        react to the same application events.
    -   Bridge application events to Qt signals at the UI boundary.
    -   Keep the event bus small and typed; avoid building a distributed
        messaging system inside the desktop app.
11. **Strengthen persistence and database recovery.**
    -   Add explicit SQLite schema versioning and migrations.
    -   Consider WAL mode, busy timeouts, and a serialized write
        connection.
    -   Persist operation lifecycle/recovery metadata and catalog
        revision references.
    -   Add tests for database corruption, disk-full conditions, failed
        writes, and recovery.
    -   Preserve the current session and actual operation result when
        persistence fails.
12. **Expand failure-injection and contract testing.**
    -   Add adapter contract tests and property-based tests for
        catalog/domain invariants.
    -   Test package-manager exit code 0 with failed post-install
        verification.
    -   Test inventory changing between preview and execution.
    -   Test network loss during an operation, application shutdown
        during execution, and simultaneous app instances.
    -   Test disk-full conditions while writing state.
    -   Test source changes after review and ensure stale plans are
        rejected.
    -   Keep native installation tests isolated in disposable
        VMs/containers where possible.

## Phase 2 --- Package catalog and discovery

13. **Formalize the three discovery trust levels.**
    -   Curated packages: reviewed mappings with explicit platform,
        source, and version metadata.
    -   Live discoveries: native search results that remain browse-only
        until explicitly mapped and reviewed.
    -   User-defined packages: local custom definitions with validation
        and optional contribution workflow.
    -   Never silently convert a live search result into an installable
        curated package.
14. **Build an explicit live-result mapping workflow.**
    -   Inspect native package metadata.
    -   Select the exact native source.
    -   Review package identity and platform compatibility.
    -   Validate the proposed mapping.
    -   Save a local mapping and optionally prepare a catalog
        contribution.
    -   Preserve provenance and verification status throughout.
15. **Expand catalog metadata validation.**
    -   Validate homepage and documentation URLs.
    -   Standardize license identifiers where feasible.
    -   Add release channel metadata (stable, beta, nightly).
    -   Add architecture constraints and minimum OS versions.
    -   Add reboot/restart requirements.
    -   Define verification strategy and expected post-install evidence.
    -   Provide alternatives and security-advisory references where
        useful.
    -   Distinguish metadata revision from the date a native mapping was
        last verified.
16. **Introduce catalog revisions and synchronization.**
    -   Add versioned catalog manifests and schema compatibility checks.
    -   Support incremental updates, local caching, and rollback to a
        previously valid catalog revision.
    -   Verify signed catalog metadata before accepting updates.
    -   Keep the application usable offline with its last valid catalog.
    -   Do not let remote metadata define arbitrary executable commands;
        command construction must remain in reviewed adapters.
17. **Add catalog quality and maintenance tooling.**
    -   Report stale mappings and review dates.
    -   Detect duplicate native mappings and inconsistent source
        identities.
    -   Make catalog contribution review reproducible.
    -   Add automated validation for every catalog change in CI.

## Phase 3 --- Cross-platform backend completeness

18. **Make backend capability reporting explicit and honest.**
    -   Distinguish command-builder support from end-to-end operational
        support.
    -   Expose capabilities per backend and action: search, inventory,
        install, update, remove, and verification.
    -   Keep unsupported operations visibly disabled rather than
        implying that a backend is fully supported.
19. **Complete APT and Homebrew end-to-end qualification.**
    -   Test install, update/upgrade, remove, inventory refresh,
        stale-plan rejection, and post-operation verification on clean
        supported systems.
    -   Document version and OS constraints.
    -   Treat these as the first fully supported backends before
        broadening the matrix.
20. **Implement Pacman end-to-end support.**
    -   Add reviewed inventory and transaction parsers.
    -   Add native integration tests in disposable Arch environments.
    -   Verify source identity, package versions, cancellation, and
        post-operation state.
21. **Implement Winget end-to-end support.**
    -   Add reviewed inventory and transaction parsers.
    -   Test source selection, package identity, version behavior,
        elevation, and verification on clean Windows systems.
22. **Implement DNF end-to-end support.**
    -   Add reviewed inventory and transaction parsers.
    -   Test package identity, repository provenance, update behavior,
        and verification in disposable Fedora environments.
23. **Implement the remaining package-manager adapters incrementally.**
    -   Prioritize Scoop, Chocolatey, Flatpak, Zypper, APK, Snap, and
        MacPorts based on user demand and maintainability.
    -   Require inventory and transaction parsing before enabling
        mutations.
    -   Do not treat a command builder alone as operational support.
24. **Keep runtime managers separate from system package managers.**
    -   Preserve the distinct adapter family for mise, uv, rustup, and
        future runtime managers.
    -   Provide a unified application-level provider interface only
        where capabilities genuinely overlap.
    -   Keep runtime installation, project-local version selection, and
        system package installation as distinct reviewed workflows.
25. **Add disposable native integration environments.**
    -   Use VMs or containers for supported package-manager integration
        tests where practical.
    -   Record which OS versions and package-manager versions were
        actually tested.
    -   Keep destructive tests away from developer workstations and
        production machines.

## Phase 4 --- Desktop product experience

26. **Build a dedicated package detail view.**
    -   Show description, vendor, homepage, documentation, license,
        installed version, available version, supported platforms,
        native sources, dependencies, trust/provenance, and estimated
        size.
    -   Display verification status and mapping review date.
    -   Route installation actions through the existing plan-review and
        consent flow.
27. **Make the installation queue a first-class workflow.**
    -   Allow users to select multiple packages before reviewing one
        combined plan.
    -   Show dependency ordering, conflicts, unsupported items,
        estimated disk usage, source, and privilege requirements.
    -   Display per-package progress and final verification.
    -   Support cancellation and safe retry only when the operation
        state makes retry appropriate.
    -   Represent partial success clearly.
28. **Improve dependency and change visualization.**
    -   Show which packages are direct selections and which are
        dependencies.
    -   Explain why each dependency is included.
    -   Display conflicts and planned removals/changes before consent.
    -   Keep the reviewed command and source information accessible.
29. **Improve progress, notifications, and empty/error states.**
    -   Provide clear operation progress and final outcomes.
    -   Explain when an operation is waiting for elevation or user
        approval.
    -   Distinguish unavailable backend, unsupported platform, network
        failure, verification failure, and unknown outcome.
    -   Ensure notifications never claim success before verification.
30. **Improve GUI modularity and interaction testing.**
    -   Separate pages, reusable components, dialogs, workers,
        navigation, and theme tokens.
    -   Add GUI tests for real user workflows, not just widget
        construction.
    -   Test long content, window resizing, accessibility, keyboard
        navigation, and high-DPI behavior.

## Phase 5 --- Developer environments and reproducibility

31. **Make developer environment profiles a central product feature.**
    -   Provide editable profiles for common workflows such as Web, Java
        Backend, Python/ML, C++ Systems, Rust, Android, DevOps, and
        Security Research.
    -   Treat profiles as portable manifests, not merely static package
        lists.
    -   Include package selections, runtime versions, and reviewed
        project configuration choices.
    -   Show unsupported profile items before execution.
32. **Improve environment manifest portability.**
    -   Add manifest schema migration and compatibility checks.
    -   Record platform constraints and explain unavailable components.
    -   Preserve exact runtime versions and source choices where
        supported.
    -   Keep package installation, runtime installation, and
        project-file changes as separate reviewed actions.
33. **Add development environment snapshots.**
    -   Capture an explicit, user-reviewed inventory of installed tools,
        runtime versions, and supported configuration.
    -   Compare snapshots to identify missing, changed, or outdated
        components.
    -   Allow export and import to reproduce a setup on another machine.
34. **Add safe configuration backup and restore.**
    -   Support selected shell, Git, editor, terminal, and tool
        configuration.
    -   Use explicit allowlists and preview every file change.
    -   Detect secrets and exclude credentials, SSH keys, `.env` files,
        and arbitrary home-directory contents by default.
    -   Never collect or overwrite user files without explicit review
        and consent.
35. **Expand project-local runtime configuration support.**
    -   Add more reviewed runtime managers and configuration formats
        when justified.
    -   Preserve strict path validation, symlink/reparse-point
        protections, bounded file sizes, and stale-file checks.
    -   Never silently modify global PATH or shell startup files.

## Phase 6 --- CLI and extensibility

36. **Expand the CLI using the same application services as the GUI.**
    -   Add search, inspect, installed inventory, update checks,
        environment export/import, and reviewed operation commands.
    -   Keep CLI and GUI behavior consistent.
    -   Require explicit consent for mutations and do not create a
        bypass around plan review.
    -   Provide machine-readable output for scripting where safe.
37. **Design a constrained provider/plugin model.**
    -   Start with declarative provider manifests and explicit
        capabilities.
    -   Consider integrations for VS Code extensions, JetBrains plugins,
        Neovim plugins, fonts, terminal themes, shell frameworks, and
        LSP servers.
    -   Do not begin with arbitrary third-party Python plugins.
    -   Treat third-party executable integrations as a separate security
        boundary requiring review and isolation.

## Phase 7 --- Distribution and release readiness

38. **Provide native installers for each platform.**
    -   Windows: MSI or MSIX.
    -   Linux: AppImage and/or Flatpak.
    -   macOS: DMG.
    -   Keep platform-specific packaging and permissions documented.
39. **Complete release signing and trust infrastructure.**
    -   Sign release artifacts where supported.
    -   Notarize macOS releases.
    -   Publish trusted release manifests and checksums.
    -   Pin and verify update metadata.
    -   Never execute an unverified downloaded artifact.
40. **Add clean-machine installation and upgrade qualification.**
    -   Test first install, launch, permissions, upgrade, migration,
        uninstall, and recovery on clean machines.
    -   Test without a development Python environment installed.
    -   Verify that failed upgrades preserve user data and provide a
        recovery path.
41. **Document support guarantees and release gates.**
    -   Publish tested OS/backend combinations.
    -   Separate experimental, browse-only, and fully supported
        capabilities.
    -   Record signing, notarization, and clean-machine evidence before
        calling a release production-ready.

## Product direction

42. **Position Dev Setup as a universal development environment
    manager.**
    -   Discover tools across native package managers.
    -   Install them through reviewed and verified operations.
    -   Compose complete development environments.
    -   Replicate environments on other machines.
    -   Maintain installed tools and detect updates.
    -   Diagnose environment state and missing components.
43. **Prioritize reliability before catalog breadth.**
    -   Avoid expanding package definitions faster than backend
        verification and operation safety.
    -   Make the installation engine dependable first.
    -   Then invest in the environment/profile experience and
        distribution.
    -   Use user demand and measurable backend completeness to decide
        what to build next.

------------------------------------------------------------------------

## Suggested immediate next steps

1.  Refactor the largest GUI modules without changing behavior.
2.  Add durable operation lifecycle and interrupted-operation recovery.
3.  Add cross-process mutation locking and executable identity checks.
4.  Expand failure-injection tests around the operation engine.
5.  Fully qualify APT and Homebrew on clean supported systems.
6.  Build the combined installation queue.
7.  Make developer environment profiles the signature product workflow.

## Review scope and caveat

This roadmap consolidates recommendations from a source-level review of
the repository. It is not a claim that every listed item is a confirmed
defect. Validate each item against the current code and tests before
implementation. Native runtime behavior, clean-machine packaging, and
security properties require dedicated testing on the target operating
systems.
