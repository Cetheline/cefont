#!/bin/sh
# Run from any directory. The backend keeps installation separate from CeFont.
set -eu
installer_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${CEFONT_INSTALL_PYTHON:-python3}" -I -B "$installer_root/scripts/install_cefont.py" "$@"
