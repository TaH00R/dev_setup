"""Loopback-only signed-release fault fixture for disposable APT qualification."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from package_managers.apt import APT
from package_managers.apt_trust import _read
from package_managers.base import AdapterError
from scripts.apt_auth_checks import (
    AuthScope,
    _reader,
    has_indexes,
    prepare_scope,
    refresh_command,
    require_signature_rejection,
)
from services.desktop import DesktopServices
from services.records import PackageRequest
from system.commands import Approval


def tamper_release(payload: bytes) -> bytes:
    """Change one description byte; preserve identity fields and signature bytes."""
    signature = payload.find(b"-----BEGIN PGP SIGNATURE-----")
    field = b"\nDescription: "
    position = payload.find(field)
    if (
        len(payload) > 2 * 1024 * 1024
        or not payload.startswith(b"-----BEGIN PGP SIGNED MESSAGE-----\n")
        or position < 0
        or signature <= position + len(field)
        or b"\nOrigin: Ubuntu\n" not in payload[:signature]
        or b"\nCodename: noble\n" not in payload[:signature]
    ):
        raise ValueError("The native signed-release fixture has an unsupported format.")
    position += len(field)
    if not 32 <= payload[position] < 127:
        raise ValueError("The signed description has no safe byte to alter.")
    replacement = b"X" if payload[position : position + 1] != b"X" else b"Y"
    return payload[:position] + replacement + payload[position + 1 :]


@dataclass
class ReleaseEndpoint:
    port: int
    release_gets: int = 0
    unexpected_gets: int = 0
    closed: bool = False


@contextmanager
def serve_release(payload: bytes) -> Iterator[ReleaseEndpoint]:
    """Serve one in-memory path; no filesystem, proxying, directory listing or logs."""
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("The loopback release fixture exceeds its bound.")

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(2)
            super().setup()

        def do_GET(self) -> None:
            if self.path != "/dists/noble/InRelease":
                endpoint.unexpected_gets += 1
                self.send_error(404)
                return
            endpoint.release_gets += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except OSError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    endpoint = ReleaseEndpoint(server.server_port)
    thread = Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        if thread.is_alive():
            raise ValueError("The owned loopback fixture did not stop.")
        endpoint.closed = True


def prepare_tampered_scope(root: Path, key: Path, port: int) -> AuthScope:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("A live loopback fixture port is required.")
    scope = prepare_scope(root, key)
    manifest = scope.sources / "sources.list.d/ubuntu.sources"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "https://archive.ubuntu.com/ubuntu/", f"http://127.0.0.1:{port}/"
        ),
        encoding="utf-8",
    )
    with scope.bootstrap.open("a", encoding="utf-8") as stream:
        stream.write('Acquire::http::Proxy::127.0.0.1 "DIRECT";\n')
    return scope


def exercise_tampered_release(
    services: DesktopServices,
    checks: dict[str, str],
    root: Path,
    authenticated: AuthScope,
) -> None:
    adapter = services.inspectors[0].adapter
    if not isinstance(adapter, APT):
        raise ValueError("Signed-release qualification requires APT.")
    checks["phase"] = "prepare_tampered_release"
    releases = tuple(authenticated.lists.glob("*_dists_noble_InRelease"))
    if len(releases) != 1:
        raise ValueError("One freshly authenticated noble release is required.")
    payload = tamper_release(_read(releases[0], (authenticated.lists,)))
    with serve_release(payload) as endpoint:
        scope = prepare_tampered_scope(
            root / "tampered", authenticated.keys / "archive.gpg", endpoint.port
        )
        checks["phase"] = "reject_tampered_release"
        command = refresh_command(adapter, scope)
        result = services.operations.runner.run(command, approval=Approval(command))
        require_signature_rejection(result, marker="BADSIG")
        if not endpoint.release_gets or endpoint.unexpected_gets or has_indexes(scope):
            raise ValueError(
                "The altered release was not rejected before index retrieval."
            )
        native = _reader(services, adapter, scope)
        try:
            blocked = native.planner.preview((PackageRequest("cmake"),))
        except AdapterError:
            pass
        else:
            if blocked.plan.ready or not blocked.plan.issues:
                raise ValueError("Altered signed metadata produced an install plan.")
    if not endpoint.closed:
        raise ValueError("The owned fixture was not closed.")
    checks["payload_signature_tampering"] = "rejected_badsig"
    checks["tampered_release_exit_code"] = str(result.exit_code)
    checks["tampered_plan"] = "blocked"
    checks["tampered_cache_package_indexes"] = "none"
    checks["loopback_fixture"] = "closed_release_only"
    checks["tampering_change"] = "one_description_byte_signature_unchanged"
