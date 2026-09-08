#!/usr/bin/env bash
# Switch the box between the two SNAGR deployment postures. Run as root
# (systemctl/rfkill/nft all need it). See deploy/pi5-setup.md.
#
#   set-network-mode.sh airgapped
#   set-network-mode.sh lan <this-box's-LAN-IP>
set -euo pipefail

ENV_FILE=/etc/snagr/network-mode.env
NFT_RULES=/opt/snagr/deploy/nftables-lan.conf
MODE="${1:-}"

usage() {
  echo "usage: $0 airgapped" >&2
  echo "       $0 lan <lan-ip>   (e.g. $0 lan 192.168.1.50)" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }

case "$MODE" in
  airgapped)
    echo "==> Writing $ENV_FILE (airgapped)"
    cat > "$ENV_FILE" <<'EOF'
SNAGR_NETWORK_MODE=airgapped
SNAGR_HOST=127.0.0.1
EOF

    echo "==> Blocking Wi-Fi/Bluetooth radios"
    rfkill block wifi bluetooth 2>/dev/null || true

    echo "==> Flushing any LAN firewall rules"
    nft flush ruleset 2>/dev/null || true

    echo "==> Switching services: engine (headless) -> kiosk"
    systemctl disable --now snagr-engine.service 2>/dev/null || true
    systemctl enable --now snagr-kiosk.service
    ;;

  lan)
    HOST="${2:-}"
    [ -n "$HOST" ] || usage
    echo "==> Writing $ENV_FILE (lan, host=$HOST)"
    cat > "$ENV_FILE" <<EOF
SNAGR_NETWORK_MODE=lan
SNAGR_HOST=$HOST
EOF

    echo "==> Applying LAN-only firewall ruleset ($NFT_RULES)"
    echo "    (edit that file's subnet first if you haven't already)"
    nft -f "$NFT_RULES"

    echo "==> Switching services: kiosk -> engine (headless)"
    systemctl disable --now snagr-kiosk.service 2>/dev/null || true
    systemctl enable --now snagr-engine.service
    ;;

  *)
    usage
    ;;
esac

chmod 600 "$ENV_FILE"
chown snagr:snagr "$ENV_FILE" 2>/dev/null || true
echo "==> Done. Network mode: $MODE"
echo "    Check with: systemctl status snagr-kiosk.service snagr-engine.service"
