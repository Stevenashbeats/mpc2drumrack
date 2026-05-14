#!/usr/bin/env bash
# Buduje samodzielną aplikację macOS MPC2DrumRack.app
# Wymaga: pip3 install pyinstaller pygame (i tkinterdnd2 jeśli chcesz drag-and-drop)

set -e

PYTHON="${PYTHON:-/opt/homebrew/bin/python3.11}"
NAME="MPC2DrumRack"

cd "$(dirname "$0")"

rm -rf build dist "$NAME.spec"

"$PYTHON" -m PyInstaller \
    --windowed \
    --name "$NAME" \
    --icon app.icns \
    --add-data "templates:templates" \
    --collect-all pygame \
    --osx-bundle-identifier "com.kacpernowak.mpc2drumrack" \
    --noconfirm \
    mpc2drumrack.py

echo
echo "Gotowe: dist/$NAME.app"
echo "Uruchom: open dist/$NAME.app"
echo
echo "Aby zainstalować: przenieś dist/$NAME.app do /Applications"
