#!/usr/bin/env bash
# Post install script for the UI .deb to place symlinks in places to allow the CLI to work similarly in both versions

set -e

ln -s /opt/corpochain-beacon-client/resources/app.asar.unpacked/daemon/corpochain /usr/bin/corpochain || true
ln -s /opt/corpochain-beacon-client/corpochain-gui /usr/bin/corpochain-gui || true
