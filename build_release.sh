#!/bin/bash
# Build a QGIS plugin release ZIP with proper root folder structure.
# Usage: ./build_release.sh [VERSION]
# Default version is read from metadata.txt.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$SCRIPT_DIR"

VERSION="${1:-$(grep '^version=' "$PLUGIN_ROOT/metadata.txt" | cut -d= -f2)}"
ZIP_NAME="AgenticGIS-v${VERSION}.zip"
BUILD_DIR="/tmp/agenticgis-release-$$"

echo "Building AgenticGIS v${VERSION}..."

# Clean and create build dir
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/AgenticGIS"

# Copy ALL required files (root-level .py files are critical!)
cp "$PLUGIN_ROOT/__init__.py" "$BUILD_DIR/AgenticGIS/"
cp "$PLUGIN_ROOT/plugin.py" "$BUILD_DIR/AgenticGIS/"      # REQUIRED for classFactory
cp "$PLUGIN_ROOT/config.py" "$BUILD_DIR/AgenticGIS/"      # REQUIRED by plugin.py
cp "$PLUGIN_ROOT/metadata.txt" "$BUILD_DIR/AgenticGIS/"
cp "$PLUGIN_ROOT/LICENSE" "$BUILD_DIR/AgenticGIS/"
cp "$PLUGIN_ROOT/README.md" "$BUILD_DIR/AgenticGIS/"

# Copy directories
cp -r "$PLUGIN_ROOT/core" "$BUILD_DIR/AgenticGIS/"
cp -r "$PLUGIN_ROOT/gui" "$BUILD_DIR/AgenticGIS/"
cp -r "$PLUGIN_ROOT/backends" "$BUILD_DIR/AgenticGIS/"
cp -r "$PLUGIN_ROOT/resources" "$BUILD_DIR/AgenticGIS/"

# Optional directories (may not exist)
cp -r "$PLUGIN_ROOT/i18n" "$BUILD_DIR/AgenticGIS/" 2>/dev/null || true
cp -r "$PLUGIN_ROOT/utils" "$BUILD_DIR/AgenticGIS/" 2>/dev/null || true
cp -r "$PLUGIN_ROOT/server" "$BUILD_DIR/AgenticGIS/" 2>/dev/null || true

# Verify critical files
for f in __init__.py plugin.py config.py metadata.txt; do
    if [ ! -f "$BUILD_DIR/AgenticGIS/$f" ]; then
        echo "ERROR: Missing critical file: $f" >&2
        exit 1
    fi
done

# Create ZIP with AgenticGIS/ folder at root
cd "$BUILD_DIR"
zip -r "$ZIP_NAME" AgenticGIS/

# Move to project root
mv "$ZIP_NAME" "$PLUGIN_ROOT/"

# Cleanup
rm -rf "$BUILD_DIR"

echo ""
echo "✅ Release ZIP built: $PLUGIN_ROOT/$ZIP_NAME"
echo ""
echo "Contents:"
unzip -l "$PLUGIN_ROOT/$ZIP_NAME" | head -20
