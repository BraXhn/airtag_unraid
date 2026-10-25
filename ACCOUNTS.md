# Egna användarkonton

Administratören loggar in som **admin** med containerns befintliga `APP_PASSWORD`.
Den nya inloggningssidan ersätter webbläsarens lösenordsruta. Befintliga
admin-enheter, Apple-sessioner, kartdata och TCP-inställningar ligger kvar.

## Skapa ett konto

1. Uppdatera containern till den publicerade imagen. Behåll `/data` och `APP_PASSWORD`.
2. Logga in som **admin** och öppna **Hantera användarkonton** i vänsterspalten.
3. Ange ett unikt användarnamn och ett separat lösenord med minst 12 tecken.
   Användarnamnet har 3–40 tecken: a–z, siffror, punkt, bindestreck eller
   understreck, och börjar med en bokstav. `admin` är reserverat.
4. Klicka **Skapa konto**. Förmedla uppgifterna till den avsedda användaren.
5. Användaren öppnar samma appadress och loggar in med sitt appkonto. Därefter
   väljer användaren **Anslut Apple-konto**, slutför Apples verifiering och
   väljer sina egna föremål. Kontot börjar utan enheter; admin-enheter kopieras inte.

Detta skapar lokala appkonton, inte Apple-konton. Ett appkonto använder ett
Apple-konto åt gången. Inga Apple-uppgifter behövs för att administratören ska
skapa appkontot. Ingen e-postinbjudan skickas och självregistrering finns inte.
Upp till 100 vanliga konton kan skapas; större belastning är inte verifierad.

## Vad varje användare har

Varje konto har egen karta och positionscache, egen Apple-session och
egna föremålsnycklar, eget enhetsurval, egen inloggningsdialog, egna sökintervall
och egna TCP-inställningar med virtuella IMEI och leveranskö. Samma API-adresser
används för alla; servern väljer data från den inloggade identiteten. Ett konto-ID
i en URL, frågeparameter eller begärandekropp väljer aldrig en annan användares data.

Vanliga användare kan inte skapa konton, återställa andras lösenord eller läsa
andras enheter. Administratörsvyn hanterar appkonton och innehåller ingen funktion
för att öppna en annan användares karta eller Apple-dialog. Den som administrerar
själva Unraid-servern har fortfarande åtkomst till appdata och serverns nycklar.
Detta är separation i appen, inte separata operativsystem eller containrar.

Jobb och scheman är separata. Ett pågående jobb blockerar nästa jobb för samma
användare. Kör fortfarande endast **en Gunicorn-worker**, enligt Dockerfilen.
Aktiverade scheman och TCP-köer återupptas efter containeromstart utan att användaren
behöver öppna webbläsaren. Första Apple-inloggningen för ett nytt konto förbereder
CoreADI i den användarens datakatalog och behöver minst 1 GB ledigt utrymme.

## Pausa konto och återställ lösenord

I **Hantera användarkonton** kan admin pausa/aktivera konton och ange nya lösenord.
En paus spärrar inloggning, avslutar befintliga webb-sessioner, stoppar kontots
Apple-jobb och pausar dess bakgrundsarbete. Redan påbörjad TCP-trafik kan slutföras.
Data och köposter behålls. Vid återaktivering måste användaren logga in igen;
gamla webb-sessioner blir inte giltiga igen.

Lösenordsåterställning avslutar tidigare webb-sessioner men behåller den krypterade
Apple-sessionen och föremålsnycklarna. Det nya appkontots lösenord är inte dess
lagringsnyckel. Det ursprungliga admin-kontot använder fortfarande `APP_PASSWORD`
även för sin äldre krypterade lagring; ändra inte detta för att förnya en Apple-session.
Admin-kontot kan inte pausas eller återställas i kontolistan.

## Webb-inloggning och HTTPS

Webb-inloggningen gäller i högst **12 timmar** innan ny appinloggning behövs.
Det är separat från Apples session, vars giltighetstid fortfarande är okänd.
**Logga ut** avslutar webbläsarens inloggning och stoppar inte bakgrundsschemat.
Lösenord lagras som saltade scrypt-hashar. Upprepade misslyckade inloggningsförsök
begränsas per källadress och användarnamn. Sessionscookies är HttpOnly och SameSite=Strict.
CSRF-skydd krävs även vid inloggning, utloggning och kontohantering.

Använd HTTPS för åtkomst från andra nätverk, exempelvis via din Cloudflare Tunnel.
Vid enbart HTTPS-åtkomst sätter du även variabeln **COOKIE_SECURE=1** i Unraid
(Add another Path, Port, Variable… → Variable). Då skickar webbläsaren sessionscookien
enbart via HTTPS. Lokal HTTP-inloggning fungerar inte med det värdet; standard är 0
för befintliga lokala installationer. HTTPS och den inställningen gäller webbappen,
inte Teltonika-mottagarens TCP-port.

Äldre Basic-auth-klienter stöds fortfarande. Webbläsare bör använda den nya
inloggningssidan och **Logga ut** för att byta konto, alternativt separata
webbläsarprofiler för samtidiga konton. Inga kontouppgifter läggs i URL:er.

## Lagring och backup

- `/data/accounts.sqlite3`: konton, lösenordshashar, status och sessionsversioner.
- `/data/accounts.key`: installationshemlighet för webb-sessioner och härledning
  av olika krypteringsnycklar för nya användare. Förvara den tillsammans med backupen.
- `/data/users/<slumpmässigt konto-ID>/`: respektive användares Apple-data,
  positionscache, scheman, enhetsurval och TCP-databas.
- Befintliga admin-data ligger kvar direkt i `/data` med sina tidigare filnamn.

Säkerhetskopiera hela appdata-katalogen, helst med containern stoppad. Återställ
kontodatabas, `accounts.key` och användarkataloger tillsammans. Om nyckeln saknas
medan kontodatabasen finns vägrar appen starta så att den inte skapar en ersättande
nyckel som gör gamla data oläsbara. Lösenordsåterställning kan inte ersätta en
förlorad installationsnyckel. Kontodatabasen och nyckelfilen har behörighet 0600.
