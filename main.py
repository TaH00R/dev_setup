from installers.gcc_installer import GCCInstaller
from installers.vscode_installer import VSCodeInstaller
from installers.python_installer import PythonInstaller
from installers.git_installer import GitInstaller


INSTALLERS = {
    "1": ("GCC", GCCInstaller.setup),
    "2": ("VS Code", VSCodeInstaller.setup),
    "3": ("Python", PythonInstaller.setup),
    "4": ("Git", GitInstaller.setup),
}


def show_menu():
    print("\n=== Dev Setup ===")

    for key, (name, _) in INSTALLERS.items():
        print(f"{key}. Install {name}")

    print("5. Install Everything")
    print("0. Exit")


def install_all():
    for name, installer in INSTALLERS.values():
        print(f"\nInstalling {name}...")
        installer()


def main():
    while True:
        show_menu()

        choice = input("\nEnter your choice: ").strip()

        if choice == "0":
            print("\nGoodbye!")
            break

        elif choice == "5":
            install_all()
            print("\nSetup Complete!")

        elif choice in INSTALLERS:
            name, installer = INSTALLERS[choice]

            print(f"\nInstalling {name}...")
            installer()

            print(f"\n{name} setup complete!")

        else:
            print("\nInvalid choice. Please try again.")


if __name__ == "__main__":
    main()