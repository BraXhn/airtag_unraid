# GitHub och Docker-image

Källkod och template: https://github.com/BraXhn/airtag_unraid

Actions: https://github.com/BraXhn/airtag_unraid/actions

Container-image efter lyckat bygge: `ghcr.io/braxhn/airtag_unraid:latest`.

Arbetsflödet `.github/workflows/container.yml` kör lokala tester, bygger Linux amd64, provar exportverktygets start och appens hälsokontroll utan Apple-konto. Först om alla steg lyckas publiceras imagen med taggarna `latest` och commit-id. Första gången kan paketet vara privat; kontrollera dess åtkomst separat innan Unraid-installation.

Apple-inloggning och faktisk AirTag-åtkomst testas inte av Actions. Lägg inga Apple-uppgifter i GitHub, Actions-secrets eller templaten. Appdata, exporterade nycklar och sessioner ska stanna på din server. Appens eget slumpmässiga lösenord anges vid installationen.

Installationssteg: [INSTALL-UNRAID-TEMPLATE.md](INSTALL-UNRAID-TEMPLATE.md).
