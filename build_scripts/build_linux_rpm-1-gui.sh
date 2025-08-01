#!/bin/bash

set -o errexit

echo "Installing global npm packages"
cd npm_linux || exit 1
npm ci
PATH=$(npm bin):$PATH

cd ../../ || exit 1
git submodule update --init corpochain-gui

cd ./corpochain-gui || exit 1
echo "npm build"
lerna clean -y
npm ci
# Audit fix does not currently work with Lerna. See https://github.com/lerna/lerna/issues/1663
# npm audit fix
npm run build
LAST_EXIT_CODE=$?
if [ "$LAST_EXIT_CODE" -ne 0 ]; then
	echo >&2 "npm run build failed!"
	exit $LAST_EXIT_CODE
fi

# Remove unused packages
rm -rf node_modules

# Other than `corpochain-gui/package/gui`, all other packages are no longer necessary after build.
# Since these unused packages make cache unnecessarily fat, here unused packages are removed.
echo "Remove unused @Corpochain-Network packages to make cache slim"
ls -l packages
rm -rf packages/api
rm -rf packages/api-react
rm -rf packages/core
rm -rf packages/icons

# Remove unused fat npm modules from the gui package
cd ./packages/gui/node_modules || exit 1
echo "Remove unused node_modules in the gui package to make cache slim more"
rm -rf electron/dist # ~186MB
rm -rf "@mui" # ~71MB
rm -rf typescript # ~63MB

# Remove `packages/gui/node_modules/@Corpochain-Network` because it causes an error on later `electron-packager` command
rm -rf "@Corpochain-Network"
