# HuCalib

HuCalib is een cameracalibratietool voor motion-capture-opstellingen in de
fysiotherapie, gebouwd in Python. Het programma biedt een grafische interface
om meerdere camera's te kalibreren ter voorbereiding op bewegingsopnames.

> **Geplande functie:** een toekomstige versie ondersteunt het opnemen van
> bewegingen. Die opnames worden niet door het programma zelf geanalyseerd, maar
> kunnen geëxporteerd worden naar externe tools zoals
> [Pose2Sim](https://github.com/perfanalytics/pose2sim) voor verdere verwerking.

---

## Inhoud

- [Overzicht](#overzicht)
- [Functies](#functies)
- [Installatie](#installatie)
- [Gebruik](#gebruik)
- [Documentatie](#documentatie)
- [Bijdragen](#bijdragen)

---

## Overzicht

HuCalib is een studentenproject dat een gestroomlijnde werkwijze biedt voor het
kalibreren van camerasystemen voor fysiotherapie-onderzoek. Het programma
analyseert de beweging niet zelf; de kalibratie zorgt ervoor dat opnames
nauwkeurig genoeg zijn om door externe software verwerkt te worden.

---

## Functies

- Kalibratietool voor een nauwkeurige camera-opstelling
- Grafische interface die eenvoudig te bedienen is
- Projectbeheer: nieuwe projecten aanmaken of bestaande openen
- Video-opname (gepland), exporteerbaar naar tools zoals Pose2Sim

---

## Installatie

Open een opdrachtprompt met Anaconda en volg de onderstaande stappen.

**1. Maak een Python-omgeving aan**
```bash
conda create -n HuCalib-env python=3.12
```

**2. Activeer de omgeving**
```bash
conda activate HuCalib-env
```

**3. Kloon de repository**
```bash
git clone https://github.com/MertenF06/HuCalib
```

**4. Installeer de benodigde bibliotheken**
```bash
pip install -r HuCalib/requirements.txt
```

**5. Start de interface**
```bash
python HuCalib/run.py
```

De interface opent automatisch in een nieuw venster.

---

## Gebruik

### 1. Start een project
Klik op het **Home**-scherm op **Nieuw Project** om een nieuwe kalibratie te
beginnen, of op **Project Openen** om een bestaand project voort te zetten.

### 2. Sluit de camera's aan
Sluit je webcams aan voor of tijdens het gebruik van de app. Bij het opstarten
zoekt HuCalib automatisch naar camera's, opent elke gevonden camera en toont van
elke camera een live beeld op het tabblad **Camera's / Kalibratie**. Gebruik de
knop **Camera's zoeken** om opnieuw te scannen als je later een camera aansluit.

### 3. Kalibreer
Je hebt een geprint kalibratiebord nodig: standaard een **schaakbord**;
**ChArUco** wordt ook ondersteund. De afmetingen en de vierkantgrootte van het
bord pas je aan op het tabblad **Geavanceerde instellingen**.

Met de standaardinstellingen doorloopt één knop **Start kalibratie** de hele
volgorde automatisch:

1. **Intrinsics**: elke camera wordt op zichzelf gekalibreerd. Beweeg het bord
   rustig zodat het over het hele beeld van elke camera te zien is; het raster op
   het scherm vult zich naarmate er genoeg beelden zijn vastgelegd.
2. **Extrinsics**: de camera's worden ten opzichte van elkaar gekalibreerd. Houd
   het bord zo dat **meerdere camera's het tegelijk zien**, totdat elke camera
   verbonden is met de referentiecamera.

Daarna schakelt de app over naar het tabblad **Resultaten / Export**, dat een
kalibratieoordeel in gewone taal toont (geslaagd of niet).

### 4. Exporteer
Kies op het tabblad **Resultaten / Export** een formaat (**TOML** of **JSON**)
en klik op **Export** om de cameraparameters op te slaan voor gebruik in externe
tools zoals [Pose2Sim](https://github.com/perfanalytics/pose2sim).

---

## Documentatie

De broncode is gedocumenteerd met [Doxygen](https://www.doxygen.nl/). De
gegenereerde documentatie staat online en is vanuit de app te openen via
**Help → Documentatie openen**:

<https://mertenf06.github.io/HuCalib/>

### Lokaal genereren

De configuratie staat in [`Doxyfile`](Doxyfile) in de projectmap.

**1. Installeer Doxygen** via <https://www.doxygen.nl/download.html> (of
`winget install DimitriVanHeesch.Doxygen` op Windows, `apt install doxygen` op
Linux). Installeer eventueel [Graphviz](https://graphviz.org/) voor klasse- en
aanroepgrafieken.

**2. Genereer de HTML** vanuit de projectmap:
```bash
doxygen Doxyfile
```

**3. Open het resultaat** in `docs/doxygen/html/index.html`.

De gegenereerde uitvoer wordt niet meegecommit (staat in `.gitignore`). De
online versie wordt automatisch bijgewerkt door de workflow in
[`.github/workflows/docs.yml`](.github/workflows/docs.yml) bij elke push naar
`main`. Werk bij het verhogen van het versienummer in `mocap_app/__init__.py`
ook `PROJECT_NUMBER` in `Doxyfile` bij, zodat de documentatie de juiste versie
toont.

---

## Bijdragen

Dit project is ontwikkeld door studenten aan de Hogeschool Utrecht. Bijdragen
zijn welkom: open een [issue](https://github.com/MertenF06/HuCalib/issues) of
dien een pull request in.

---

> Dit project is gemaakt door en voor studenten, als onderdeel van een opleiding
> aan de Hogeschool Utrecht.
