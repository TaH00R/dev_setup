"""Explicit keyboard-accessible consent with separate elevation approval."""

import re
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, TextArea


def plain(value: object) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", str(value))


@dataclass(frozen=True)
class Decision:
    elevation: bool = False


class Review(ModalScreen[Decision | None]):
    BINDINGS = [("escape", "decline", "Cancel")]
    DEFAULT_CSS = """
    Review { align: center middle; }
    Review > Vertical { width: 85%; height: 85%; border: thick $accent;
        background: $surface; padding: 1 2; }
    Review TextArea { height: 1fr; margin: 1 0; }
    Review Horizontal { height: 3; }
    Review Checkbox { height: auto; }
    """

    def __init__(
        self, title: str, content: str, *, ready: bool = True, elevation: bool = False
    ) -> None:
        super().__init__()
        self.title_text, self.content = title, content
        self.ready, self.elevation = ready, elevation

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(plain(self.title_text), markup=False)
            yield TextArea(plain(self.content), read_only=True, id="review-text")
            yield Checkbox(
                "I reviewed and approve these changes",
                id="consent",
                disabled=not self.ready,
            )
            yield Checkbox(
                "Allow the native administrator prompt",
                id="elevation",
                disabled=not self.elevation,
            )
            with Horizontal():
                yield Button("Cancel", id="decline")
                yield Button("Proceed", id="approve", variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#decline", Button).focus()

    def on_checkbox_changed(self) -> None:
        self.query_one("#approve", Button).disabled = not (
            self.ready
            and self.query_one("#consent", Checkbox).value
            and (not self.elevation or self.query_one("#elevation", Checkbox).value)
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "approve" and not event.button.disabled:
            self.dismiss(Decision(self.query_one("#elevation", Checkbox).value))
        else:
            self.dismiss(None)

    def action_decline(self) -> None:
        self.dismiss(None)
