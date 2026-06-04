"""
estilos.py — Estilos Qt compartidos entre ambas aplicaciones dendrocronológicas.
"""

ESTILO_OSCURO = """
    QMainWindow, QDialog, QMessageBox { background-color: #2b2b2b; color: white; }
    QWidget { color: white; }
    QPushButton {
        background-color: #4a4a4a; color: white;
        padding: 6px; border-radius: 4px; min-width: 80px;
    }
    QPushButton:checked  { background-color: #5cb85c; color: white; font-weight: bold; }
    QPushButton:hover    { background-color: #5a5a5a; }
    QGraphicsView        { background-color: #1e1e1e; border: 1px solid #555; }
    QLabel               { color: white; background: transparent; }
    QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
        background-color: #4a4a4a; color: white;
        padding: 4px; border: 1px solid #666;
    }
    QComboBox QAbstractItemView {
        background-color: #4a4a4a; color: white;
        selection-background-color: #5cb85c;
    }
    QListView, QTreeView, QTableView {
        background-color: #3b3b3b; color: white; border: 1px solid #555;
    }
    QHeaderView::section {
        background-color: #4a4a4a; color: white;
        border: 1px solid #555; padding: 4px;
    }
    QTabWidget::pane { border: 1px solid #555; }
    QTabBar::tab {
        background: #3b3b3b; color: white;
        border: 1px solid #555; padding: 8px; min-width: 150px;
    }
    QTabBar::tab:selected { background: #5a5a5a; font-weight: bold; }
    QGroupBox {
        background-color: #2b2b2b; color: white;
        border: 1px solid #555; border-radius: 4px;
        margin-top: 8px; padding-top: 4px;
    }
    QGroupBox::title {
        subcontrol-origin: margin; subcontrol-position: top left;
        padding: 0 4px; color: white; background-color: #2b2b2b;
    }
    QScrollBar:vertical {
        background: #3b3b3b; width: 10px;
    }
    QScrollBar::handle:vertical {
        background: #5a5a5a; border-radius: 4px; min-height: 20px;
    }
"""

ESTILO_CLARO = """
    QMainWindow, QDialog, QMessageBox { background-color: #f0f0f0; color: black; }
    QWidget { color: black; }
    QPushButton {
        background-color: #e0e0e0; color: black;
        padding: 6px; border-radius: 4px; min-width: 80px;
    }
    QPushButton:checked  { background-color: #5cb85c; color: white; font-weight: bold; }
    QPushButton:hover    { background-color: #d0d0d0; }
    QGraphicsView        { background-color: #ffffff; border: 1px solid #ccc; }
    QLabel               { color: black; background: transparent; }
    QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
        background-color: white; color: black;
        padding: 4px; border: 1px solid #ccc;
    }
    QComboBox QAbstractItemView {
        background-color: white; color: black;
        selection-background-color: #e0e0e0;
    }
    QListView, QTreeView, QTableView {
        background-color: #ffffff; color: black; border: 1px solid #ccc;
    }
    QHeaderView::section {
        background-color: #e0e0e0; color: black;
        border: 1px solid #ccc; padding: 4px;
    }
    QTabWidget::pane { border: 1px solid #ccc; }
    QTabBar::tab {
        background: #e0e0e0; color: black;
        border: 1px solid #ccc; padding: 8px; min-width: 150px;
    }
    QTabBar::tab:selected { background: #ffffff; font-weight: bold; }
    QGroupBox {
        background-color: #f0f0f0; color: black;
        border: 1px solid #ccc; border-radius: 4px;
        margin-top: 8px; padding-top: 4px;
    }
    QGroupBox::title {
        subcontrol-origin: margin; subcontrol-position: top left;
        padding: 0 4px; color: black; background-color: #f0f0f0;
    }
    QScrollBar:vertical {
        background: #e0e0e0; width: 10px;
    }
    QScrollBar::handle:vertical {
        background: #b0b0b0; border-radius: 4px; min-height: 20px;
    }
"""
