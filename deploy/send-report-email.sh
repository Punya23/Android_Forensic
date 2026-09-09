#!/usr/bin/env bash
# One-shot, deliberate, outbound-only email of a single case report.
# Not run automatically by anything — invoke by hand, then re-lock the box
# with `set-network-mode.sh airgapped` when you're done.
#
# Sends directly to your own mail provider over TLS via msmtp — no relay, no
# third-party mail service, nothing else in the middle. Configure msmtp with
# your own account first (~/.msmtprc for the `snagr` user, account name
# "snagr", chmod 600), see https://marlam.de/msmtp/documentation/ for the
# few lines that needs.
#
# Usage: send-report-email.sh <CASE_ID> <to-address>
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "must run as root (needs rfkill)" >&2; exit 1; }

CASE_ID="${1:-}"
TO="${2:-}"
[ -n "$CASE_ID" ] && [ -n "$TO" ] || {
  echo "usage: $0 <CASE_ID> <to-address>" >&2
  exit 1
}

REPORT="/opt/snagr/cases/${CASE_ID}/report.html"
[ -f "$REPORT" ] || {
  echo "no report.html for case $CASE_ID — generate it in the dashboard first" >&2
  exit 1
}
command -v msmtp >/dev/null || {
  echo "msmtp not installed — sudo apt install msmtp, then configure ~/.msmtprc for user snagr" >&2
  exit 1
}

echo "==> Bringing Wi-Fi up for one outbound send"
rfkill unblock wifi
sleep 3   # let the interface actually associate before we try to send

echo "==> Sending $REPORT to $TO"
{
  echo "To: $TO"
  echo "Subject: SNAGR report - ${CASE_ID}"
  echo "MIME-Version: 1.0"
  echo "Content-Type: text/html; charset=UTF-8"
  echo
  cat "$REPORT"
} | runuser -u snagr -- msmtp -a snagr "$TO"

echo "==> Sent. Re-blocking Wi-Fi."
rfkill block wifi
echo "==> Done. Run set-network-mode.sh airgapped if you also switched network-mode.env for this."
