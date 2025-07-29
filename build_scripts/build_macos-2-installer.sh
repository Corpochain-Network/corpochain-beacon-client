#!/bin/bash

set -o errexit -o nounset

git status
git submodule

if [ ! "$CORPOCHAIN_INSTALLER_VERSION" ]; then
	echo "WARNING: No environment variable CORPOCHAIN_INSTALLER_VERSION set. Using 0.0.0."
	CORPOCHAIN_INSTALLER_VERSION="0.0.0"
fi
echo "Corpochain Installer Version is: $CORPOCHAIN_INSTALLER_VERSION"

echo "Installing npm utilities"
cd npm_macos || exit 1
npm ci
PATH=$(npm bin):$PATH
cd .. || exit 1

echo "Create dist/"
sudo rm -rf dist
mkdir dist

echo "Create executables with pyinstaller"
SPEC_FILE=$(python -c 'import corpochain; print(corpochain.PYINSTALLER_SPEC_PATH)')
pyinstaller --log-level=INFO "$SPEC_FILE"
LAST_EXIT_CODE=$?
if [ "$LAST_EXIT_CODE" -ne 0 ]; then
	echo >&2 "pyinstaller failed!"
	exit $LAST_EXIT_CODE
fi
cp -r dist/daemon ../corpochain-gui/packages/gui

# Change to the gui package
cd ../corpochain-gui/packages/gui || exit 1

# sets the version for corpochain-beacon-client in package.json
brew install jq
cp package.json package.json.orig
jq --arg VER "$CORPOCHAIN_INSTALLER_VERSION" '.version=$VER' package.json > temp.json && mv temp.json package.json

echo "Building macOS Electron app"
OPT_ARCH="--x64"
if [ "$(arch)" = "arm64" ]; then
  OPT_ARCH="--arm64"
fi
PRODUCT_NAME="Corpochain Beacon Client"
echo electron-builder build --mac "${OPT_ARCH}" --config.productName="$PRODUCT_NAME"
electron-builder build --mac "${OPT_ARCH}" --config.productName="$PRODUCT_NAME"
LAST_EXIT_CODE=$?
ls -l dist/mac*/corpochain.app/Contents/Resources/app.asar

# reset the package.json to the original
mv package.json.orig package.json

if [ "$LAST_EXIT_CODE" -ne 0 ]; then
	echo >&2 "electron-builder failed!"
	exit $LAST_EXIT_CODE
fi

mv dist/* ../../../build_scripts/dist/
cd ../../../build_scripts || exit 1

mkdir final_installer
DMG_NAME="corpochain-gui-${CORPOCHAIN_INSTALLER_VERSION}.dmg"
if [ "$(arch)" = "arm64" ]; then
  mv dist/"${DMG_NAME}" dist/corpochain-beacon-client-"${CORPOCHAIN_INSTALLER_VERSION}"-arm64.dmg
  DMG_NAME=corpochain-${CORPOCHAIN_INSTALLER_VERSION}-arm64.dmg
fi
mv dist/"$DMG_NAME" final_installer/

ls -lh final_installer
