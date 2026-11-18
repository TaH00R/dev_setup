"""Terminal pages with stable keyboard focus order and plain-text widgets."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TextArea

PAGES = (
    "Dashboard",
    "Explore",
    "Installed",
    "Updates",
    "Queue",
    "Presets",
    "Runtimes",
    "History",
    "Settings",
)


def table(identity: str) -> DataTable[Text]:
    return DataTable(id=identity, cursor_type="row", zebra_stripes=True)


def pages() -> ComposeResult:
    with VerticalScroll(id="dashboard"):
        yield Static(
            "Detecting platform and loading local state…", id="system", markup=False
        )
        yield Button("Refresh inventory", id="refresh", variant="primary")
        yield Static("", id="backends", markup=False)
    with VerticalScroll(id="explore"):
        yield Input(
            placeholder="Search curated tools and available native sources", id="search"
        )
        yield Select(
            [("All categories", "all")], value="all", allow_blank=False, id="category"
        )
        yield Static("", id="search-status", markup=False)
        yield table("catalog")
        yield Select(
            [("Choose a package", "none")], value="none", allow_blank=False, id="source"
        )
        yield Static(
            "Select a package to inspect its source and platform support.",
            id="details",
            markup=False,
        )
        yield Input(
            placeholder="Optional exact package version; leave blank for reviewed candidate",
            id="package-version",
        )
        with Horizontal(classes="actions"):
            yield Button("Add install", id="add-install")
            yield Button("Add upgrade", id="add-upgrade")
            yield Button("Add removal", id="add-uninstall", variant="warning")
            yield Button("Verify", id="verify")
        yield Button("Review combined plan", id="preview", variant="primary")
    with VerticalScroll(id="installed"):
        yield Button("Refresh installed tools", id="refresh-installed")
        yield table("installed-table")
        yield Button("Inspect selected package", id="inspect-installed")
    with VerticalScroll(id="updates"):
        yield Button("Check tool updates", id="tool-updates")
        yield table("updates-table")
        yield Static("", id="update-errors", markup=False)
        yield Button("Review all offered upgrades", id="review-updates")
    with VerticalScroll(id="queue"):
        yield Static("No active operation", id="operation-status", markup=False)
        yield table("cart")
        with Horizontal(classes="actions"):
            yield Button("Review queue", id="queue-review", variant="primary")
            yield Button("Remove selected request", id="queue-remove")
            yield Button("Cancel active work", id="cancel", variant="warning")
        yield TextArea("", id="logs", read_only=True)
        yield Static(
            "Interrupted operations require inspection and acknowledgement.",
            markup=False,
        )
        yield table("recovery-table")
        with Horizontal(classes="actions"):
            yield Button("Inspect interrupted work", id="recovery-inspect")
            yield Button("Acknowledge inspected outcome", id="recovery-ack")
    with VerticalScroll(id="presets"):
        yield Select(
            [("Choose a preset", "none")], value="none", allow_blank=False, id="preset"
        )
        yield Input(placeholder="Collection name", id="collection-name")
        yield Input(
            placeholder="Package IDs separated by commas (for example git,python)",
            id="collection-packages",
        )
        yield Static(
            "Runtime/configuration choices from imported manifests are retained. Add runtime choices on Runtimes.",
            markup=False,
        )
        yield Input(placeholder="Manifest file path", id="manifest-path")
        with Horizontal(classes="actions"):
            yield Button("Import manifest", id="manifest-import")
            yield Button("Export to new file", id="manifest-export")
            yield Button("Review packages", id="manifest-preview", variant="primary")
        yield TextArea("", id="manifest-details", read_only=True)
    with VerticalScroll(id="runtimes"):
        yield Select(
            [("mise", "mise"), ("uv", "uv"), ("rustup", "rustup")],
            value="mise",
            allow_blank=False,
            id="runtime-manager",
        )
        yield Input(
            value="python",
            placeholder="Runtime name: python, node or rust",
            id="runtime-name",
        )
        yield Input(
            placeholder="Exact version, for example 3.14.0", id="runtime-version"
        )
        with Horizontal(classes="actions"):
            yield Button(
                "Review runtime install", id="runtime-preview", variant="primary"
            )
            yield Button("Add to collection", id="runtime-add")
        yield Static(
            "Project files and environment guidance are applied only after a separate review.",
            markup=False,
        )
        yield Input(placeholder="Existing project directory", id="project-path")
        yield Select(
            [(v, v) for v in ("python", "rust", "mise-python", "mise-node")],
            value="python",
            allow_blank=False,
            id="config-kind",
        )
        yield Input(placeholder="Exact project runtime version", id="config-version")
        yield Button("Review project configuration", id="config-preview")
        yield Button("Add configuration to collection", id="config-add")
        yield Select(
            [(v, v) for v in ("JAVA_HOME", "PYTHONPATH", "GOPATH", "CARGO_HOME")],
            value="JAVA_HOME",
            allow_blank=False,
            id="env-name",
        )
        yield Input(
            placeholder="Project-relative directory (for example tools/java)",
            id="env-path",
        )
        yield Button("Review project environment file", id="env-preview")
        yield Button("Add environment choice to collection", id="env-add")
    with VerticalScroll(id="history"):
        yield table("history-table")
        yield Button("Review fresh retry of selected item", id="history-retry")
        yield Button("Clear audit history…", id="history-clear", variant="warning")
    with VerticalScroll(id="settings"):
        yield Select(
            [("Dark", "dark"), ("Light", "light")],
            value="dark",
            allow_blank=False,
            id="theme-choice",
        )
        yield Checkbox("Search detected native sources", True, id="live-search")
        yield Input(
            value="250",
            placeholder="Search debounce in milliseconds (150–1000)",
            id="search-delay",
        )
        yield Button("Save settings", id="settings-save", variant="primary")
        yield Button("Preview diagnostics", id="diagnostics-preview")
        yield Input(placeholder="New diagnostics JSON file path", id="diagnostics-path")
        yield Button("Export reviewed diagnostics", id="diagnostics-export")
        yield Button("Check application updates", id="app-updates")
        yield TextArea("", id="support-details", read_only=True)
