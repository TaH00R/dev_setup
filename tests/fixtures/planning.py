"""Synthetic operation plans shared by domain tests."""

from models.package import PackageSource, SourceKind, TrustLevel
from models.planning import CommandPreview, OperationAction, PlanItem
from models.platform import Architecture, OperatingSystem
from models.specs import InstallSpec, PlatformSupport


def example_item(package_id="example.tool", **kwargs):
    source = PackageSource("example", "Example", SourceKind.CURATED, TrustLevel.UNKNOWN)
    support = PlatformSupport(OperatingSystem.WINDOWS, (Architecture.X86_64,))
    spec = InstallSpec(support, "example", "Example.Tool", source)
    return PlanItem(
        package_id,
        OperationAction.INSTALL,
        spec,
        (
            CommandPreview(
                ("example-manager", "install", "Example.Tool"), "Install tool"
            ),
        ),
        **kwargs,
    )
