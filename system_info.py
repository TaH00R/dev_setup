"""Display information about the current development environment.

Can be run on its own:

    python system_info.py

or invoked from the Dev Setup menu.
"""
import platform
import re
import shutil
import subprocess
# (display name, executable looked up on PATH)
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


def get_tool_version(command, version_args):
    """Get the version string for a given tool command.
    
    Args:
        command: The executable name (e.g., 'git', 'python')
        version_args: List of arguments to get version (e.g., ['--version'])
    
    Returns:
        Version string if found, None if tool not installed or version not detected
    """
    try:
        result = subprocess.run(
            [command] + version_args,
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        
        # Combine stdout and stderr (some tools output to stderr)
        output = (result.stdout + result.stderr).strip()
        
        # Extract version number using regex
        # Matches patterns like: 2.51.0, 3.13.5, 1.102.0, etc.
        version_match = re.search(r'(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)', output)
        
        if version_match:
            return version_match.group(1)
        
        # If no version pattern found, return first line (fallback)
        first_line = output.split('\n')[0] if output else None
        return first_line
        
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


def get_installed_tools():
    """Return a list of (name, is_installed, version) for the known development tools."""
    tools_info = []
    
    for name, command, version_args in DEV_TOOLS:
        is_installed = shutil.which(command) is not None
        version = None
        
        if is_installed:
            version = get_tool_version(command, version_args)
        
        tools_info.append((name, is_installed, version))
    
    return tools_info


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
        if installed:
            if version:
                print(f"✓ {name} {version}")
            else:
                print(f"✓ {name} (version unknown)")
        else:
            print(f"✗ {name}")


if __name__ == "__main__":
    show_system_info()
