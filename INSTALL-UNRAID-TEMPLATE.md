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
| Network Type | `bridge` |
| Privileged | Av |
| Appdata: Host Path | `/mnt/user/appdata/airtag-map` |
| Appdata: Container Path | `/data` |
| App password / APP_PASSWORD | Skapa ett separat slumpmässigt lösenord, minst 24 tecken |
| DATA_DIR | `/data` |
| ANISETTE_URL | `https://ani.sidestore.io` |

Spara applösenordet i din lösenordshanterare. **Skriv inte ditt Apple-lösenord i templaten.**

I Advanced View finns **Extra Parameters**:

```text
--publish=127.0.0.1:8787:8787 --user=1000:1000 --read-only --tmpfs=/tmp:rw,noexec,nosuid,size=128m --cap-drop=ALL --security-opt=no-new-privileges:true --init --restart=unless-stopped
```

Portmappningen ligger här för att binda porten till serverns loopback-adress. Lägg inte till en separat Port-rad för 8787. Klicka **Apply** och sedan **Done** när skapandet lyckats.

## 4. Öppna appen

På din egen dator, öppna PowerShell eller Terminal:

```sh
ssh -N -L 8787:127.0.0.1:8787 root@DIN-UNRAID-IP
```

Ersätt `DIN-UNRAID-IP`, ange Unraid-lösenordet och låt fönstret vara öppet. SSH måste vara tillgängligt på servern. Öppna sedan [http://localhost:8787](http://localhost:8787) på samma dator. WebUI-länken förutsätter att tunneln redan är igång. En HTTPS-reverse-proxy är ett alternativ för den som redan har en sådan konfigurerad.

Logga in med användarnamnet `admin` och **applösenordet du valde i templaten**. Inne i appen klickar du **Anslut Apple-konto** och följer dialogen. När exporten lyckats klickar du **Hämta positioner**. Två separata Apple-inloggningar kan behövas. Tvåfaktorskod och en tidigare enhets skärmlåskod kan krävas och kan inte kringgås.

## Begränsningar

Actions testar byggbarhet och start utan Apple-konto. Det bekräftar inte att ditt Apple-konto kan användas eller att alla dina föremål kan hämtas. Integrationen är inofficiell och kan sluta fungera när Apple ändrar sina tjänster. Se README.md för datalagring och felsökning.

Unraid sparar miljövariabler, inklusive applösenordet, i sin lokala template. Skydda flash-säkerhetskopior och appdata.

Om du tidigare startade appen med Compose, kör `docker compose down` från den ursprungliga projektkatalogen innan du använder denna template. Kör inte båda installationerna samtidigt mot samma datakatalog.

För lokal byggning som alternativ: klona repot och kör `sh prepare-unraid-template.sh`. Det bygger `airtag-map:local` och installerar en anpassad template. Använd endast en av metoderna.
