"""Display information about the current development environment.

Can be run on its own:

    python system_info.py

or invoked from the Dev Setup menu.
"""
import platform
import re
import shutil
import subprocess
# (display name, executable looked up on PATH, version arguments)
DEV_TOOLS = [
    ("Git", "git", ["--version"]),
    ("Python", "python", ["--version"]),
    ("GCC", "gcc", ["--version"]),
    ("GDB", "gdb", ["--version"]),
    ("Make", "make", ["--version"]),
    ("VS Code", "code", ["--version"]),
    ("CMake", "cmake", ["--version"]),
    ("Java", "java", ["-version"]),
    ("Node.js", "node", ["--version"]),
]


def get_system_info():
    """Return basic facts about the host environment."""
    return {
        "os": " ".join(part for part in (platform.system(), platform.release()) if part),
        "architecture": platform.machine() or "unknown",
        "python_version": platform.python_version(),
        "winget_available": shutil.which("winget") is not None,
    }


def get_tool_version(command, args):
    """Probe a known executable; unavailable or failed probes return None."""
    try:
        result = subprocess.run(
            [command, *args], capture_output=True, text=True,
            check=True, timeout=5, shell=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    output = (result.stdout or result.stderr).strip()
    if not output:
        return None
    first_line = output.splitlines()[0]
    version = re.search(r"\d+(?:\.\d+)+", first_line)
    return version.group() if version else first_line


def get_installed_tools():
    """Return (name, is_installed, version) records for known tools."""
    tools = []
    for name, command, args in DEV_TOOLS:
        installed = shutil.which(command) is not None
        version = get_tool_version(command, args) if installed else None
        tools.append((name, installed, version))
    return tools

def show_system_info():
    """Print the environment summary and the status of each development tool."""
    info = get_system_info()

    print("\n=== System Information ===\n")
    print(f"OS:           {info['os'] or 'unknown'}")
    print(f"Architecture: {info['architecture']}")
    print(f"Python:       {info['python_version']}")
    if platform.system() == "Windows":
        print(f"Winget:       {'available' if info['winget_available'] else 'not found'}")

    print("\nInstalled Tools:")
    for name, installed, version in get_installed_tools():
        suffix = f" ({version})" if version else ""
        print(f"{'✓' if installed else '✗'} {name}{suffix}")


if __name__ == "__main__":
    show_system_info()
