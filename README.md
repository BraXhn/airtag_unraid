# AirTag-karta för Unraid

**Installera via Unraids Docker-formulär:** följ [installationsguiden](INSTALL-UNRAID-TEMPLATE.md). Den använder `ghcr.io/braxhn/airtag_unraid:latest` och kräver inte Docker Compose. [GitHub Actions](https://github.com/BraXhn/airtag_unraid/actions) bygger och kontrollerar imagen. Compose-metoden nedan är ett separat alternativ; använd endast en metod för samma datakatalog.

Experimentell webbapp i en Linux-container för dina egna AirTags och kompatibla Hitta-föremål. Den kombinerar export av tillbehörsnycklar från iCloud med hämtning av krypterade positionsrapporter. Kartan visar senast rapporterad position och tidsstämpel. Inga exempelpositioner används.

**Status:** Appens HTTP-skydd, interaktiva dialog, sessionskryptering och datamappning är lokalt testade. Actions kontrollerar Docker-bygget och appstart innan imagen publiceras; se senaste körningen för resultat. Inloggning mot ett riktigt Apple-konto är inte verifierad. Detta är en experimentell integration, inte bekräftad AirTag-åtkomst för ditt konto.

## Vad du behöver

- Unraid på x86-64 med Docker. Docker Compose behövs bara om du väljer Compose-metoden nedan; template-installationen kräver det inte.
- Internet från servern för byggberoenden, Apple och Anisette; webbläsaren hämtar kartbilder från OpenStreetMap.
- Apple-konto och lösenord, tillgång till en tillåten tvåfaktorsmetod och ofta skärmlåskoden/lösenordet till en tidigare betrodd enhet. Den gamla telefonen behöver inte vara tillgänglig om kontots återställning/verifiering ändå kan genomföras.
- Hitta och iCloud-nyckelring måste sedan tidigare innehålla användbara uppgifter för dina föremål. Alla kontokonfigurationer stöds inte.

Appen kan inte hoppa över tvåfaktorsverifiering eller återskapa saknade dekrypteringsnycklar. SMS finns implementerat i exportverktyget men beskrivs av dess författare som ännu inte fullständigt testat. Konton med säkerhetsnycklar eller andra inloggningsbegränsningar kan sakna en metod som verktygen stöder. Försvaga inte kontoskyddet för att använda appen.

## Installera på Unraid

Packa upp hela katalogen på servern, exempelvis `/mnt/user/appdata/airtag-map-source`. Kör i Unraids terminal:

```sh
cd /mnt/user/appdata/airtag-map-source
sh setup.sh
# Lägg till BIND_IP=DIN-SERVER-IP i .env innan du startar.
docker compose up -d --build
```

Rust-bygget kan ta lång tid första gången. Det hämtar källkod från de angivna GitHub-projekten. Huvudprojekten är låsta till granskade commit-id:n, men Rust-projektets underberoenden följer fortfarande dess upstream-definitioner; bygget är inte fullständigt reproducerbart.

`setup.sh` skapar ett slumpmässigt separat applösenord i `.env` och en datakatalog med ägare UID/GID 1000:1000. Skriptet ändrar endast själva datakatalogens ägare och läge, inte filer rekursivt. Kör skriptet som root i Unraids terminal. Använd en tom, dedikerad datakatalog vid första installationen.

## Öppna appen

Med Unraid-templaten väljer du **Network Type: Custom: br0** (eller ditt eget Custom-nätverk) och anger önskad ledig adress i **Fixed IP address**. Öppna sedan `http://DIN-VALDA-IP:8787` eller välj WebUI på containern. Ingen SSH-tunnel behövs.

Logga in med `admin` och det separata applösenord du angav i templaten. Apple-inloggningen anges först inne i appen.

Om du använder Compose-metoden anger du även `BIND_IP=DIN-SERVER-IP` i `.env` före `docker compose up`. Det måste vara en IP som redan finns på servern. Compose binder värdporten till den adressen; det skapar inte en egen container-IP som Unraids Custom-nätverk gör. Lösenordet för Compose finns efter `APP_PASSWORD=` i `.env`.

Använd ett betrott nätverk och HTTPS via din reverse-proxy om du behöver kryptera anslutningen. HTTP krypterar inte inloggningsuppgifterna.

## Hämta föremål och positioner

1. Klicka **Anslut Apple-konto**. Läs den sista frågan i dialogen och skriv svaret i det dolda svarsfältet. Alla svar är dolda, även kontonamn och menyval. Tomt svar och Skicka motsvarar Enter.
2. Exportverktyget frågar efter Apple-konto, lösenord, verifieringsmetod och kod. Välj SMS om det erbjuds och det är den metod du kan använda. Därefter kan det fråga vilken tidigare enhet som ska användas för återställning och efter just den enhetens skärmlåskod/lösenord. Följ dialogen; ingen verifiering kringgås.
3. Vid frågan om exporterprofilens nya escrow-lösenord väljer du normalt att generera ett slumpmässigt lösenord. Det är ett separat lokalt profilvärde, inte ditt Apple-lösenord. Exporten kan lägga till en syntetisk betrodd identitet i iCloud-nyckelring; det är en del av tredjepartsverktygets arbetsmetod.
4. När exporten avslutats med kod 0 klickar du **Hämta positioner**. Använd samma Apple-konto. Den separata Python-klienten kan kräva ännu en inloggning och tvåfaktorskod; detta är inte en enda sömlös inloggning i den här första versionen.
5. Kartan fylls först när verkliga positionsrapporter finns. Klicka på ett föremål för att centrera kartan. Tidpunkten är när positionen rapporterades, medan ”Hämtat” är appens hämtningstid. Positioner äldre än en timme markeras med annan textfärg.
6. Klicka **Hämta positioner** när du vill uppdatera. Ingen kontinuerlig bakgrundsspårning eller historikinsamling är aktiverad. Svarsdialogen hämtas varannan sekund; det innebär inte att Apple tillfrågas varannan sekund.

Det här är en vy över exporterbara Hitta-föremål. Den ersätter inte Apples fullständiga enhetslista och inkluderar inte funktioner som fjärradering, fjärrlåsning eller en generell lista över alla inloggade enheter.

## Data och åtkomst

- Alla Apple-uppgifter matas in i din egen server via den skyddade anslutningen. De ska inte skickas till ChatGPT.
- Svarsfältet skickas till en isolerad, fördefinierad processdialog; appen erbjuder ingen generell kommandoterminal. Svar ekas inte och dialogutdata sparas inte till fil av webbappen. Råa exporterutdata från tredje part visas för dig i appen; dela inte dessa utdata om de innehåller kontouppgifter.
- `/mnt/user/appdata/airtag-map` innehåller AirTag-nycklar, betrodd nyckelringsidentitet, återanvändbara autentiseringstoken och senast hämtade positioner. Exportverktygets cache och nyckelfiler lagras **inte krypterat av denna app**, men katalog/filer har begränsade Unix-behörigheter. Skydda även Unraid-administratörsåtkomst och säkerhetskopior.
- Python-klientens sparade session krypteras med en nyckel härledd från det slumpmässiga `APP_PASSWORD`. Upstream-sessionen kan innehålla lösenord eller andra återanvändbara inloggningsuppgifter; kryptering innebär inte att dessa saknas. Den som har både `.env` och datakatalogen kan dekryptera sessionen. Ett byte av `APP_PASSWORD` gör den gamla sessionen oläsbar; använd då **Ny positionsinloggning**.
- Exportören använder normalt Anisette-tjänsten `https://ani.sidestore.io`. Det är en tredjepartstjänst för autentiseringsmetadata. Ange en egen kompatibel v3-tjänst via `ANISETTE_URL` i `.env` om du har en. Python-klienten använder sin lokala Anisette-generator, som hämtar nödvändiga bibliotek första gången.
- Webbläsaren hämtar kartbilder från OpenStreetMap. Kartleverantören får webbläsarens IP och efterfrågade kartområden. Kontolösenord och AirTag-nycklar skickas inte till kartleverantören av appen.
- En process kan köras åt gången. Dialogen avslutas efter högst 15 minuter. Kör endast en Gunicorn-worker; tillståndet för dialogen ligger i den processen.

## Felsökning

```sh
docker compose ps
docker compose logs --tail=60
```

- Om Docker-bygget misslyckas är Apple-inloggning ännu inte provad. Rapportera byggfelet utan hemligheter. Inga bygg- eller Apple-fel får tolkas som att dina föremål saknas.
- Om exporten misslyckas: kontrollera den sista frågan, verifieringsmetod och om kontot har en återställningsbar nyckelring. Ett konto utan tillgänglig verifiering kan inte räddas av appen.
- Om positionshämtning misslyckas: kontrollera internet, försök **Ny positionsinloggning** och behåll samma Apple-konto. Den gamla kartan ligger kvar med sin gamla tidsstämpel.
- Om kartbakgrunden saknas: kontrollera åtkomst till `tile.openstreetmap.org`. Positionsdata kan fortfarande visas i listan.
- Den första positionssökningen kan ta flera minuter eftersom nycklarnas tidsjustering måste beräknas. Om den når 15-minutersgränsen är detta ett integrationsproblem att undersöka, inte bevis för att föremålet är offline.

## Stoppa och avinstallera

`docker compose down` stoppar appen och behåller data. Att ta bort containern återkallar inte exporteridentiteten hos Apple. Vid full avveckling behöver syntetiska betrodda exporterposter hanteras enligt exportverktygets instruktioner. Radera aldrig poster för riktiga Apple-enheter. Säkerställ att du inte längre behöver exporteridentiteten innan du tar bort lokal profil och datakatalog.

## Utveckling och tester

Python 3.12:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q
```

Python-testerna använder lokala processer och simulerade kontosvar. Actions bygger dessutom Rust-verktyget och Docker-imagen och provar appens start utan Apple-konto. Varken testerna eller byggflödet verifierar Apple-inloggning, SMS, Anisette eller faktisk AirTag-åtkomst för ditt konto.

## Källor

- Exportverktyg: https://github.com/stek29/export-findmy — commit `eb5a3a93116478e1c088dc90fa84e1bb30ee232e`
- Positionsklient: https://github.com/malmeloo/FindMy.py — commit `22e4879426f97c9b0a1a33b22dac34ef0cf3d02f`
- Bakgrund till nyckelringsexport utan Mac: https://github.com/malmeloo/FindMy.py/issues/173
- Leaflet 1.9.4: https://leafletjs.com — BSD-2-Clause; licens i `static/LEAFLET-LICENSE`.

Apple tillhandahåller inget officiellt stöd för denna integration. Förändringar på Apples sida kan stoppa den.
## Vidarebefordra till egen spårningsserver

Appen stöder Teltonika Codec 8 och Codec 8E över TCP med redigerbart virtuellt
15-siffrigt ID per AirTag, beständig kö, kvittenser och återförsök. Ställ in
serveradress, port och protokoll i webbgränssnittet och aktivera sändningen.
Apple-positioner hämtas fortfarande manuellt med **Hämta positioner**.
Se [TCP-SERVER.md](TCP-SERVER.md) för installation och mottagarens protokollkrav.

