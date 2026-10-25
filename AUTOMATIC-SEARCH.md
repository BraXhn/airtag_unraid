# Automatisk positionssökning och Apple-session

## Aktivera i appen

1. Uppdatera containern till senaste publicerade versionen. Behåll appdata,
   applösenord och container-IP.
2. Logga in och hämta föremålsnycklarna manuellt om det inte redan är gjort.
   Välj önskade föremål och klicka **Spara urval**. Samma urval används av schemat.
3. Välj intervall under **Automatisk positionssökning** och klicka
   **Spara intervall**. Funktionen är avstängd från början.

Tillgängliga intervall: 5, 15 och 30 minuter samt 1, 3, 6, 12 och 24 timmar.
**Av – endast manuellt** stänger av framtida automatiska körningar.
En redan pågående körning avslutas med **Avbryt** om den också ska stoppas.

Första automatiska körningen startar efter det valda intervallet. Därefter
räknas nästa intervall från att föregående körning har avslutats. Om en sökning
tar 10 minuter och intervallet är 5 minuter börjar nästa ungefär 15 minuter
efter den föregåendes start. Körningarna överlappar inte och missade tillfällen
samlas inte i en kö. En manuell sökning eller inloggning får också avslutas
innan en automatisk sökning kan starta.

Schemat körs i containern även när webbläsaren är stängd. En enda
Gunicorn-worker krävs, enligt Dockerfilen. Inställningarna sparas i
`/data/auto-refresh.json`. Efter containeromstart behålls schemat; om en
tidigare körning avbröts vid omstart väntar appen ett nytt intervall innan
den försöker igen.

Sökningen använder den befintliga Apple-sessionen och de sparade
föremålsnycklarna. Den registrerar inte en ny maskin och frågar inte efter
Apple-lösenord, verifieringskod eller skärmlåskod i bakgrunden. Om sådana
åtgärder behövs pausas automatiken och appen ber dig logga in manuellt.
När en manuell körning lyckas kan schemat fortsätta med det sparade intervallet.

Om inget föremål är valt pausas schemat tills du sparar ett nytt urval.
Se [DEVICE-SELECTION.md](DEVICE-SELECTION.md).

Vid nätverksfel väntar appen minst 15 minuter eller det valda intervallet,
beroende på vilket som är längst, innan nästa automatiska försök. Vid Apples
HTTP 429 respekteras den beständiga väntetiden på minst 45 minuter.
Automatik ändrar inte hur ofta AirTagen själv rapporterar till Apples nätverk.

Varje mottagen position sparas direkt och skickas till TCP-kön om
vidarebefordran är aktiverad. Samma rapport skickas inte om bara för att
sökningen körs flera gånger. Den tidigare gränsen på 15 minuter per jobb
gäller även automatiska körningar; redan sparade delresultat behålls.

## Hur länge förväntas Apple-inloggningen hålla?

**Ingen fast eller normalt förväntad giltighetstid är verifierad för den här
inofficiella Linux-integrationen.** Appen kan därför inte lova ett visst antal
timmar eller dagar eller visa ett tillförlitligt utgångsdatum.

Sessionen sparas krypterat och återanvänds tills Apple avvisar den eller kräver
ny verifiering. Det sparade tillståndet behålls vid containeromstart när
appdata och applösenordet är oförändrade, men det garanterar inte att Apple
fortsätter acceptera sessionen. Upstreams tester av emuleringen ger ingen
garanterad långsiktig sessionslivslängd:
[os-findmy-e](https://github.com/sacca97/os-findmy-e).

Under **Hur länge håller Apple-inloggningen?** visar appen:

- Tidpunkt för senaste nya, lyckade inloggning.
- Faktisk ålder på den inloggningen.
- Senaste slutförda positionssökning för ett föremål.
- Om manuell inloggning krävs. Förväntat utgångsdatum anges som okänt.

För sessioner som skapades innan denna funktion infördes är
inloggningstidpunkten okänd tills du loggar in på nytt. Filens ändringstid
används inte som en gissad inloggningstid: sessionen sparas även under hämtning.
De nya tidsuppgifterna sparas utan token eller lösenord i
`/data/session-status.json`.

När appen visar att ny inloggning behövs använder du **Ny positionsinloggning**.
Om föremålsnycklar saknas använder du **Anslut Apple-konto**. Byt inte
`APP_PASSWORD` för att förnya Apple-sessionen; det skulle göra befintliga
krypterade sessioner och nycklar oläsbara.
