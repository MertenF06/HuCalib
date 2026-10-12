# Opnames: camera-identiteit, sessiemanifest en framelog

Elke nieuwe opname bewaart naast de losse video's een `session.json`, een
`frames.jsonl` en, wanneer een kalibratie beschikbaar is, een kalibratiekopie.
Bestaande projecten en kalibratieprofielen blijven leesbaar. Oude video's
worden niet aangepast en krijgen niet achteraf verzonnen tijdstempels.

## Bestanden

```text
rec_20261009_120000/
├── cam01.mp4                     # of .avi bij codec-fallback
├── cam02.mp4
├── session.json
├── frames.jsonl
└── calibration/                  # alleen als er een kalibratie beschikbaar is
    ├── calibration.json          # volledig profiel op het moment van starten
    └── calibration.toml          # motion-capture-export voor deze camera's
```

Camerabestanden hebben vaste namen. De natuurlijke volgorde van source-ID's
bepaalt de nummering: `cam0`, `cam2`, `cam10` worden `cam01`, `cam02`, `cam03`.
Gebruikerslabels staan in het manifest en beïnvloeden de bestandsnamen niet.
Gelijke labels, ongeldige tekens en Windows-namen zoals `CON` veroorzaken dus
geen botsende videobestanden. Ook de gewone TOML-export gebruikt deze natuurlijke
volgorde. Een bestaande, niet-lege opnamemap wordt nooit overschreven.

## Manifest (`schema_version = 1`)

Het manifest bevat een sessie-ID, begin/eindtijd, nominale FPS, klokreferentie,
en een geordende lijst `cameras`. Per camera:

- `camera_id`, `label` en `source` (soort invoer en device-index/bestand);
- `video_order`, `video_file`, werkelijk geschreven beeldformaat en video-FPS;
- `calibration_table`, `calibration_order` en gekalibreerde beeldafmetingen;
- aantallen `written`, `capture_failed`, `queue_dropped` en `encode_failed`;
- aantal naar het oorspronkelijke videoformaat geschaalde frames;
- status van eventuele correctie van de gemiddelde afspeel-FPS.

De kalibratie wordt bij het starten gekopieerd. Latere wijzigingen in het
actuele projectprofiel veranderen deze kopie niet. SHA-256-controlesommen
identificeren de JSON- en TOML-bestanden. Cameraparameters blijven in meters
met wereld-naar-camera-extrinsics; de wereldoriëntatie wordt niet aangepast.

`analysis_issues` maakt ontbrekende kalibraties, afwijkende resoluties,
verschillende video-/kalibratievolgorde, ontbrekende frames, geschaalde frames
en mislukte of onafgeronde FPS-correctie expliciet. Camera's zonder opgeloste
kalibratie blijven zichtbaar in het manifest; er worden geen parameters voor
verzonnen. Gebruik niet blind alle video's met een TOML waar camera's uit zijn
weggelaten. De latere Pose2Sim-adapter moet deze problemen eerst beoordelen.

Paden naar video, framelog en kalibratie zijn relatief aan de opnamemap. De
bestaande functies voor bewaren, hernoemen en verwijderen blijven hierdoor
werken. De oorspronkelijke invoer-URI is alleen herkomstinformatie.

**Een OpenCV-device-index is geen permanente fysieke webcam-identiteit.**
`hardware_identity_verified` is daarom `false`; bij webcams is `identity_scope`
`opencv_device_index`. Controleer na opnieuw aansluiten de fysieke camera's
tegen hun kalibratie. Deze wijziging claimt geen herkenning van USB-serienummers.

## Framelog

`frames.jsonl` bevat één JSON-regel per camera per aangeboden opnameronde,
met precies één terminale uitkomst:

| Status | Betekenis |
|---|---|
| `written` | De codec-aanroep is teruggekeerd zonder fout |
| `capture_failed` | Beeld ontbreekt, of uitlezen/ophalen is mislukt |
| `queue_dropped` | De encoderwachtrij was vol |
| `encode_failed` | De encoder kon niet openen of gaf een fout |

Iedere regel bevat de sessie- en camera-ID, het batch-ID, de sessie-batchindex,
de batchindex uit de live worker, bronframenummer waar beschikbaar, en het
1-gebaseerde `video_frame_index` voor geschreven beelden. Bij ontbrekende
beelden is dat videoframenummer `null`. De batchindex houdt de plek van het
ontbrekende beeld vast, ook wanneer één camera daarna minder videoframes heeft.
De regels kunnen door de encoderthread in een andere tijdsvolgorde binnenkomen;
gebruik batchindex en camera-ID voor koppeling, niet de regelnummers.

Monotone begin-/eindtijden in nanoseconden meten de uitleesoperatie. Het midden
daarvan is `timestamp_monotonic_ns`, met `timestamp_kind=host_read_midpoint`.
Dit blijft bruikbaar als de systeemklok wordt bijgesteld. Bij oude callers
zonder FramePacket staat expliciet `timestamp_kind=recorder_handoff`; er worden
geen ontbrekende sensortijdstempels ingevuld. De Unix-tijd blijft apart staan.

Dit zijn **hosttijden, geen belichtingstijden of hardware-synchronisatie**.
Framelogging kan niet vaststellen hoeveel beelden al onzichtbaar in sensor of
driver verloren gingen. `written` bewijst evenmin dat de codec elk beeld duurzaam
heeft opgeslagen: OpenCV bevestigt dit niet. Daarom staat
`video_integrity_check=not_performed` in het appmanifest. De videoproef hieronder
controleert onafhankelijk hoeveel beelden uit de bestanden terug te lezen zijn.

De kleine metadataregels worden naar disk geflusht; de app wacht daarbij niet
op video-encoding, maar trage of volle opslag kan ook metadataverwerking hinderen.
Een log-/manifestfout wordt als opnamefout doorgegeven en niet als succesvolle
afronding gemeld. Een sessie die tijdens de opname onderbroken wordt, blijft
herkenbaar aan een niet-afgeronde manifeststatus.

## FPS-correctie

De bestaande gemiddelde FPS-correctie blijft beschikbaar. Zij verandert geen
framenummers in het log en herstelt geen gaten of sensorsynchronisatie. Het
manifest vermeldt de daadwerkelijke gekozen video-FPS en status per camera.
Voordat een opnieuw gecodeerd bestand het origineel vervangt, wordt gecontroleerd
of alle verwachte originele frames konden worden teruggelezen. De videoproef
verifieert bovendien de gecorrigeerde bestanden.

## Tests zonder webcams

Vanuit de repository, met de HuCalib-dependencies geïnstalleerd:

```text
python -m unittest discover -s tests -v
```

Op een cloudmachine zonder scherm: stel `QT_QPA_PLATFORM=offscreen` in. De tests
gebruiken tijdelijke mappen en simuleren onder andere gelijke labels,
klokveranderingen, ontbrekende beelden, queue-overloop, encoderfouten,
resolutiewijzigingen en manifestfouten.

Gebruik de officiële Pose2Sim `Demo_SinglePerson`, met de bijbehorende TOML uit
de kalibratieconversie. De onderstaande proef leest alle vier video's, biedt
ze aan de echte recorder aan en simuleert één ontbrekend camerabeeld:

```text
python tests/smoke_pose2sim_recording.py --demo C:/data/Demo_SinglePerson --calibration C:/data/Calib_qualisys.toml --output C:/data/opnameproef --fps 25
```

De uitvoermap moet nieuw of leeg zijn. Herhaal met `--fps 60` en een andere
uitvoermap om encoderbelasting te onderzoeken. Iedere aangeboden camerarondgang
moet in het log terug te vinden zijn; decoder-aantallen en manifesttellingen
moeten overeenkomen. Wachtrijverlies op een zware proef hoeft niet nul te zijn,
maar mag niet ongemerkt blijven. Het script maakt `smoke-results.json`.

Met `--live-worker` wordt ook de echte `LiveCaptureWorker` op de videobestanden
uitgevoerd, zonder de gesimuleerde fout. Met `--pose2sim-check` wordt de geëxporteerde
kalibratie met een aanwezige Pose2Sim-installatie gelezen en worden de
projectiematrices vergeleken. Gebruik daarvoor een aparte validatieomgeving;
Pose2Sim installeren is niet nodig om de HuCalib-app te starten.

Deze proeven testen bestandsinvoer, codecs en metadata. De 60-FPS-demobeelden
op 25 FPS aanbieden is een recorderproef, geen bewijs van correcte bewegingstijd.
Windows-driverwerking, echte sensorsynchronisatie en reconnect-identiteit blijven
praktijktests met webcams.
