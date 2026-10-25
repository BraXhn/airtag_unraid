#!/bin/sh
# Run as root on the Unraid server. Does not create or start a container.
set -eu
umask 077
cd "$(dirname "$0")"
if ! command -v docker >/dev/null 2>&1; then
  echo 'Docker saknas. Kor skriptet pa Unraid-servern med Docker aktiverat.' >&2
  exit 1
fi
if [ ! -d /boot/config/plugins/dockerMan ]; then
  echo 'Unraids Docker-konfiguration hittades inte. Ingen template har installerats.' >&2
  exit 1
fi
sh setup.sh
app_password=$(sed -n 's/^APP_PASSWORD=//p' .env)
case "$app_password" in
  ''|*[!0-9a-f]*) echo 'APP_PASSWORD i .env maste vara det genererade hex-losenordet. Befintlig fil har behallits.' >&2; exit 1 ;;
esac
if [ "${#app_password}" -ne 64 ]; then
  echo 'APP_PASSWORD ska innehalla 64 hex-tecken fran setup.sh. Befintlig fil har behallits.' >&2
  exit 1
fi
docker build -t airtag-map:local .
template_dir=/boot/config/plugins/dockerMan/templates-user
mkdir -p "$template_dir"
template_path="$template_dir/my-airtag-map.xml"
if [ -e "$template_path" ]; then
  echo 'my-airtag-map.xml finns redan. Den har behallits; anvand befintlig template.'
else
  sed -e 's#ghcr.io/braxhn/airtag_unraid:latest#airtag-map:local#' \
      -e "s#Mask=\"true\"></Config>#Mask=\"true\">$app_password</Config>#" \
      unraid/my-airtag-map.xml > "$template_path"
  chmod 600 "$template_path"
  echo 'Template installerad. Oppna Docker > Add Container och valj airtag-map i Template-listan.'
fi
echo 'Ingen container har startats av skriptet. Skapa den med Apply i Unraid.'
