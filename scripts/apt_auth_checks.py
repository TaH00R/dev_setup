"""Opt-in native authentication checks with fresh, isolated APT caches."""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from models.errors import ErrorCode
from package_managers.apt import APT
from package_managers.base import AdapterError
from package_managers.inspection import APTInspector
from scripts.apt_origin_checks import PrivateListsRunner
from services.desktop import DesktopServices
from services.records import PackageRequest
from system.commands import Approval, Command, CommandResult
from system.identity import bind


@dataclass(frozen=True)
class AuthScope:
    root: Path
    bootstrap: Path
    lists: Path
    sources: Path
    keys: Path


def _quote(path: Path) -> str:
    text = path.as_posix()
    if (
        not path.is_absolute()
        or any(c in text for c in ('"', "\\"))
        or any(ord(c) < 32 for c in text)
    ):
        raise ValueError("Private APT paths cannot be encoded safely.")
    return '"' + text + '"'


def prepare_scope(root: Path, key: Path) -> AuthScope:
    """Create an exclusive empty scope; never inherit cached lists or native hooks."""
    _quote(root)
    root.mkdir()
    for name in (
        "state/lists/partial",
        "cache/archives/partial",
        "config/parts",
        "config/trusted.d",
        "sources/sources.list.d",
        "keys",
        "log",
    ):
        (root / name).mkdir(parents=True)
    # These fixtures contain only public repository metadata and public keys.
    # Let APT's normal _apt sandbox traverse them instead of forcing root fetches.
    for directory in (root, *(path for path in root.rglob("*") if path.is_dir())):
        directory.chmod(0o755)
    (root / "config/main.conf").write_text("", encoding="utf-8")
    shutil.copyfile(key, root / "keys/archive.gpg")
    manifest = (
        "Types: deb\nURIs: https://archive.ubuntu.com/ubuntu/\n"
        "Suites: noble\nComponents: main\nArchitectures: amd64\n"
        f"Signed-By: {(root / 'keys/archive.gpg').as_posix()}\n"
    )
    (root / "sources/sources.list.d/ubuntu.sources").write_text(
        manifest, encoding="utf-8"
    )
    paths = {
        "Dir::Etc::parts": root / "config/parts",
        "Dir::Etc::main": root / "config/main.conf",
        "Dir::Etc::sourcelist": root / "sources/sources.list",
        "Dir::Etc::sourceparts": root / "sources/sources.list.d",
        "Dir::Etc::trusted": root / "config/trusted.gpg",
        "Dir::Etc::trustedparts": root / "config/trusted.d",
        "Dir::State": root / "state",
        "Dir::State::lists": root / "state/lists",
        "Dir::Cache": root / "cache",
        "Dir::Cache::archives": root / "cache/archives",
        "Dir::Log": root / "log",
    }
    configuration = "\n".join(f"{name} {_quote(path)};" for name, path in paths.items())
    configuration += (
        '\nDir::State::status "/var/lib/dpkg/status";\n'
        'Dir::Cache::pkgcache "";\nDir::Cache::srcpkgcache "";\n'
        'Acquire::Languages "none";\nAcquire::Retries "0";\n'
        'Acquire::http::Timeout "15";\nAcquire::https::Timeout "15";\n'
    )
    bootstrap = root / "bootstrap.conf"
    bootstrap.write_text(configuration, encoding="utf-8")
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    return AuthScope(
        root, bootstrap, root / "state/lists", root / "sources", root / "keys"
    )


def refresh_command(adapter: APT, scope: AuthScope) -> Command:
    available = adapter.availability()
    if not available.available:
        raise ValueError("Private refresh requires the native APT executables.")
    return bind(
        Command(
            (dict(available.executables)["apt-get"], "--error-on=any", "update"),
            "Refresh private Ubuntu noble indexes with native signature checks",
            adapter.host.os,
            "ubuntu-noble",
            mutates=True,
            timeout_seconds=180,
            environment=(("LC_ALL", "C"), ("APT_CONFIG", str(scope.bootstrap))),
            launch_paths=(
                str(scope.bootstrap),
                str(scope.sources / "sources.list.d/ubuntu.sources"),
                str(scope.keys / "archive.gpg"),
            ),
        )
    )


def has_indexes(scope: AuthScope) -> bool:
    return any(
        path.is_file() and re.search(r"_Packages(?:\.(?:lz4|gz|xz))?$", path.name)
        for path in scope.lists.iterdir()
    )


def require_signature_rejection(result: CommandResult) -> None:
    if (
        result.exit_code != 100
        or result.output_truncated
        or (result.error is not None and result.error.code != ErrorCode.COMMAND_FAILED)
        or "NO_PUBKEY" not in result.stdout + result.stderr
    ):
        raise ValueError("Native rejection did not establish a missing signing key.")


def _reader(
    services: DesktopServices, adapter: APT, scope: AuthScope
) -> DesktopServices:
    runner = PrivateListsRunner(
        services.operations.runner, scope.lists, scope.sources, scope.bootstrap
    )
    inspector = APTInspector(
        adapter, runner, source_roots=(scope.sources,), key_roots=(scope.keys,)
    )
    return DesktopServices(
        services.catalog,
        services.host,
        (adapter,),
        (inspector,),
        runner,
        require_native_qualification=False,
    )


def exercise_apt_auth(
    services: DesktopServices, checks: dict[str, str], root: Path
) -> None:
    """Guarded disposable Ubuntu only; approval covers private index refreshes."""
    adapter = services.inspectors[0].adapter
    if not isinstance(adapter, APT):
        raise ValueError("Authentication qualification requires APT.")
    root.chmod(0o755)
    checks["phase"] = "refresh_current_key"
    trusted = prepare_scope(
        root / "current", Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
    )
    command = refresh_command(adapter, trusted)
    result = services.operations.runner.run(command, approval=Approval(command))
    if not result.succeeded or result.output_truncated or not has_indexes(trusted):
        raise ValueError("Fresh signed Ubuntu indexes did not authenticate.")
    native = _reader(services, adapter, trusted)
    plan = native.planner.preview((PackageRequest("cmake"),))
    if (
        not plan.plan.ready
        or len(plan.prepared) != 1
        or plan.prepared[0].installed is not None
        or plan.prepared[0].review is None
    ):
        raise ValueError(
            "Authenticated metadata did not produce the fixed CMake review."
        )
    version = plan.prepared[0].review.version
    if (
        version is None
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", version) is None
    ):
        raise ValueError("Authenticated version metadata exceeded its bound.")
    checks.update(
        source_id="ubuntu-noble", native_id="cmake", candidate_version=version
    )
    checks["current_key_refresh"] = "passed_fresh_signed_indexes"
    checks["authenticated_plan"] = "ready"
    checks["phase"] = "refresh_removed_keys"
    untrusted = prepare_scope(
        root / "removed", Path("/usr/share/keyrings/ubuntu-archive-removed-keys.gpg")
    )
    command = refresh_command(adapter, untrusted)
    rejected = services.operations.runner.run(command, approval=Approval(command))
    require_signature_rejection(rejected)
    if has_indexes(untrusted):
        raise ValueError("Rejected signatures retained usable package indexes.")
    native = _reader(services, adapter, untrusted)
    try:
        blocked = native.planner.preview((PackageRequest("cmake"),))
    except AdapterError:
        pass
    else:
        if blocked.plan.ready or not blocked.plan.issues:
            raise ValueError("Unauthenticated fresh metadata produced an install plan.")
    checks["removed_key_refresh"] = "rejected_no_pubkey"
    checks["removed_key_exit_code"] = str(rejected.exit_code)
    checks["rejected_cache_package_indexes"] = "none"
    checks["unauthenticated_plan"] = "blocked"
    checks["cache_reuse"] = "none_fresh_scopes"
    checks["package_mutation_dispatch"] = "none"
    checks["payload_signature_tampering"] = "not_tested"
    checks["phase"] = "complete"
