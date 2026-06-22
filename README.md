# Dev Setup

Automated setup tool for programming languages, compilers, and development environments.

`Dev Setup` is an automated, easy-to-use setup tool designed for Windows to install programming languages, compilers, package managers, and development environments. It leverages `winget` (Windows Package Manager) and direct downloads to configure your environment seamlessly, logging its operations for easy verification.

---

## Features

- Git Installer
- VS Code Installer
- GCC Installer
- Python Installer (WIP)

### 📋 Supported Installers

The utility provides support for installing the following tools and development environments:

| # | Tool / Environment | Installation Method | Details |
|---|---|---|---|
| **1** | **GCC (MinGW-w64)** | Direct Download & Extract | Downloads the latest WinLibs GCC, extracts it to `%USERPROFILE%\DevSetup\gcc`, and updates your user environment PATH. |
| **2** | **VS Code** | Silent Installer & CLI | Installs Visual Studio Code silently, and installs popular extensions (`C/C++`, `CMake Tools`, `Error Lens`). |
| **3** | **Python** | `winget` | Installs Python 3.13 via Windows Package Manager. |
| **4** | **Git** | `winget` | Installs Git for Windows via Windows Package Manager. |
| **5** | **Java (JDK)** | `winget` | Installs Microsoft OpenJDK 21 via Windows Package Manager. |
| **6** | **CMake** | `winget` | Installs Kitware CMake via Windows Package Manager. |
| **7** | **Node.js** | `winget` | Installs OpenJS Node.js LTS via Windows Package Manager. |

---

## Roadmap

- Java Support
- Node.js Support
- Flutter Support
- Linux Support
- MacOS Support

---

## Installation & Getting Started

### Prerequisites

- **OS**: Windows (preferred, as installers target Windows dependencies).
- **Python**: Python 3.x installed.
- **Windows Package Manager (`winget`)**: Required for most installers.

### Quick Start

Clone this repository and run the setup script:

```bash
# Clone the repository
git clone https://github.com/TaH00R/dev_setup.git
cd dev_setup

# Install required dependencies
pip install -r requirements.txt

# Start the interactive setup menu
python main.py
```

---

## Usage

When you run `python main.py`, you will be presented with an interactive command-line interface:

```text
=== Dev Setup ===
1. Install GCC
2. Install VS Code
3. Install Python
4. Install Git
5. Install Java
6. Install CMake
7. Install Node.js
8. Install Everything
9. System Information
0. Exit

Enter your choice:
```

### Options

#### 1-7. Individual Installers
Select any number from `1` to `7` to run the setup for that specific tool. The tool will check if it is already installed before proceeding.

#### 8. Install Everything
Selecting option `8` will sequentially execute all 7 supported installers in order (GCC, VS Code, Python, Git, Java, CMake, Node.js). This is perfect for setting up a fresh development machine.

#### 9. System Information
Displays a summary of the current host environment and checks which dev tools are already installed on the system PATH.
This utility can also be run independently from the command line:
```bash
python system_info.py
```

Example Output:
```text
=== System Information ===

OS:           Windows 11
Architecture: AMD64
Python:       3.12.2
Winget:       available

Installed Tools:
✓ Git
✓ Python
✗ GCC
✓ VS Code
✗ CMake
✗ Node.js
```

---

## 🪵 Setup Logging

To help troubleshoot installations, `Dev Setup` logs details of all installation attempts, successes, and failures to a log file:
- **Log Location**: `logs/setup.log`
- **Format**: Entries are marked with timestamps.

Example log entry:
```text
[2026-06-22 18:45:00]
Installing GCC

[2026-06-22 18:46:12]
GCC installed successfully
```

> [!NOTE]
> Logging is designed to be best-effort. If the log directory cannot be created or the file is not writeable (due to permission constraints), the installers will still continue to run without throwing errors.

---

## 🧪 Development & Testing

### Running Tests Locally

The codebase has robust unit tests covering the installers, system info detection, logging, and error handling. You can run the test suite using Python's built-in `unittest` framework:

```bash
python -m unittest discover -s tests -v
```

### GitHub Actions CI

A continuous integration (CI) pipeline is set up via GitHub Actions. On every push and pull request to the `main` branch, the CI runner automatically:
1. Installs Python and updates dependencies.
2. Performs a syntax check using `compileall` across the files.
3. Lints the codebase using `ruff`.
4. Runs the entire unit test suite.

---

## Contributing

Contributions are welcome!
Check the Issues tab for beginner-friendly tasks.

If you want to contribute, please check [CONTRIBUTING.md](file:///c:/Users/ashwitha%20mobiles/Downloads/dev_setup-main/CONTRIBUTING.md) and see the list of open issues.
