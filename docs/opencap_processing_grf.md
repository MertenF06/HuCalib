# OpenCap Processing GRF: rechten en aansluiting op HU3D

Onderzocht op 12 oktober 2026, repository
[`utahmobl/opencap-processing-grf`](https://github.com/utahmobl/opencap-processing-grf/tree/6369b19d9b53fa357befe651401af25b8350622d),
commit `6369b19d9b53fa357befe651401af25b8350622d`.
Dit is een verkenning; er is geen integratie of simulatie uitgevoerd.

## Gratis commerciële integratie van de code

De [hoofdlicentie](https://github.com/utahmobl/opencap-processing-grf/blob/6369b19d9b53fa357befe651401af25b8350622d/LICENSE.md)
is Apache 2.0. Deze staat kosteloos commercieel gebruik, wijziging en
verspreiding toe. De eigen app hoeft hierdoor niet volledig openbaar te worden.
Bij verspreiding moeten de licentie en relevante copyright-/attributieteksten
worden behouden, wijzigingen worden aangegeven en eventuele toepasselijke
NOTICE-informatie worden meegeleverd. De licentie geeft geen algemeen recht op
de handelsmerken van de auteurs.

De onderzochte simulatiebestanden dragen Apache 2.0-headers. In de doorzochte
tekstbestanden is geen non-commercial of research-only beperking gevonden.
Er is niet dezelfde aantoonbare VideoPose3D-herkomstkwestie gevonden als bij
ForcePose. Dit is geen volledige audit van alle transitieve afhankelijkheden.

De code noemt CasADi onder LGPL 3.0. LGPL verbiedt commercieel gebruik niet;
bij bundeling gelden wel eigen voorwaarden, onder meer voor vervanging van de
bibliotheek en eventuele wijzigingen eraan. OpenSimAD downloadt daarnaast
platformspecifieke pakketten van SourceForge; de inhoud en licenties daarvan
zijn in deze verkenning niet uitgepakt en beoordeeld.

## Wat het daadwerkelijk toevoegt

De [README](https://github.com/utahmobl/opencap-processing-grf/blob/6369b19d9b53fa357befe651401af25b8350622d/README.md)
beschrijft een hybride machine learning–simulatie-aanpak voor krachten,
gewrichtsmomenten en gewrichtscontactkrachten, gebaseerd op OpenCap-kinematica.

Het [batchscript](https://github.com/utahmobl/opencap-processing-grf/blob/6369b19d9b53fa357befe651401af25b8350622d/batchDownload_ForGRFTrack.py)
maakt model-/bewegingsinvoer, vraagt GRF op bij
`gradio_client.Client("alanttan/GaitDynamics")` en vervangt de voorspelde COP
vervolgens met een afzonderlijke COP-voorspelling. Het gaat dus om aanvulling
op GaitDynamics, niet om een volledig onafhankelijke GRF-voorspeller.
De hybride simulatie probeert de kinematica en voorspelde krachten samen te
volgen met een biomechanisch model en voet-grondcontact.

Er is een COP-checkpoint `OpenSimPipeline/ForGaitDynamics/cop_mlp_ar.pt` in de
repository opgenomen. Er is geen afzonderlijke modellicentie in de bekeken
bestanden gevonden; het bestand wordt als onderdeel van de Apache-repository
aangeboden. Een afzonderlijke bevestiging van herkomst en toepasselijkheid op
dit checkpoint kan een productdossier versterken. Externe GaitDynamics-gewichten
en de hosted dienst zijn afzonderlijke onderdelen en krijgen niet automatisch
de Apache-licentie van deze clientcode. De openbare GaitDynamics-hoofdcode
heeft MIT; de voorwaarden van de daadwerkelijk gebruikte modelbestanden en
dienst blijven apart te controleren.

## Aansluiting op HU3D

- Gebruik lokaal beschikbare Pose2Sim-resultaten in plaats van downloaden via
  een OpenCap-account. Daarvoor moeten model, markers, assen, eenheden en
  coördinaten op het verwachte OpenCap-model worden aangesloten.
- Vervang de hosted GaitDynamics-aanroep door de bestaande lokale HU3D/WSL2-route.
  De huidige voorbeeldcode uploadt de bewegings- en modelbestanden naar de dienst.
- Voeg de COP-voorspelling en voetoptimalisatie als afzonderlijk testbare stappen
  toe voordat de volledige hybride simulatie wordt geprobeerd.
- Controleer het verwachte spier-/contactmodel. Het bestaande HU3D-model is niet
  zonder verificatie een vervanging voor de modellen van dit project.

Windows wordt in de installatie-instructies ondersteund: OpenSim 4.5 en een
C++-compiler met Visual Studio zijn nodig voor de simulatiecomponent. Dat maakt
dit project niet automatisch een native Windows-vervanging voor de huidige
lokale GaitDynamics-runtime. De gratis Visual Studio Community-editie heeft
eigen organisatie-/gebruiksvoorwaarden; de README-claim dat zij voor iedereen
gratis is, moet niet als algemene commerciële licentiegarantie worden gebruikt.

De aanpak verwacht 3D-kinematica en modeldata, geen rechtstreekse Sports2D-invoer.
Een vlakke reconstructie kan worden onderzocht, maar valt buiten de hier
bevestigde werking. De voorbeelden focussen op lopen; de bestaande korte
evenwichtsoefening van HU3D is geen passende validatie van loopdynamica.

Het [loopvoorbeeld](https://github.com/utahmobl/opencap-processing-grf/blob/6369b19d9b53fa357befe651401af25b8350622d/Example_GRFTrack/example_walking_opensimAD_GRF.py)
waarschuwt expliciet dat de voorbeelden demonstreren hoe simulaties draaien en
geen biomechanische validiteit garanderen. De code adviseert korte segmenten
van minder dan twee seconden voor simulatie; `[]` kan de volledige trial kiezen.
Dit is een aanbeveling vanwege de simulatiebelasting, geen vaste maximale
videolengte.

## Besluit

Dit is een concrete, commercieel bruikbare kandidaat op het niveau van de
gepubliceerde broncode. Er is geen verplichte licentievergoeding voor die code.
Een volledige lokale HU3D-integratie vereist nog een adapter, verificatie van
de model-/pakketvoorwaarden en tests op geschikte loopdata. Gratis licentie
betekent geen gratis ontwikkeltijd, onbeperkte hosted dienst of bewezen
meetnauwkeurigheid.

## Technische proef met de bestaande HU3D-route — 12 oktober 2026

Vervolgonderzoek: [aansluitingsproef met echte marker-augmenter-uitvoer](examples/opencap-grf/cloud-connection/README.md)
en [verfijnde controle met voetcontactgeometrie, gezamenlijk COP en zwaartepunt](examples/opencap-grf/refined-contact/README.md).
Die laatste controle vervangt de markerdriehoek als belangrijkste geometrische
plausibiliteitscontrole en beoordeelt lage-krachtfasen afzonderlijk.
Daarop volgt de [controle van contactovergangen, hysterese en lage-krachtfasen](examples/opencap-grf/contact-transitions/README.md),
met diagnostische faselabels en afzonderlijke venster-/drempelgevoeligheid.

De bestaande gebruikersbestanden `model.osim`, `motion.mot` en
`ground_reaction_forces.mot` uit de eerdere inverse-dynamicaproef zijn gebruikt.
De beweging is een **evenwichtsoefening**, geen loopproef. Dit onderzoek test
bestand- en rekencompatibiliteit, niet de nauwkeurigheid van loopdynamica.
De SHA256-hashes van de bronbestanden zijn voor en na gecontroleerd: de
oorspronkelijke bestanden zijn ongewijzigd.

### Wat werkelijk is uitgevoerd

| Proef | Uitkomst |
| --- | --- |
| Bestaand OpenSim-model en beweging laden | Geslaagd, OpenSim 4.6; 96 tijdstappen, 23 onafhankelijke bewegingscoördinaten plus twee gekoppelde patellacoördinaten in het model |
| Krachtsbestand aansluiten | 159 rijen; rechter-/linkervoetkolommen en momentkolommen expliciet hernoemd zonder numerieke wijzigingen |
| COP-checkpoint standaard laden | Mislukt met PyTorch 2.8: standaard `weights_only` accepteert de opgeslagen NumPy-objecten niet |
| COP-checkpoint met beperkte NumPy-allowlist laden | Geslaagd; `weights_only=True` blijft actief, geen algemene pickle-vrijgave |
| COP-inferentie op CPU | Geslaagd: 159 eindige uitvoerregels; uitsluitend geometrische voetproxies gebruikt |
| Eenheids-/translatieproef | Gepubliceerde code reageert op 0,25 m verplaatsing met slechts 0,00025 m COP-verplaatsing |
| Geïsoleerde eenheidscorrectie | Reactie wordt 0,25 m; geen correctie in app of upstream doorgevoerd |
| Upstream voet-grondcontacten aan model toevoegen | Mislukt: `get_attached_geometry(0)` vindt geen geometrie/schaalinformatie op de betrokken voetbody |
| Volledige OpenSimAD-optimalisatie | Niet uitgevoerd; modeladapter en simulatieomgeving zijn nog nodig |

Ons gereduceerde model heeft **nul markers, nul spieren en nul krachtelementen**.
Voor de COP-techniekproef zijn daarom de oorsprongen van `calcn_r`, `toes_r`,
`calcn_l` en `toes_l` met forward kinematics berekend en als tijdelijke
`r_calc`, `r_toe`, `L_calc`, `L_toe` aangeboden. Dit zijn geen gemeten anatomische
hiel-/teenmarkers. De berekende COP is daarom geen bruikbaar nieuw meetresultaat.
De markerproxies dienen alleen om de code en eenheidsafhandeling te onderzoeken.
Hun onderlinge voetafstand is circa 0,174 m.

### Eenheidsfout concreet gereproduceerd

De COP-code vermenigvuldigt TRC-coördinaten eerst met 1.000 en deelt ze later
door 1.000.000. Voor onze TRC-invoer in meters wordt de geometrie daarmee een
factor **1.000 te klein**. `safe_interp1` voegt geen verborgen schaalfactor toe.
Het checkpoint bevat voor de voetlengte een trainingsgemiddelde van circa
0,211 m en een standaardafwijking van circa 0,0144 m, wat ook niet past bij de
verkleinde invoer.

In een tweede proef zijn alle voetpunten 0,25 m langs X verschoven. De gepubliceerde
code verplaatst beide COP-uitkomsten slechts 0,00025 m. Een tijdelijke variant
die bij invoer in meters de deler 1.000.000 vervangt door 1.000, verplaatst beide
uitkomsten 0,25 m. Hiermee is de schaalfout voor onze invoer aangetoond, zonder
te claimen dat alle oorspronkelijke OpenCap-varianten dezelfde invoereenheid
gebruiken. Een productieadapter moet eenheden expliciet lezen en controleren.

De standaard COP-code verandert alleen de **X-component** van het drukpunt.
Y, Z, krachten en momentkolommen zijn in deze proef behouden. Het is dus geen
volledige nieuwe driedimensionale COP-voorspelling. De looprichting en de
sagittale as moeten vóór gebruik correct worden uitgelijnd.

Verder verwijdert `predict_cop_for_trial` na het schrijven het aangeleverde
krachtsbestand. Dit is daadwerkelijk waargenomen op onze wegwerpkopieën.
HU3D moet een eigen uitvoerbestand gebruiken en de oorspronkelijke krachten
behouden. Ook loopt de huidige interpolatie door buiten steunfasen; zulke COP
moet als niet bruikbaar worden gemarkeerd in plaats van als voetcontactmeting
worden geïnterpreteerd.

### Waarom de standaard simulatie nog niet aansluit

De voetcontactgenerator gebruikt de schaalfactoren van de eerste aangehechte
voetgeometrie. Ons numerieke model heeft die geometrie niet. De geïsoleerde
upstreamroutine is op een kopie uitgevoerd en stopt op deze aanname.

Het standaard `walking_grf`-profiel verwacht bovendien tien arm-/elleboog- en
onderarmcoördinaten die ons gereduceerde model niet heeft. De normale volledige
voorbereidingsroute past ook spierwrapping van onder andere `iliacus_r` en
`psoas_r` aan; ons model bevat geen spieren. Hoewel een torque-driven variant
mogelijk is, zijn de standaard voorbereiding en instellingen dus niet direct
geschikt voor ons model.

CasADi en verschillende aanvullende simulatiepakketten ontbreken in de gebruikte
OpenSim-omgeving. Er zijn geen nieuwe pakketten geïnstalleerd, geen OpenCap-data
gedownload en geen bestanden naar de hosted GaitDynamics-dienst gestuurd.
Een gecompileerde OpenSimAD-runtime en een aangepaste volledige optimalisatie
zijn niet getest, evenmin als uitvoering van deze kandidaat op Windows/WSL2.

### Aanbevolen bouwvolgorde

1. Maak een adapter met expliciete meter-/asconventies, tijdscontrole,
   rechter-/linkervoettoewijzing en behoud van originele krachtsbestanden.
2. Gebruik de oorspronkelijke Pose2Sim-markertrajecten en een model met
   passende anatomische voetmarkers. Zet de COP-eenheden en checkpoint-loader
   goed; toets de uitkomst op langere loopdata en complete steunfasen.
3. Bouw eerst een torque-driven simulatie met expliciete voetcontactgeometrie
   en instellingen voor ons model. Hergebruik geen mesh-schaalwaarden zonder
   geometrische verificatie. Controleer ook segmentmassa's en traagheid.
4. Beoordeel daarna een spiergedreven route op een daarvoor geschikt geschaald
   model, in plaats van spieren stilzwijgend aan het gereduceerde model toe te
   kennen. Gewrichtscontactkrachten vereisen extra aandacht voor spierbijdragen.
5. Vergelijk eerst met de eerdere inverse-dynamica-uitkomst en vervolgens met
   loopdata met gemeten krachten. Pas daarna opnemen in de app.

**Besluit na de proef:** technisch kansrijk als aanvullende analysemodule,
maar geen directe uitbreiding die we met de huidige bestanden al betrouwbaar
kunnen aanbieden. COP-inferentie is lokaal aangetoond; de volledige hybride
simulatie en biomechanische nauwkeurigheid zijn nog niet aangetoond.

### Bewijs en herhaalbaarheid

- [Invoer- en modelcontrole](examples/opencap-grf/preparation.json).
- [COP- en translatieproeven](examples/opencap-grf/compatibility.json).
- [Simulatievoorbereiding en foutdetails](examples/opencap-grf/simulation-preflight.json).
- [Invoer voorbereiden](examples/opencap-grf/prepare.py),
  [COP-proef](examples/opencap-grf/probe.py) en
  [contactvoorbereiding](examples/opencap-grf/preflight.py).

De scripts zijn onderzoeksbestanden, geen appfuncties. Geef `prepare.py` een
`--inputs`-map met de drie oorspronkelijke bestanden en een nieuwe `--output`-map.
De andere scripts gebruiken dezelfde argumenten, plus `--candidate` met een
checkout van de hierboven vastgelegde upstreamcommit. Voer `prepare.py` en
`preflight.py` uit met OpenSim/NumPy, en `probe.py` met PyTorch/NumPy/pandas/SciPy.
Gebruik uitsluitend een nieuwe wegwerp-uitvoermap: de upstreamroutine verwijdert
haar invoerkopie, en deze scripts mogen geen bestaande resultaatmap vervangen.
De geïsoleerde eenheidsvariant is alleen een diagnostische vergelijking.
