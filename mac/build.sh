#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Compile a self-contained universal binary; no Xcode project needed.
# -Osize keeps the binary modest (~600 KB) since this is a long-running tray app.
swiftc -O -target arm64-apple-macos13 \
    -parse-as-library \
    -framework Cocoa \
    -o worknow-mac \
    main.swift

echo "Built $DIR/worknow-mac"
