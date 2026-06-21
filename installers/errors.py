"""Shared helpers for clear, actionable installer error messages.

Installers use these to explain *why* an installation failed and what the user
can do about it - a missing package manager, a permissions problem, or a
network failure - instead of a generic "Failed to install X".
"""
import shutil
import subprocess

import requests

WINGET_DOCS_URL = "https://learn.microsoft.com/windows/package-manager/winget/"

WINGET_MISSING_HINT = (
    "Winget was not found. Please verify that Winget is installed and "
    "available in PATH.\n"
    'Install "App Installer" from the Microsoft Store, or see '
    f"{WINGET_DOCS_URL}"
)

FILE_PERMISSION_HINT = (
    "Permission denied while writing files. Check the folder permissions, "
    "or run the terminal as Administrator."
)


def winget_available():
    """Return True if the winget executable can be found on PATH."""
    return shutil.which("winget") is not None


def describe_winget_error(error):
    """Return an actionable, human-readable explanation for a winget failure."""
    if isinstance(error, FileNotFoundError):
        return WINGET_MISSING_HINT
    if isinstance(error, PermissionError):
        return (
            "Permission denied. Try running the terminal as Administrator, "
            "then run the installer again."
        )
    if isinstance(error, subprocess.CalledProcessError):
        return (
            f"Winget exited with code {error.returncode}.\n"
            "- For a permissions error, run the terminal as Administrator.\n"
            "- For a network error, check your internet connection and try again."
        )
    return str(error) or "An unexpected error occurred."


def describe_network_error(error):
    """Return an actionable, human-readable explanation for a download failure."""
    if isinstance(error, requests.exceptions.Timeout):
        return "The connection timed out. Check your internet connection and try again."
    if isinstance(error, requests.exceptions.ConnectionError):
        return (
            "Could not reach the download server. Check your internet "
            "connection and try again."
        )
    if isinstance(error, requests.exceptions.HTTPError):
        return (
            f"The download server returned an error ({error}). "
            "The file may be temporarily unavailable."
        )
    return str(error) or "An unexpected network error occurred."


def report_failure(action, hint):
    """Print a consistent failure message: what failed, then why and what to do.

    Example:
        x Failed to install Git
          Winget was not found. Please verify that Winget is installed ...
    """
    print(f"✗ {action}")
    for line in hint.splitlines():
        print(f"  {line}")
