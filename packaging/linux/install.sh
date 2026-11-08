#!/usr/bin/env bash
# Instalace NetMonitoru pro aktuálního uživatele (bez sudo):
#   ./install.sh            -> ~/.local/opt/netmonitor + menu aplikací
#   ./install.sh --uninstall
set -euo pipefail

find_ldconfig() {
  local candidate
  if command -v ldconfig; then
    return 0
  fi
  for candidate in /sbin/ldconfig /usr/sbin/ldconfig; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/opt/netmonitor"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"
LEGACY_ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/netmonitor.png"
if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$DEST" "$APPS/netmonitor.desktop" "$ICONS/netmonitor.png" "$LEGACY_ICON" "$HOME/.local/bin/netmonitor"
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/netmonitor.desktop"
  echo "Odinstalováno (konfigurace v ~/.config/netmonitor a data v ~/.local/share/netmonitor ponechána)."
  exit 0
fi

# Starší/neúplný bundle lze nainstalovat, ale chybějící Qt xcb závislosti ohlásíme.
LIBRARIES=(libxcb-cursor.so.0 libxkbcommon-x11.so.0)
PACKAGES=(libxcb-cursor0 libxkbcommon-x11-0)
MISSING=()
LDCONFIG_CACHE=""
# Nedostupná cache nesmí blokovat instalaci; knihovny pak ověří jen bundle.
if ! LDCONFIG=$(find_ldconfig) || ! LDCONFIG_CACHE=$("$LDCONFIG" -p); then
  echo "Varování: nelze přečíst cache systémových knihoven (ldconfig -p)." >&2
  LDCONFIG_CACHE=""
fi
for i in "${!LIBRARIES[@]}"; do
  if [ ! -f "$SRC/_internal/${LIBRARIES[$i]}" ] &&
     ! awk -v lib="${LIBRARIES[$i]}" '$1 == lib && /x86-64/ { found=1 } END { exit !found }' <<< "$LDCONFIG_CACHE"; then
    echo "Varování: v bundlu ani v systémové cache není ${LIBRARIES[$i]}." >&2
    MISSING+=("${PACKAGES[$i]}")
  fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "Pro spuštění Qt xcb doinstalujte: sudo apt install ${MISSING[*]}" >&2
fi

mkdir -p "$DEST" "$APPS" "$ICONS" "$HOME/.local/bin"
cp -r "$SRC"/* "$DEST"/
rm -f "$LEGACY_ICON"
cp "$SRC/netmonitor.png" "$ICONS/netmonitor.png"
sed "s#^Exec=.*#Exec=$DEST/NetMonitor#" "$SRC/netmonitor.desktop" > "$APPS/netmonitor.desktop"
ln -sf "$DEST/NetMonitor" "$HOME/.local/bin/netmonitor"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
echo "Nainstalováno do $DEST. Spusťte z menu aplikací nebo příkazem: netmonitor"
echo "Tip: nepřivilegovaný ICMP – sudo sysctl -w net.ipv4.ping_group_range='0 2147483647' (jinak se použije systémový ping)."
