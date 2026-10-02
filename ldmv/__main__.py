"""Point d'entrée : `python -m ldmv [fichiers...]` lance l'interface graphique."""

import sys


def main() -> int:
    from ldmv.ui.main_window import run

    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
