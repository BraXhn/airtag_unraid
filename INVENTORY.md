# När nya AirTags saknas i listan

Tidigare läste **Hämta positioner** bara de föremål som redan importerats till
appkontot. Nyregistrerade AirTags upptäcktes inte av positionssökningen. En
tidigare import av exempelvis 20 föremål kunde därför ligga kvar på 20 även när
fler hade lagts till i Hitta. Det fanns ingen avsiktlig gräns på 20 i appen.

## Uppdatera en befintlig installation

1. Uppdatera Unraid-containern och behåll appdata och applösenord.
2. Logga in på det appkonto vars Apple-föremål ska uppdateras.
3. Öppna **Välj föremål att hämta → Uppdatera föremålslista**.
4. Första gången efter uppdateringen behövs samma läsning av iCloud-nyckelringen
   som vid den ursprungliga importen: Apple-lösenord och skärmlåskod/lösenord
   för den tidigare betrodda enhet du väljer. Detta gäller per appkonto.
5. Kontrollera importantalet och välj dina föremål. **Alla föremål, även
   nytillkomna** inkluderar nya föremål vid nästa positionssökning. Ett eget
   urval utökas inte automatiskt; nya föremål visas så att du kan välja dem.

Efter förberedelsen kontrollerar manuella positionssökningar listan mot Apple
innan positionerna hämtas. Automatisk sökning kontrollerar listan högst en gång
per timme för att begränsa extra Apple-anrop. Knappen **Uppdatera föremålslista**
kontrollerar alltid listan och startar ingen positionssökning.

**Anslut Apple-konto** finns kvar och gör en ny manuell nyckelringsläsning,
exempelvis om Apple har bytt zonnycklar. Automatiken frågar aldrig efter lösenord
eller enhetskod. Listan och positionssökningen använder fortfarande den sparade
Apple-sessionen; om den har gått ut behövs ny Apple-inloggning.

## Vad räknarna betyder

Dialogen visar antal hämtade sidor och CloudKit-poster, antal importerade föremål,
antal nytillkomna och hur många som är tillgängliga i appen. **CloudKit-poster är
inte samma sak som AirTags:** samma föremål kan ha separata poster för nycklar,
namn och tidsjustering, och zonen kan innehålla andra typer av poster.

Om exempelvis 31 föremål finns över två svarssidor läses båda, inklusive posterna
i den avslutande sidan. En lista sparas först när Apple uttryckligen markerat
synkroniseringen som färdig. Saknad eller upprepad fortsättningstoken, en cykel
eller mer än 100 sidor ger ett synligt fel och behåller den tidigare listan.
Detta ersätter bibliotekets beteende som bara loggade en varning och returnerade
ett ofullständigt resultat i sådana fall.

Poster som inte kan dekrypteras eller konverteras räknas separat och listan
markeras som delvis ofullständig. Tidigare importerade föremål som saknas i
resultatet behålls och räknas som behållna; uppdateringen raderar dem inte.
En aktuell tidsjustering från tidigare positionssökningar behålls när CloudKit
har äldre tidsjusteringsdata för samma föremålsnyckel.

Om bara 20 fortfarande importeras efter en ny kontroll, dela räknarna från
dialogen. De visar om Apple levererade fler poster men dekryptering/konvertering
uteslöt något, eller om inga fler användbara föremål levererades. Råa
CloudKit-svar, nyckelfiler, fortsättningstoken och kontouppgifter ska inte delas.
Delade föremål och nya tillbehörsformat beror fortfarande på bibliotekets stöd.

## Lagring och verifiering

`inventory-zone-keys.enc` sparar enbart föremålszonens privata nycklar krypterade
med appkontots befintliga lagringsnyckel. De bredare iCloud-nyckelringsnycklarna
sparas inte av den här funktionen. Apple-lösenord och skärmlåskod sparas inte här.
`inventory-status.json` innehåller bara tider och antal för importstatus.
Filerna ligger i respektive appkontos privata datakatalog. Säkerhetskopiera hela
appdata enligt ACCOUNTS.md.

Testerna provar 31 föremål över flera sidor med en tidigare lista på 20, sedan
ett nytillkommet 32:a föremål, samt ofullständiga svar, fel och nyckellagring.
Detta verifierar appens beteende med simulerade Apple-svar, inte hur många
föremål ditt verkliga konto kan exportera.
