"""Plain-text rendering of reviewable domain data; no widget or process logic."""

from models.environment import ConfigPlan, RuntimePlan
from models.specs import PrivilegeRequirement
from services.records import ReviewedPlan


def package_review(plan: ReviewedPlan) -> tuple[str, bool]:
    lines = ["Review the selected source, exact commands and native effects."]
    elevation = False
    for issue in plan.plan.issues:
        lines.append(f"Blocked: {issue.code.value}: {issue.message}")
    for item, prepared in zip(plan.plan.items, plan.prepared):
        lines.extend(
            (
                "",
                f"{item.action.value}: {item.package_id}",
                f"Source: {item.spec.source.id} ({item.spec.source.url})",
                f"Backend: {item.spec.backend} / Native ID: {item.spec.native_id}",
                f"Version: {prepared.review.version if prepared.review else 'unknown'}",
                f"Dependencies: {', '.join(item.dependencies) or 'none'}",
            )
        )
        lines.extend(item.changes)
        if item.skip_reason:
            lines.append(item.skip_reason)
        if prepared.command:
            command = prepared.command
            lines.append("Command: " + repr(command.preview().display_args))
            lines.append("Privileges: " + command.privilege.value)
            elevation |= command.privilege != PrivilegeRequirement.NONE
    lines.append(
        "\nNative operations can leave partial changes. No rollback is promised."
    )
    return "\n".join(lines), elevation


def runtime_review(plan: RuntimePlan) -> str:
    return "\n".join(
        (
            f"{plan.request.manager}: {plan.request.runtime}@{plan.request.version}",
            f"Installed: {', '.join(plan.installed) or 'not detected'}",
            *plan.notes,
            "Command: "
            + (
                repr(plan.command.preview().display_args)
                if plan.command
                else plan.skip_reason or "none"
            ),
        )
    )


def config_review(plan: ConfigPlan) -> str:
    return "\n".join(
        (
            f"Project: {plan.project}",
            f"File: {plan.path}",
            *plan.notes,
            "\nCurrent content:\n" + (plan.previous_content or "(new file)"),
            "\nProposed content:\n" + plan.content,
        )
    )
