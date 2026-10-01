#!/usr/bin/env bash
# Undo the NVIDIA/CUDA driver stack that Ollama's install.sh adds when it detects an NVIDIA GPU.
# On machines whose GPU is too old for the current driver (e.g. Fermi-era laptop GPUs), udev
# retries `modprobe nvidia` over and over. That burns CPU, overheats the chip, and triggers
# thermal throttling. Symptom: "nvidia-modprobe failed" spamming `journalctl`.
# Run as: sudo bash server-remove-nvidia.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

temps() { for z in /sys/class/thermal/thermal_zone*; do printf '%s=%sC ' "$(cat "$z/type")" "$(( $(cat "$z/temp") / 1000 ))"; done; echo; }
failures() { journalctl --since "-${1:-60} s" --no-pager 2>/dev/null | grep -c "nvidia-modprobe' failed" || true; }

echo "== Before: $(temps)  modprobe failures in last 60 s: $(failures 60)"

echo "== Purging NVIDIA/CUDA packages"
mapfile -t PKGS < <(dpkg-query -W -f='${Package} ${db:Status-Status}\n' \
  | awk '$2 == "installed" && $1 ~ /^(cuda-drivers|cuda-keyring|nvidia-|libnvidia-|libxnvctrl|xserver-xorg-video-nvidia)/ {print $1}')
if (( ${#PKGS[@]} )); then
  printf '   %s\n' "${PKGS[@]}"
  apt-get purge -y "${PKGS[@]}"
else
  echo "   none installed"
fi

echo "== Removing the CUDA apt repo"
rm -fv /etc/apt/sources.list.d/cuda-ubuntu2404-x86_64.list

echo "== Pinning so a future Ollama install can't pull the driver back in"
cat > /etc/apt/preferences.d/no-nvidia-cuda <<'EOF'
# This machine's GPU is unsupported by current NVIDIA drivers. Added by doc-gym ops/server-remove-nvidia.sh.
Package: cuda-drivers nvidia-* libnvidia-*
Pin: release *
Pin-Priority: -1
EOF

udevadm control --reload
apt-get update -qq

echo
echo "== Orphaned dependencies (X server, GTK, compilers, etc.)"
echo "   apt lists them below and asks before removing anything."
apt-get autoremove --purge

sleep 20
echo
echo "== After: $(temps)  modprobe failures/min: $(failures)"
