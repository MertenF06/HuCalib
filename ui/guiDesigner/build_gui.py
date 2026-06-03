# build_gui.py
import os
import re

def build_and_fix():
    # Ga naar het directory van dit script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # 1. Voer je standaard commando uit
    print("UI omzetten naar Python...")
    os.system("python -m PyQt5.uic.pyuic GUI.ui -o ../gui.py")

    # 2. Lees het gegenereerde bestand
    gui_path = "../gui.py"
    if os.path.exists(gui_path):
        with open(gui_path, 'r') as f:
            content = f.read()

        # 3. De Fix: vervang alle :: door .
        # We gebruiken regex om specifiek de Qt paden te pakken
        fixed_content = content.replace("::", ".")
        
        # Soms zet de generator ook 'Qt.Qt.Vertical' neer door de foutieve conversie
        # Dit fixen we ook voor de zekerheid
        fixed_content = fixed_content.replace("QtCore.Qt.Qt.", "QtCore.Qt.")

        with open(gui_path, 'w') as f:
            f.write(fixed_content)
        
        print("Klaar! gui.py is gegenereerd en de C++ syntax is verwijderd.")
    else:
        print("Fout: gui.py is niet gevonden.")

if __name__ == "__main__":
    build_and_fix()