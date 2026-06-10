# HuCalib uitrollen en updaten (Velopack)

HuCalib gebruikt [Velopack](https://velopack.io/) om een **installer** te maken
en om de app **zichzelf te laten updaten** via GitHub Releases. Dit document
legt uit hoe je dat gebruikt en beheert.

## Het idee in het kort

```
   jouw pc                         GitHub Releases                  gebruiker
 ┌──────────┐   build_release      ┌─────────────────┐   check     ┌──────────┐
 │ code +   │ ───────────────────► │  Setup.exe      │ ◄────────── │ HuCalib  │
 │ versie   │   -Upload            │  *.nupkg        │   download   │ (geïnst.)│
 └──────────┘                      │  RELEASES feed  │ ──────────► │  herstart│
                                   └─────────────────┘             └──────────┘
```

1. Jij past code aan en verhoogt het versienummer.
2. Eén commando bouwt de app en publiceert een release naar GitHub.
3. Geïnstalleerde apps zien bij het opstarten dat er een nieuwere versie is,
   vragen de gebruiker of die wil updaten, downloaden en herstarten.

"Vanuit jouw pc updates pushen" werkt dus via GitHub als tussenstation — niet
als directe pc-naar-pc-verbinding. GitHub Releases is gratis.

## Eenmalige setup (alleen de eerste keer)

Je hebt dit al geïnstalleerd, maar voor een nieuwe machine:

- **.NET SDK** (voor de `vpk` tool) — https://dotnet.microsoft.com/download
- **De vpk tool**:
  ```powershell
  dotnet tool install -g vpk
  ```
- **Python dependencies** (incl. `velopack`):
  ```powershell
  pip install -r requirements.txt
  ```
- Een **GitHub Personal Access Token** met `repo`-scope om te kunnen
  publiceren. Maak die aan op https://github.com/settings/tokens en zet hem in
  je omgeving zodat je hem niet steeds hoeft te typen:
  ```powershell
  $env:GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxx"
  ```

## Een nieuwe versie uitbrengen

1. **Verhoog de versie** in [`mocap_app/__init__.py`](mocap_app/__init__.py):
   ```python
   __version__ = "1.0.1"
   ```
   Dit is de enige plek; het build-script leest het hier vandaan. Gebruik
   oplopende versies (`1.0.0` → `1.0.1` → `1.1.0`). Velopack staat standaard
   geen downgrade toe.

2. **Bouw en publiceer** met één commando:
   ```powershell
   .\build_release.ps1 -Upload
   ```
   Dit doet achter elkaar: PyInstaller bouwen → Velopack-pakket maken →
   uploaden naar GitHub Releases (tag `v1.0.1`).

   Wil je eerst lokaal testen zonder te publiceren? Laat `-Upload` weg:
   ```powershell
   .\build_release.ps1
   ```
   De output staat dan in `.\Releases\`.

3. Klaar. Iedereen met HuCalib geïnstalleerd krijgt bij de eerstvolgende start
   de melding "Versie 1.0.1 is beschikbaar".

## De allereerste installatie bij gebruikers

Auto-update werkt **alleen** als mensen de app via de Velopack-installer hebben
geïnstalleerd. Voor de eerste keer:

1. Breng versie `1.0.0` uit met het stappenplan hierboven.
2. Geef mensen het bestand **`HuCalib-win-Setup.exe`** (te vinden onder de
   release op GitHub, of in `.\Releases\`). Dat installeert HuCalib netjes en
   maakt een snelkoppeling.

> ⚠️ De oude losse `HuCalib.exe` (één bestand) kan **niet** auto-updaten.
> Vanaf nu altijd via `Setup.exe` distribueren.

## Hoe de update-check in de app werkt

- Bij het opstarten checkt de app ~2,5s na het openen op de achtergrond
  ([`mocap_app/core/updater.py`](mocap_app/core/updater.py)). Is er niets, dan
  merkt de gebruiker niets.
- Is er een nieuwere versie, dan volgt een ja/nee-vraag. Bij "ja" wordt de
  update gedownload (met voortgangsbalk) en herstart de app op de nieuwe versie.
- Gebruikers kunnen ook handmatig checken via **Help → Controleren op updates…**.
- In een dev-checkout (vanuit de broncode draaien) gebeurt er niets — Velopack
  werkt alleen op een geïnstalleerde build.

## Delta-updates

Velopack maakt automatisch **delta-pakketten**: gebruikers downloaden alleen het
verschil t.o.v. hun huidige versie, niet steeds de volledige app. Je hoeft
daar niets voor te doen; zorg alleen dat je `.\Releases\`-map blijft bestaan
tussen releases (of laat `vpk` de vorige release van GitHub ophalen) zodat het
de delta kan berekenen.

## Code-signing (optioneel maar aanbevolen)

Zonder code-signing-certificaat toont Windows SmartScreen bij de eerste
installatie een "onbekende uitgever"-waarschuwing. De app werkt prima, maar oogt
minder vertrouwd. Met een certificaat (±€200–400/jaar) verdwijnt die melding.
Je geeft het dan mee aan `vpk pack` met `--signParams`. Zie de
[Velopack-docs over signing](https://docs.velopack.io/).

## Handige commando's

```powershell
# Alleen bouwen, niet publiceren (test in .\Releases\)
.\build_release.ps1

# Bouwen én publiceren naar GitHub
.\build_release.ps1 -Upload

# Token eenmalig meegeven i.p.v. via $env:GITHUB_TOKEN
.\build_release.ps1 -Upload -Token ghp_xxxx
```

## Bronnen

- Velopack — Getting Started (Python): https://docs.velopack.io/getting-started/python
- Velopack docs: https://docs.velopack.io/
- Velopack op GitHub: https://github.com/velopack/velopack
