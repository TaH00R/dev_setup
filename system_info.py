"""Display information about the current development environment.

Can be run on its own:

    python system_info.py

or invoked from the Dev Setup menu.
"""
import platform
import shutil

# (display name, executable looked up on PATH)
DEV_TOOLS = [
    ("Git", "git"),
    ("Python", "python"),
    ("GCC", "gcc"),
    ("VS Code", "code"),
    ("CMake", "cmake"),
    ("Node.js", "node"),
]


def get_system_info():
    """Return basic facts about the host environment."""
    return {
        "os": " ".join(part for part in (platform.system(), platform.release()) if part),
        "architecture": platform.machine() or "unknown",
        "python_version": platform.python_version(),
        "winget_available": shutil.which("winget") is not None,
    }


def get_installed_tools():
    """Return a list of (name, is_installed) for the known development tools."""
    return [(name, shutil.which(command) is not None) for name, command in DEV_TOOLS]


def show_system_info():
    """Print the environment summary and the status of each development tool."""
    info = get_system_info()

    print("\n=== System Information ===\n")
    print(f"OS:           {info['os'] or 'unknown'}")
    print(f"Architecture: {info['architecture']}")
    print(f"Python:       {info['python_version']}")
    print(f"Winget:       {'available' if info['winget_available'] else 'not found'}")

    print("\nInstalled Tools:")
    for name, installed in get_installed_tools():
        print(f"{'✓' if installed else '✗'} {name}")


if __name__ == "__main__":
    show_system_info()
