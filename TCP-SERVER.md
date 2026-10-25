# AirTag-positioner till egen TCP-server

Appen kan skicka de positioner den hämtar från Apple med Teltonika Codec 8
eller Codec 8 Extended (8E). Funktionen är avstängd från början.

## Ställ in i appen

1. Uppdatera containern i Unraid till den nya imagen när GitHub Actions har
   publicerat den. Behåll `/data`, ditt applösenord och din valda fasta IP.
2. Hämta föremål och positioner. Öppna **Skicka positioner till egen server**
   under kartan och inloggningsdialogen.
3. Ange mottagarens IP/värdnamn, TCP-port och Codec 8 eller 8E. Port 5027 är
   bara ett förslag; välj porten din egen server faktiskt lyssnar på.
4. Varje föremål får automatiskt ett stabilt, unikt 15-siffrigt virtuellt ID.
   Du kan ändra det. Registrera samma ID i din mottagare. Detta är inte ett
   riktigt, tilldelat mobil-IMEI och finns inte på AirTagens hölje.
5. Aktivera och spara. Även den senast sparade positionen köas vid aktivering.
   Tryck **Hämta positioner** för nya Apple-rapporter eller aktivera ett intervall
   under **Automatisk positionssökning**. Varje ny position som hämtas skickas automatiskt när bryggan
   är aktiverad; föremål utan position hoppas över.

**Välj föremål att hämta** styr både manuell och automatisk sökning. Tidigare
positioner som är markerade som ej valda köas inte som nya poster. Ett nytt
urval tar inte bort redan köade TCP-poster från tidigare hämtningar.

Unraid behöver kunna nå mottagaren utgående via TCP. Ingen extra inkommande
Docker-port behövs för bryggan. Använd en IP som är nåbar från containerns
Custom-nätverk. Trafiken är vanlig TCP utan TLS; använd privat nät eller VPN
om transporten behöver krypteras.

## Vad din mottagare ska göra

Appen öppnar en separat TCP-anslutning för varje köpost och stänger den efter
kvittens. Samma ID kan därför ansluta igen för varje position och återförsök.
Alla heltal är big endian utom själva CRC-beräkningens interna bitordning.
TCP-läsningar måste buffras: ett `recv()` behöver inte innehålla hela fältet.

1. Läs två byte: `00 0F`. Läs sedan exakt 15 ASCII-siffror för föremålets ID.
2. Svara med den binära byten `01` för accepterat ID, eller `00` för avvisat.
   ASCII-texten `"01"` fungerar inte.
3. Läs åtta byte: fyra nollbyte och fyra byte som anger datafältets längd.
   Läs sedan datafältet och fyra byte CRC.
4. Kontrollera CRC-16/IBM, init 0, reflekterat polynom `0xA001`. Beräkna över
   datafältet från codec-ID till och med det andra postantalet. CRC-värdet
   ligger i de sista fyra byten som `00 00 xx xx`.
5. Datafältet består av codec-ID (`08` eller `8E`), postantal `01`, en AVL-post
   och ett avslutande postantal `01`. Lagra posten innan du kvitterar.
6. Svara med fyra binära byte `00 00 00 01` när en post har lagrats.
   Noll, fel antal, timeout eller stängd anslutning leder till återförsök.

AVL-posten innehåller följande:

| Fält | Byte | Värde |
| --- | ---: | --- |
| Tid | 8 | Ursprunglig rapporttid från Apple i Unix-millisekunder, UTC |
| Prioritet | 1 | 0 |
| Longitud | 4 | Signerat heltal, grader × 10 000 000 |
| Latitud | 4 | Signerat heltal, grader × 10 000 000 |
| Höjd | 2 | 0, saknas |
| Riktning | 2 | 0, saknas |
| Satelliter | 1 | 0, saknas |
| Hastighet | 2 | 0, saknas |
| IO Codec 8 | 6 | Event-ID, totalantal och N1/N2/N4/N8: alla 0, en byte per fält |
| IO Codec 8E | 14 | Event-ID, totalantal och N1/N2/N4/N8/NX: alla 0, två byte per fält |

Enbart IO-raden för vald codec används. Datafältet är 33 byte för Codec 8
eller 41 byte för Codec 8E. Hela TCP-paketet är 45 respektive 53 byte.
Inga batteri-, mobilnäts- eller andra IO-värden hittas på.

**Viktigt för din server:** satelliter=0 betyder här att uppgiften saknas,
inte att koordinaterna ska kasseras. AirTagens position kommer från Apples
nätverk, inte från en GPS-mottagare i AirTagen. Höjd/hastighet/riktning=0
är också platshållare och ska inte tolkas som verkliga mätningar.
Positionsnoggrannheten från Apple visas på kartan men skickas inte i TCP-paketet.

## Kö, dubbletter och ändrade inställningar

`/data/teltonika.sqlite3` sparar inställningar, ID-kopplingar, osända positioner
och kvitterade postfingeravtryck. Det överlever containeromstart. Kör en
Gunicorn-worker enligt Dockerfilen; flera parallella workers stöds inte.
Bryggan startar med webbservern och behöver ingen öppen webbläsare.

Samma ID, mottagare, codec, rapporttid och koordinater köas inte igen efter en
upprepad hämtning. En senare rapporttid skickas även om föremålet står still.
Ändrat ID, mottagare eller codec innebär en ny leveransström och den senast
sparade positionen kan skickas igen. Köade poster behåller sitt gamla ID,
protokoll och mottagare när inställningarna ändras. Avaktivering pausar kön;
en redan påbörjad sändning kan slutföras. Återaktivering återupptar gamla poster.

Misslyckad leverans försöks igen efter 5 sekunder, sedan med ökande väntan upp
till 5 minuter. En felande enhet blockerar inte övriga enheter. Poster skickas
i köordning per ID och mottagare. Nätverksoperationer har timeout.

Leverans är **minst en gång**: om servern sparar posten men kvittensen försvinner
kan samma post skickas igen. Mottagaren behöver därför avdubbla på ID, rapporttid
och koordinater (och vid behov codec). Behåll kvitterade fingeravtryck för att
undvika omsändning efter omstart. Databasen växer över tid och har ingen automatisk
rensning; ta med den i appdata-backup och övervaka diskutrymmet. Om `/data` inte
går att skriva kan nya positioner inte garanteras i kön; kartans senaste position
försöks köas igen när lagringen fungerar.

## Verifiering

`tests/test_teltonika.py` testar båda codec-formaten, negativa koordinater,
CRC mot Teltonikas publicerade exempel, ID-validering, beständig kö,
avdubblering, återförsök, paus och en verklig lokal TCP-mottagare med
fragmenterade/felaktiga kvittenser. Enhetstesterna använder inga Apple-konton.
Din produktionsserver och riktig Apple-hämtning måste verifieras separat.

Protokollkälla: [Teltonika Data Sending Protocols](https://wiki.teltonika-gps.com/view/Teltonika_Data_Sending_Protocols).
