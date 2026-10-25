# Välj vilka föremål som ska hämtas

Under **Välj föremål att hämta** visas föremålen vars nycklar appen redan har
hämtat. Listan bygger på den krypterade lokala nyckelfilen; det krävs inga nya
positionsanrop till Apple för att visa eller ändra urvalet. Webb-API:t lämnar
endast ut namn, modell, identifierare och vald-status, aldrig privata nycklar.

1. Uppdatera containern till senaste publicerade versionen.
2. Befintliga föremål visas direkt. Annars väljer du **Anslut Apple-konto** för
   att hämta föremålsnycklarna. Det steget söker inte längre positioner automatiskt,
   så du hinner göra urvalet.
3. Kryssa i önskade föremål och klicka **Spara urval**.
4. Välj **Hämta positioner**, eller aktivera ett automatiskt intervall.

**Alla föremål, även nytillkomna** är standard för bakåtkompatibilitet.
Om du avmarkerar ett enskilt föremål övergår listan till ett eget urval.
Nya föremål läggs då inte till i sökningen automatiskt. Markera huvudkryssrutan
om du vill inkludera även framtida föremål från kontot.

Samma urval gäller manuell sökning, ny positionsinloggning och automatisk
positionssökning. Det sparas i `/data/device-selection.json` och behålls vid
containeromstart. Urvalet gäller nästa körning; en redan startad sökning
fortsätter med den lista den startade med.

Om inget föremål är valt görs ingen positionsförfrågan och automatiken pausas.
Välj och spara minst ett föremål för att schemat ska fortsätta. Inställningar,
Apple-session och alla föremålsnycklar behålls även när bara några föremål hämtas.

Tidigare positioner för ej valda föremål kan ligga kvar på kartan med
markeringen **Ej vald för hämtning** och sin ursprungliga rapporttid. De
uppdateras inte och köas inte som nya TCP-poster av den körningen. Redan
köade TCP-poster från tidigare hämtningar påverkas inte av ett nytt urval.

Urvalet begränsar positionssökningarna. För att visa tillgängliga föremål
behöver appen fortfarande läsa kontots exporterbara föremålsnycklar under
**Anslut Apple-konto**. Inga föremål eller kopplingar hos Apple ändras.
