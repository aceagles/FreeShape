#!/bin/bash
# Builds dist/freeshape_<rev>_amd64.deb on Linux from the packed app that build_appimage.sh makes.
#   bash packaging/linux/build_appimage.sh && bash packaging/linux/build_deb.sh
# The package installs to /opt/freeshape with a launcher in /usr/bin and a menu entry. It runs on Debian and
# Ubuntu systems at least as new as the one it was built on (glibc).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
REV="$("$PY" -c 'import freeshape; print(freeshape.__version__)')"
# FREESHAPE_DEB_VERSION lets a release build say which release it is (2.0.0-fork.1 becomes 2.0.0+fork.1)
VER="${FREESHAPE_DEB_VERSION:-$REV}"
SRC=build/linux-dist/FreeShape
[ -x "$SRC/FreeShape" ] || { echo "[FreeShape] $SRC is missing: run packaging/linux/build_appimage.sh first." >&2; exit 1; }

PKG=build/deb/freeshape_${VER}_amd64
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/256x256/apps" "$PKG/usr/share/doc/freeshape"
cp -a "$SRC" "$PKG/opt/freeshape"
# as in the AppImage: use the system's graphics stack and C++ runtime, never bundled copies
find "$PKG/opt/freeshape" \( -name 'libGL.so*' -o -name 'libGLX*.so*' -o -name 'libEGL.so*' \
     -o -name 'libGLdispatch.so*' -o -name 'libdrm.so*' -o -name 'libgbm.so*' -o -name 'libxcb.so*' \
     -o -name 'libX11.so*' -o -name 'libwayland-client.so*' -o -name 'libstdc++.so*' -o -name 'libgcc_s.so*' \) -delete
cat > "$PKG/usr/bin/freeshape" <<'LAUNCH'
#!/bin/sh
exec /opt/freeshape/FreeShape "$@"
LAUNCH
chmod 755 "$PKG/usr/bin/freeshape"
sed 's|^Exec=.*|Exec=freeshape %F|' packaging/linux/freeshape.desktop > "$PKG/usr/share/applications/freeshape.desktop"
cp freeshape/assets/logo_256.png "$PKG/usr/share/icons/hicolor/256x256/apps/freeshape.png"
cp LICENSE "$PKG/usr/share/doc/freeshape/copyright"
cp THIRD-PARTY-NOTICES.txt "$PKG/usr/share/doc/freeshape/"

SIZE=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: freeshape
Version: $VER
Section: graphics
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Depends: libegl1, libgl1, libopengl0, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libfontconfig1, libdbus-1-3
Recommends: git
Maintainer: Adam Eagles <aceagles@users.noreply.github.com>
Homepage: https://github.com/${GITHUB_REPOSITORY:-aceagles/FreeShape}
Description: Onshape-style parametric CAD that runs on your own computer
 Parts, assemblies, 2D drawings and sheet metal on the OpenCascade kernel,
 with Documents under git, Derived parts, Variable Studios and standard parts.
CONTROL

mkdir -p dist
OUT="dist/freeshape_${VER}_amd64.deb"
rm -f "$OUT"
dpkg-deb --build --root-owner-group "$PKG" "$OUT"
echo "[FreeShape] Done: $OUT"
