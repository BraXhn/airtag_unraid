# Installera i Unraid med en Docker-template

Den här metoden använder den färdigbyggda imagen från GitHub Container Registry och kräver inte Docker Compose eller ett lokalt Rust-bygge.

## 1. Kontrollera att imagen är klar

Öppna [GitHub Actions](https://github.com/BraXhn/airtag_unraid/actions) och kontrollera att arbetsflödet **Test, build and publish container** har lyckats. Imagen heter `ghcr.io/braxhn/airtag_unraid:latest`.

Kontrollera även [containerpaketets åtkomst](https://github.com/BraXhn/airtag_unraid/pkgs/container/airtag_unraid). Ett offentligt repo betyder inte automatiskt att containerpaketet går att hämta anonymt. Om paketet är privat behöver du antingen behörig registry-inloggning på servern eller uttryckligen välja offentlig synlighet i paketets inställningar.

## 2. Lägg till templaten

Öppna terminalen i Unraid som root. Kör följande kommandon ett i taget:

```sh
mkdir -p /boot/config/plugins/dockerMan/templates-user
curl --fail --location https://raw.githubusercontent.com/BraXhn/airtag_unraid/main/unraid/my-airtag-map.xml -o /boot/config/plugins/dockerMan/templates-user/my-airtag-map.xml
mkdir -p /mnt/user/appdata/airtag-map
chown 1000:1000 /mnt/user/appdata/airtag-map
chmod 700 /mnt/user/appdata/airtag-map
```

Använd en ny, dedikerad datakatalog. Kommandona ändrar bara katalogens ägare och behörighet, inte befintliga filer rekursivt. Om du redan har en anpassad `my-airtag-map.xml`, säkerhetskopiera den innan du hämtar filen eftersom `curl -o` ersätter den.

## 3. Skapa containern via formuläret

Gå till **Docker → Add Container**. Välj **airtag-map** i listan **Template**. Den kan visas bland användartemplates med ett prefix. Kontrollera:

| Fält | Värde |
| --- | --- |
| Name | `airtag-map` |
| Repository | `ghcr.io/braxhn/airtag_unraid:latest` |
| Network Type | `Custom: br0`, eller ditt eget Custom-nätverk |
| Fixed IP address | Den lediga IP-adress du vill ge containern |
| WebUI port | `8787` |
| Privileged | Av |
| Appdata: Host Path | `/mnt/user/appdata/airtag-map` |
| Appdata: Container Path | `/data` |
| App password / APP_PASSWORD | Skapa ett separat slumpmässigt lösenord, minst 12 tecken |
| DATA_DIR | `/data` |
| ANISETTE_URL | Används endast av den äldre Rust-exportören; standardmetoden använder lokal CoreADI |

Spara applösenordet i din lösenordshanterare. **Skriv inte ditt Apple-lösenord i templaten.**

I Advanced View finns **Extra Parameters**:

```text
--user=1000:1000 --read-only --tmpfs=/tmp:rw,noexec,nosuid,size=128m --cap-drop=ALL --security-opt=no-new-privileges:true --init --restart=unless-stopped
```

Den hårdkodade localhost-mappningen är borttagen. Med ett Custom-nätverk använder appen **containerns egen valda IP-adress** och port **8787** direkt. Port-raden finns för WebUI-information; värdportmappning används inte på detta nätverk.

Ange **Fixed IP address** till en ledig adress i samma subnät som det Custom-nätverk du valt. Använd inte Unraid-serverns egen IP eller en adress som redan används. Välj gärna en adress utanför routerns DHCP-pool eller reservera den där. Om `Custom: br0` saknas, välj det Custom-nätverk du redan har konfigurerat för dina containrar.

Klicka **Apply** och sedan **Done** när skapandet lyckats.

## 4. Öppna appen

Välj **WebUI** för containern, eller öppna `http://DIN-VALDA-IP:8787` i webbläsaren. Ingen SSH-tunnel behövs.

Använd ett betrott nätverk. HTTP krypterar inte lösenord; för krypterad åtkomst använder du HTTPS via en reverse-proxy.

Logga in med användarnamnet `admin` och **applösenordet du valde i templaten**. Inne i appen klickar du **Anslut Apple-konto** och följer dialogen. När exporten lyckats klickar du **Hämta positioner**. Standardmetoden använder nu Linux-emulering utan fysisk Mac; första gången behövs minst 1 GB ledigt appdata för förberedelse av Apple-biblioteket. Se [APPLE-LINUX.md](APPLE-LINUX.md) för SMS, sparad session och felsökning. Tvåfaktorskod och en tidigare enhets skärmlåskod kan krävas och kan inte kringgås.

## Begränsningar

Actions testar byggbarhet och start utan Apple-konto. Det bekräftar inte att ditt Apple-konto kan användas eller att alla dina föremål kan hämtas. Integrationen är inofficiell och kan sluta fungera när Apple ändrar sina tjänster. Se README.md för datalagring och felsökning.

Unraid sparar miljövariabler, inklusive applösenordet, i sin lokala template. Skydda flash-säkerhetskopior och appdata.

Om du tidigare startade appen med Compose, kör `docker compose down` från den ursprungliga projektkatalogen innan du använder denna template. Kör inte båda installationerna samtidigt mot samma datakatalog.

För lokal byggning som alternativ: klona repot och kör `sh prepare-unraid-template.sh`. Det bygger `airtag-map:local` och installerar en anpassad template. Använd endast en av metoderna.

## Ändra en redan installerad container

En ny XML-fil ändrar inte automatiskt en befintlig containers inställningar. Gå till **Docker → airtag-map → Edit**:

1. Visa **Advanced View** och ta bort enbart `--publish=127.0.0.1:8787:8787` från **Extra Parameters** om den finns kvar.
2. Välj **Network Type: Custom: br0** eller ditt eget Custom-nätverk.
3. Ange önskad ledig adress i **Fixed IP address**.
4. Sätt **WebUI** till `http://[IP]:[PORT:8787]/`. Om fältet inte visas kan du öppna den valda IP-adressen manuellt.
5. Behåll appdata och applösenord. Klicka **Apply** och öppna `http://DIN-VALDA-IP:8787`.

Appen lyssnar redan på `0.0.0.0:8787` inne i containern. Ändringen kräver därför ingen ny appversion för att fungera.
