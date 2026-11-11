"""Small shared Qt widget theme, with no platform-specific behavior."""

from PySide6.QtWidgets import QApplication


def apply_theme(dark: bool = False) -> None:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        return
    background, surface, text, muted, border = (
        ("#131c27", "#1d2938", "#e8eef5", "#aab9ca", "#344456")
        if dark
        else ("#f3f5f7", "#ffffff", "#172536", "#52667b", "#d9e0e7")
    )
    application.setStyleSheet(f"""
        QWidget {{ color: {text}; font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; }}
        QMainWindow, QDialog, QWidget#page {{ background: {background}; }}
        QFrame#sidebar {{ background: #142637; border: none; }}
        QFrame#sidebar QLabel {{ color: #e8f1f7; background: transparent; }}
        QLabel#brand {{ font-size: 23px; font-weight: 700; padding: 12px 4px; }}
        QLabel#pageTitle {{ font-size: 28px; font-weight: 650; margin-bottom: 5px; }}
        QLabel#sectionTitle {{ font-size: 19px; font-weight: 600; }}
        QLabel#muted {{ color: {muted}; }}
        QListWidget#navigation {{ background: transparent; color: #d6e1eb; border: none; outline: none; }}
        QListWidget#navigation::item {{ padding: 12px; margin: 3px 0; border-radius: 6px; }}
        QListWidget#navigation::item:selected {{ background: #24566a; color: white; }}
        QGroupBox {{ background: {surface}; border: 1px solid {border}; border-radius: 9px; margin-top: 12px; padding: 16px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 5px; }}
        QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QListWidget, QTableWidget {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; padding: 6px; selection-background-color: #d5ecea; selection-color: #102f35; }}
        QListWidget::item {{ padding: 10px; border-bottom: 1px solid {border}; }}
        QListWidget::item:selected {{ background: #d5ecea; color: #102f35; }}
        QPushButton {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; padding: 8px 14px; }}
        QPushButton:hover {{ border-color: #247c7b; }}
        QPushButton#primary {{ background: #116a68; color: white; border-color: #116a68; }}
        QPushButton:disabled {{ color: {muted}; background: {background}; }}
        QHeaderView::section {{ background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {border}; padding: 8px; }}
        QTableWidget {{ gridline-color: {border}; }}
        QProgressBar {{ border: 1px solid {border}; border-radius: 4px; max-height: 7px; }}
        QProgressBar::chunk {{ background: #208c87; }}
    """)
