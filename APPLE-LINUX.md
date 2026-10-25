# Apple-inloggning på Unraid utan Mac

Standardmetoden använder nu macOS CoreADI under Unicorn-emulering på Linux,
baserat på [sacca97/os-findmy-e](https://github.com/sacca97/os-findmy-e), låst
till `374d83854f657a7fdca45f813387d99d491db0e2`, tillsammans med
[parawanderer/FindMy.py](https://github.com/parawanderer/FindMy.py), låst till
`7969003ca785658369b650f75d9e7ca519566bf6`.

Bakgrunden är Apples fel `-80045`: Android-baserad Anisette tillsammans med
en Mac-identitet kan avvisas. En ändring av klientnamnet ensam kan få login
att fungera men ändå sakna `searchPartyToken` för positionshämtningen.
Den nya metoden håller maskin, Anisette och kontosession på samma emulerade
macOS-identitet. Den kräver ingen fysisk Mac eller macOS-VM.

Detta är fortfarande experimentellt. Upstream rapporterar fungerande login,
nyckelhämtning och positioner utan Apple-hårdvara; vi har inte verifierat ditt
konto. Våra tester verifierar lokal kod, byggbarhet, förberedelse av biblioteket
och emulatorstart utan konto. Ett grönt bygge bevisar inte att Apple accepterar
en viss användares inloggning. Se [upstreams resultat](https://github.com/malmeloo/FindMy.py/issues/278).

## Gör så här

1. Uppdatera `ghcr.io/braxhn/airtag_unraid:latest` när det senaste Actions-bygget
   har lyckats. Behåll appdata, applösenord och din valda container-IP.
2. Öppna appen och välj **Anslut Apple-konto**. Första gången hämtar containern
   iTunes 12.8.3 direkt från Apples nedladdningsserver och extraherar endast
   det CoreADI-bibliotek som behövs. Ha minst 1 GB ledigt i appdata. Nedladdningen
   kan ta flera minuter. Biblioteket sparas i `/data/macadi/CoreADI.x86_64` och
   ingår inte i den publicerade Docker-imagen.
3. Ange Apple-konto och Apple-lösenord i appens dolda dialog. En emulerad
   Mac-identitet registreras hos Apple och dess tillstånd sparas lokalt.
4. När tvåfaktor behövs: välj `1` för SMS eller `2` för betrodd Apple-enhet.
   Välj telefonnummer om flera erbjuds och ange verifieringskoden. SMS måste
   erbjudas av Apple för ditt konto; metoden kan inte kringgå verifieringen.
5. Välj en tidigare enhet vars skärmlåskod/lösenord du känner till och ange
   just den enhetens kod. Den fysiska enheten behöver inte vara framför dig
   om Apple fortfarande har en återställningsbar nyckelringspost för den.
   Ett felaktigt kodförsök upprepas inte automatiskt.
6. Appen läser och dekrypterar föremålsnycklarna. Välj vilka föremål som ska
   ingå under **Välj föremål att hämta** och klicka **Spara urval**.
   **Hämta positioner** använder sedan den sparade sessionen och nycklarna.
   **Ny positionsinloggning** loggar in igen på samma sparade maskin.

Första positionssökningen kan ta flera minuter per föremål när biblioteket
behöver hitta rätt rullande nyckelindex. Appen visar nu `[1/16] Föremålsnamn`
och en statusrad var tionde sekund under sökningen. En sådan rad visar att
processen fortfarande körs, inte hur mycket av sökningen som återstår.
Varje färdigt föremål sparas separat, syns på kartan och köas till TCP om den
funktionen är aktiverad. Ett enskilt nätverksfel kastar inte bort de andra
föremålens resultat. Tidigare positioner behålls med sin ursprungliga tid
när en ny rapport saknas eller hämtningen misslyckas. Nästa hämtning använder
sparade nycklar och den nyckeljustering som redan hunnit göras.

Jobbet har fortfarande en total gräns på 15 minuter. En statusrad är ingen
garanti för att Apple kommer att lämna en position. Vid avbrott behålls redan
sparade resultat; välj **Hämta positioner** för att försöka igen utan att
återställa föremålsnycklarna på nytt.

Ingen nyckelringsmedlem ansluts av den nya återställningsvägen. Den läser
återställningsbara poster och föremålsnycklar. Den tar inte bort enheter,
byter inte lösenord och accepterar inte iCloud-villkor åt dig. Om Apple kräver
åtgärder som metoden inte stöder avslutas den med fel.

## Lagring och transport

- `/data/mac-session.enc`: krypterade kontotoken och sessionstillstånd.
  Apple-lösenordet utelämnas från den sparade sessionen. Det hålls i processminnet
  medan nycklarna återställs. Vid förnyad nyckelåterställning kan det behöva anges igen.
- `/data/mac-accessories.enc`: krypterade föremålsnycklar.
- `/data/macadi/machine/`: maskinidentitet och CoreADI-tillstånd, skyddat med
  Unix-behörigheter men inte krypterat av appen. Dessa är också känsliga filer.
- `/data/macadi/gsa-429`: beständig väntetid efter en HTTP 429. Ingen automatisk
  inloggningsloop körs; manuella och automatiska sökningar respekterar väntetiden på 45 minuter.
- Krypteringen använder det befintliga applösenordet. Ett byte av `APP_PASSWORD`
  gör sparade sessioner och föremålsnycklar oläsbara; logga då in och hämta
  föremålen på nytt. Minimikravet är 12 tecken.
- Den lokala patchen `patch_macadi.py` slår på certifikat- och värdnamnskontroll
  för emulatorns Apple-anrop, med Apples CA-rötter, och flyttar spärrmarkören
  till beständig appdata. Okänd källkod stoppas vid byggning i stället för att
  patchas på chans. Apples bibliotek kontrolleras med låst SHA-256.
- Kontouppgifter ska bara anges i din egen app, aldrig skickas till ChatGPT.

Gamla filer från Rust-exportören (`/data/export/`, `/data/session.enc`) behålls.
Den nya metoden använder separata filer och återanvänder inte den gamla
Android-baserade sessionen. `ANISETTE_URL` används inte av standardmetoden.
Den gamla exportören finns kvar för kompatibilitet via `FINDMY_BACKEND=legacy`,
men den är inte en lösning på `-80045` och rekommenderas inte för nya sessioner.

## Kartbilder blockerade

Val av föremål finns under [DEVICE-SELECTION.md](DEVICE-SELECTION.md).
Automatisk positionssökning och information om sessionens livslängd finns
under [AUTOMATIC-SEARCH.md](AUTOMATIC-SEARCH.md). Automatiken använder den
sparade sessionen och pausas när manuell inloggning behövs.

Felbilden **Access blocked** från OpenStreetMap gäller kartbilderna och är ett
separat problem. Appens tidigare `Referrer-Policy: no-referrer` stred mot
[OpenStreetMaps policy](https://operations.osmfoundation.org/policies/tiles/).
Nu skickar webbläsaren endast appens ursprung (protokoll, värdnamn/IP och port)
som Referer. Sidans sökväg, frågeparametrar, Apple-uppgifter och HTTP Basic-
inloggning skickas inte till kartleverantören. Kartan använder vanlig
webbläsarcache och hämtar bara kartbilder för den synliga kartvyn.

Ladda om sidan efter uppdateringen. Om en gammal blockeringsbild är cachad
kan du göra en hård omladdning en gång. En proxy eller ett integritetstillägg
kan fortfarande ta bort Referer, och en kvarstående IP-blockering hos
karttjänsten kan inte hävas av appen.
