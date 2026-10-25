#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  secret=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  printf 'APP_PASSWORD=%s\n' "$secret" > .env
  chmod 600 .env
  echo 'Ett separat applösenord har skapats i .env. Använd det med användarnamnet admin.'
else
  echo '.env finns redan och har behållits.'
fi
mkdir -p /mnt/user/appdata/airtag-map
chown 1000:1000 /mnt/user/appdata/airtag-map
chmod 700 /mnt/user/appdata/airtag-map
echo 'Starta med: docker compose up -d --build'
