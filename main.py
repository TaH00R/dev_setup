from installers.gcc_installer_package_manager import GCCInstaller
from installers.vscode_installer_package_manager import VSCodeInstaller
from installers.python_installer import PythonInstaller
from installers.git_installer import GitInstaller
from system_info import show_system_info
from installers.java_installer import JavaInstaller
from installers.cmake_installer import CMakeInstaller
from installers.node_installer import NodeInstaller


INSTALLERS = {
    "1": ("C (GCC, GDB and Make included)", GCCInstaller.setup),
    "2": ("VS Code", VSCodeInstaller.setup),
    "3": ("Python", PythonInstaller.setup),
    "4": ("Git", GitInstaller.setup),
    "5": ("Java", JavaInstaller.setup),
    "6": ("CMake", CMakeInstaller.setup),
    "7": ("Node.js", NodeInstaller.setup)
}


def show_menu():
    print("\n=== Dev Setup ===")

    for key, (name, _) in INSTALLERS.items():
        print(f"{key}. Install {name}")

    print("8. Install Everything")
    print("9. System Information")
    print("0. Exit")


def install_all():
    for name, installer in INSTALLERS.values():
        print(f"\nInstalling {name}...")
        installer()


def main():
    print("\033[?1049h\033[0;0H")
    show_system_info()
    while True:

        show_menu()

        choice = input("\nEnter your choice: ").strip()
        print("\033[2J\033[0;0H")
        if choice == "0":
            print("\n\033[?1049lGoodbye!")
            break

        elif choice == "8":
            install_all()
            print("\nSetup Complete!")

        elif choice == "9":
            show_system_info()

        elif choice in INSTALLERS:
            name, installer = INSTALLERS[choice]

            print(f"\nInstalling {name}...")
            installer()

            print(f"\n{name} setup complete!")

        else:
            print("\nInvalid choice. Please try again.")


if __name__ == "__main__":
    main()