#!/bin/bash
# Double-cliquez sur ce fichier (Mac) ou lancez-le dans un terminal (Linux).
cd "$(dirname "$0")" || exit 1

if [ ! -f .venv/installe.ok ]; then
  echo
  echo "Première utilisation : installation de LDMV, cela prend quelques minutes..."
  echo
  if python3 -m venv .venv \
     && .venv/bin/python -m pip install --upgrade pip \
     && .venv/bin/python -m pip install -e ".[ui]"; then
    touch .venv/installe.ok
  else
    echo
    echo "ERREUR pendant l'installation. Vérifiez que Python 3 est installé (python.org)."
    read -r -p "Appuyez sur Entrée pour fermer."
    exit 1
  fi
fi

echo "Lancement de LDMV..."
.venv/bin/python -m ldmv "$@" || read -r -p "Appuyez sur Entrée pour fermer."
