"""Dependency-ordered review; only read-only native inspection is permitted."""

from dataclasses import replace
from threading import Event
from uuid import uuid4

from catalog.service import CatalogService
from models.catalog import Package
from models.errors import ErrorCode, OperationError
from models.inventory import Detection, Inventory
from models.package import PackageVersion
from models.planning import InstallPlan, OperationAction, PlanItem
from models.platform import Platform
from models.specs import InstallSpec
from package_managers.base import AdapterError, BackendAction

from .inventory import Inspector
from .records import PackageRequest, PreparedItem, ReviewedPlan


def backend_action(action: OperationAction) -> BackendAction:
    return BackendAction(action.value)


class Planner:
    def __init__(
        self, catalog: CatalogService, host: Platform, inspectors: tuple[Inspector, ...]
    ) -> None:
        self.catalog = catalog
        self.host = host
        self.inspectors = {i.adapter.id: i for i in inspectors}
        if len(self.inspectors) != len(inspectors) or any(
            i.adapter.host != host for i in inspectors
        ):
            raise ValueError("inspectors must be unique and match the target platform")

    def preview(
        self,
        requests: tuple[PackageRequest, ...],
        *,
        cancel: Event | None = None,
        observations: tuple[Detection, ...] = (),
    ) -> ReviewedPlan:
        issues: list[OperationError] = []
        selections: dict[str, tuple[Package, PackageRequest, InstallSpec]] = {}
        ordered: list[str] = []
        visiting: set[str] = set()
        snapshots: dict[str, Inventory] = {}
        explicit: dict[str, PackageRequest] = {}

        def issue(code: ErrorCode, message: str) -> None:
            error = OperationError(code, message)
            if error not in issues:
                issues.append(error)

        def inventory(backend: str) -> Inventory:
            if backend not in snapshots:
                inspector = self.inspectors.get(backend)
                if inspector is None:
                    raise AdapterError(
                        ErrorCode.UNSUPPORTED_OPERATION,
                        "This backend has no reviewed inventory/transaction inspector yet.",
                    )
                snapshots[backend] = inspector.inventory(cancel)
                if snapshots[backend].backend != backend:
                    raise AdapterError(
                        ErrorCode.VERIFICATION_FAILED,
                        "Inventory backend identity does not match the selection.",
                    )
            return snapshots[backend]

        for request in requests:
            if request.package_id in explicit:
                issue(
                    ErrorCode.DEPENDENCY_CONFLICT,
                    "A package was requested more than once; select one action and source.",
                )
            explicit[request.package_id] = request

        def visit(request: PackageRequest) -> None:
            package_id = request.package_id
            if cancel and cancel.is_set():
                issue(ErrorCode.CANCELLED, "Planning was cancelled.")
                return
            if package_id in visiting:
                issue(
                    ErrorCode.DEPENDENCY_CYCLE,
                    "The requested dependency graph contains a cycle.",
                )
                return
            if package_id in selections:
                selected_package, previous, selected_spec = selections[package_id]
                if previous.action != request.action or (
                    request.version is not None
                    and previous.version is not None
                    and previous.version != request.version
                ):
                    issue(
                        ErrorCode.DEPENDENCY_CONFLICT,
                        "Dependency actions or exact versions disagree.",
                    )
                elif request.version is not None and previous.version is None:
                    if (
                        selected_spec.version is not None
                        and selected_spec.version != request.version
                    ):
                        issue(
                            ErrorCode.VERSION_MISMATCH,
                            "Required dependency version conflicts with the catalog mapping.",
                        )
                    else:
                        selections[package_id] = (
                            selected_package,
                            replace(previous, version=request.version),
                            replace(selected_spec, version=request.version),
                        )
                return
            package = self.catalog.get(package_id)
            if package is None:
                issue(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "A requested package is absent from the curated catalog.",
                )
                return
            matches = tuple(
                s
                for s in package.install_specs
                if s.support.supports(self.host)
                and (request.backend is None or s.backend == request.backend)
                and (request.source_id is None or s.source.id == request.source_id)
            )
            if len(matches) != 1:
                issue(
                    ErrorCode.UNSUPPORTED_PLATFORM
                    if not matches
                    else ErrorCode.UNSUPPORTED_OPERATION,
                    "Select one exact compatible backend/source mapping; no automatic substitution is performed.",
                )
                return
            spec = matches[0]
            if request.version is not None:
                if spec.version is not None and spec.version != request.version:
                    issue(
                        ErrorCode.VERSION_MISMATCH,
                        "Requested version conflicts with the catalog mapping.",
                    )
                    return
                spec = replace(spec, version=request.version)
            visiting.add(package_id)
            if request.action in {OperationAction.INSTALL, OperationAction.UPGRADE}:
                for dep in package.dependencies:
                    dep_request = explicit.get(
                        dep.package_id,
                        PackageRequest(dep.package_id, version=dep.version),
                    )
                    if dep_request.action not in {
                        OperationAction.INSTALL,
                        OperationAction.UPGRADE,
                    } or (
                        dep.version is not None
                        and dep_request.version not in (None, dep.version)
                    ):
                        issue(
                            ErrorCode.DEPENDENCY_CONFLICT,
                            "A required dependency is removed, read-only, or has a conflicting version.",
                        )
                        continue
                    if dep.version is not None and dep_request.version is None:
                        dep_request = replace(dep_request, version=dep.version)
                    visit(dep_request)
            visiting.remove(package_id)
            selections[package_id] = (package, request, spec)
            ordered.append(package_id)

        for request in requests:
            visit(request)
        # Cyclic/invalid graphs must never be represented as executable partial plans.
        if issues:
            return ReviewedPlan(
                InstallPlan(uuid4().hex, self.host, (), tuple(issues)), ()
            )

        def installed_package(package: Package) -> bool:
            found = False
            for spec in package.install_specs:
                if spec.support.supports(self.host):
                    found = (
                        inventory(spec.backend).get(spec.native_id) is not None or found
                    )
            return found

        items = []
        prepared = []
        for package_id in ordered:
            package, request, spec = selections[package_id]
            try:
                inspector = self.inspectors.get(spec.backend)
                if inspector is None:
                    raise AdapterError(
                        ErrorCode.UNSUPPORTED_OPERATION,
                        "This backend needs a reviewed inspection implementation before orchestration.",
                    )
                snapshot = inventory(spec.backend)
                installed = snapshot.get(spec.native_id)
                if (
                    installed
                    and installed.source_id is not None
                    and installed.source_id != spec.source.id
                ):
                    raise AdapterError(
                        ErrorCode.DEPENDENCY_CONFLICT,
                        "Installed package source differs from the selected source.",
                    )
                for other in self.catalog.packages:
                    conflicting = (
                        other.id in package.conflicts or package_id in other.conflicts
                    )
                    if conflicting and request.action in {
                        OperationAction.INSTALL,
                        OperationAction.UPGRADE,
                    }:
                        selected = selections.get(other.id)
                        if (
                            selected and selected[1].action != OperationAction.UNINSTALL
                        ) or installed_package(other):
                            raise AdapterError(
                                ErrorCode.DEPENDENCY_CONFLICT,
                                "A conflicting catalog package is installed or selected; resolve it separately.",
                            )
                    if (
                        request.action == OperationAction.UNINSTALL
                        and any(d.package_id == package_id for d in other.dependencies)
                        and installed_package(other)
                    ):
                        raise AdapterError(
                            ErrorCode.DEPENDENCY_CONFLICT,
                            "An installed catalog package depends on this package; removal is blocked.",
                        )
                skip = None
                if request.action == OperationAction.INSTALL and installed:
                    if (
                        spec.version is not None
                        and spec.version.value not in installed.versions
                    ):
                        raise AdapterError(
                            ErrorCode.VERSION_MISMATCH,
                            "Installed native version differs from the requested version; use an explicit upgrade workflow.",
                        )
                    skip = "Already installed in the selected native inventory."
                elif request.action == OperationAction.UNINSTALL and installed is None:
                    skip = "Not installed in the selected native inventory."
                elif (
                    request.action in {OperationAction.UPGRADE, OperationAction.VERIFY}
                    and installed is None
                ):
                    raise AdapterError(
                        ErrorCode.VERIFICATION_FAILED,
                        "The selected native package is not installed.",
                    )
                command = None
                review = None
                changes: tuple[str, ...] = ()
                if skip is None and request.action != OperationAction.VERIFY:
                    action = backend_action(request.action)
                    # Validate the user's selection before read-only candidate resolution.
                    inspector.adapter.build(action, spec=spec)
                    review = inspector.review(spec, request.action, cancel)
                    if request.action != OperationAction.UNINSTALL:
                        if review.version is None:
                            raise AdapterError(
                                ErrorCode.VERIFICATION_FAILED,
                                "Native review did not resolve a candidate version.",
                            )
                        if spec.version and spec.version.value != review.version:
                            raise AdapterError(
                                ErrorCode.VERSION_MISMATCH,
                                "Native candidate differs from the requested exact version.",
                            )
                        if action in inspector.adapter.capabilities.version_selection:
                            spec = replace(spec, version=PackageVersion(review.version))
                        if (
                            request.action == OperationAction.UPGRADE
                            and installed
                            and review.version in installed.versions
                        ):
                            skip = "The reviewed native candidate is already installed."
                    if skip is None:
                        if not review.changes:
                            raise AdapterError(
                                ErrorCode.VERIFICATION_FAILED,
                                "Native review did not identify transaction effects.",
                            )
                        command = inspector.adapter.build(action, spec=spec)
                        changes = tuple(
                            dict.fromkeys(
                                (
                                    *review.changes,
                                    *spec.notes,
                                    *(
                                        "Environment guidance (not applied by this service): "
                                        + guidance
                                        for guidance in package.environment_guidance
                                    ),
                                    *(
                                        f"Executable {d.executable} is already on PATH; its origin and relationship to this native package are unverified."
                                        for d in observations
                                        if d.package_id == package_id and d.present
                                    ),
                                    "Native package-manager hooks may change the system; no rollback is implemented.",
                                    "Native installed inventory verifies package identity/version, not service health or historical download provenance.",
                                )
                            )
                        )
                if (
                    request.action == OperationAction.VERIFY
                    and spec.version
                    and installed
                    and spec.version.value not in installed.versions
                ):
                    raise AdapterError(
                        ErrorCode.VERSION_MISMATCH,
                        "Installed native version differs from the verification request.",
                    )
                deps = (
                    tuple(d.package_id for d in package.dependencies)
                    if request.action
                    in {OperationAction.INSTALL, OperationAction.UPGRADE}
                    else ()
                )
                items.append(
                    PlanItem(
                        package_id,
                        request.action,
                        spec,
                        (command.preview(),) if command else (),
                        deps,
                        changes,
                        skip,
                    )
                )
                prepared.append(PreparedItem(command, installed, review))
            except AdapterError as error:
                issue(error.error.code, error.error.message)
        if issues:
            # In particular, avoid PlanItem's topological invariant masking an
            # earlier failed dependency inspection with a constructor exception.
            return ReviewedPlan(
                InstallPlan(uuid4().hex, self.host, (), tuple(issues)), ()
            )
        return ReviewedPlan(
            InstallPlan(uuid4().hex, self.host, tuple(items)), tuple(prepared)
        )
