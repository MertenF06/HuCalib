# Commerciële rechten: ForcePose met Sports2D

Onderzocht op 11 oktober 2026. Dit document legt een onderzoek van openbare
bronnen vast; het is geen toestemming van de rechthebbenden en geen juridisch
advies. Er is geen ForcePose-code of model aan de app toegevoegd. Auteurs zijn
niet benaderd.

## Conclusie voor HU3D kinematics

ForcePose sluit technisch aan op een onderzoek naar GRF-schatting uit 2D
keypoints. De volledige gepubliceerde implementatie is op basis van de
onderzochte bronnen **nog niet commercieel vrijgegeven**. De MIT-licentie aan
de bovenkant van de repository is daarvoor onvoldoende: aantoonbaar overgenomen
VideoPose3D-code heeft bij de oorspronkelijke bron een niet-commerciële licentie.
Voor PoseFormer-bijdragen, extern aangeboden gewichten en trainingsdata blijven
afzonderlijke rechtenvragen open.

Dat is geen vaststelling dat de ForcePose-auteurs geen aanvullende toestemming
hebben. Zulke toestemming is in de onderzochte bestanden niet aangetoond.

## Bevindingen per onderdeel

| Onderdeel | Vastgesteld | Gevolg voor commercieel gebruik |
| --- | --- | --- |
| ForcePose-hoofdrepository | MIT, copyright MichiganCOG 2022 | MIT staat verkoop, gebruik en wijzigingen toe, met behoud van de copyright- en licentietekst. De verlener moet wel bevoegd zijn voor de betrokken code. |
| Overgenomen VideoPose3D-code | Oorspronkelijke licentie CC BY-NC 4.0; identieke en gewijzigde bestanden in ForcePose | Deze oorspronkelijke licentie geeft geen toestemming voor commercieel gebruik. Aanvullende toestemming of vervanging van de betrokken implementatie is nodig. |
| PoseFormer | README noemt VideoPose3D als basis; in de onderzochte repositoryversie is geen LICENSE/LICENCE/COPYING gevonden, ook geen expliciete commerciële toestemming in de README | Openbaar op GitHub is geen zelfstandige commerciële licentie. Herkomst en toestemming voor de betrokken eigen bijdragen moeten worden opgehelderd. |
| timm 0.4.12 | Apache 2.0; genoemd als basis voor transformer-code | Commercieel gebruik is toegestaan onder de licentievoorwaarden. Dit lost rechten op wijzigingen door andere partijen niet op. |
| ForcePose-gewichten | README verwijst naar extern archief met `2d_81frames.bin` en `2d_81frames_t2.bin` | Commerciële inferentie en herdistributie niet bevestigd. De voorwaarden binnen het archief zijn niet gecontroleerd. |
| ForcePose-dataset | Extern aangeboden dataset en versie 1.1 | Commercieel trainen, fine-tunen en verspreiden niet bevestigd. Vrij downloaden betekent geen onbeperkte rechten. |
| Sports2D-code | BSD-3-Clause | Commercieel gebruik toegestaan met behoud van notices en disclaimer; namen van auteurs mogen niet zonder toestemming als aanbeveling worden gebruikt. |
| Door Sports2D vastgepinde Pose2Sim-code | BSD-3-Clause | Eveneens commercieel bruikbaar onder die voorwaarden; aparte afhankelijkheden en gebundelde modellen blijven relevant. |
| RTMLib-code | Apache 2.0 | Commercieel bruikbaar onder de voorwaarden; dit bewijst geen licentie op ieder aangeboden ONNX-checkpoint. |
| Gekozen 2D-detectiemodel | Geen specifiek checkpoint voor deze koppeling geselecteerd en doorgelicht | Nog afzonderlijk controleren: modellicentie, herkomst en eventueel opgelegde gebruiksvoorwaarden. |

BSD en Apache zijn geen non-commercial licenties. Zij hebben wel verplichtingen
bij verspreiding. Voor Apache zijn onder andere licentietekst, toepasselijke
NOTICE-informatie en het aangeven van gewijzigde bestanden relevant.

Dit is geen volledige audit van alle transitieve Python-, Qt-, OpenSim- en
systeemafhankelijkheden van een Windows-product. Ook een commercieel bruikbare
Sports2D-hoofdlicentie bewijst niet dat ieder pakket of model in een gekozen
installatie onder dezelfde licentie valt.

## Controleerbaar bewijs voor het VideoPose3D-probleem

Onderzochte versies:

| Repository | Commit |
| --- | --- |
| MichiganCOG/ForcePose | `1bfa0c477b00e7e940ab6419fcea121840a342a7` |
| facebookresearch/VideoPose3D | `1afb1ca0f1237776518469876342fc8669d3f6a9` |
| zczcwh/PoseFormer | `a908b29adca354b2e0acd36606229ca19b6a5e3c` |
| davidpagnon/Sports2D | `0cc325a3f2a5a16e4e61a194fc5eb208704a3542` |
| perfanalytics/Pose2Sim, afhankelijkheid in Sports2D | `5bce3d82096200ce248f17211b209b3723f9091a` |
| Tau-J/rtmlib | `03a1693e59e4f7cd84582c0fb30459b3bf18ad42` |

`common/camera.py` is byte voor byte identiek in de onderzochte ForcePose- en
VideoPose3D-versies. Beide bestanden zijn 2.756 bytes en hebben SHA256:

```text
91246cbf7b05d61d222ea7bb934ccf37c7ecae512236ed9a6944b0412d8f8726
```

19 van de 20 Python-bestanden in ForcePose bevatten een Facebook-copyrightheader.
Die telling alleen bewijst niet de herkomst van iedere regel; de volledige
bytevergelijking van `camera.py` is sterker bewijs. Ook `generators.py` en
`utils.py` dragen die header, maar verschillen van de onderzochte upstreamversie.

Dit betreft gebruikte code: `run_poseformer_force.py` importeert onder andere
`common.camera`, `common.generators` en `common.model_poseformer`.
`camera.py` importeert weer `utils` en `quaternion`. Alleen één camerabestand
vervangen verhelpt dus niet de hele herkomstkwestie. Een integratie moet alle
gebruikte imports en hun afhankelijkheden nalopen.

Bronnen:

- [ForcePose MIT-licentie](https://github.com/MichiganCOG/ForcePose/blob/1bfa0c477b00e7e940ab6419fcea121840a342a7/LICENSE).
- [ForcePose camera.py](https://github.com/MichiganCOG/ForcePose/blob/1bfa0c477b00e7e940ab6419fcea121840a342a7/common/camera.py).
- [VideoPose3D camera.py](https://github.com/facebookresearch/VideoPose3D/blob/1afb1ca0f1237776518469876342fc8669d3f6a9/common/camera.py).
- [Oorspronkelijke VideoPose3D-licentie](https://github.com/facebookresearch/VideoPose3D/blob/1afb1ca0f1237776518469876342fc8669d3f6a9/LICENSE).
- [ForcePose-inferentie en imports](https://github.com/MichiganCOG/ForcePose/blob/1bfa0c477b00e7e940ab6419fcea121840a342a7/run_poseformer_force.py).
- [ForcePose transformer-code en vermelde timm-herkomst](https://github.com/MichiganCOG/ForcePose/blob/1bfa0c477b00e7e940ab6419fcea121840a342a7/common/model_poseformer.py).
- [PoseFormer repositoryversie](https://github.com/zczcwh/PoseFormer/tree/a908b29adca354b2e0acd36606229ca19b6a5e3c) en [README](https://github.com/zczcwh/PoseFormer/blob/a908b29adca354b2e0acd36606229ca19b6a5e3c/README.md).
- [timm 0.4.12-licentie](https://github.com/rwightman/pytorch-image-models/blob/v0.4.12/LICENSE).

## Gewichten en data: wat nog niet bewezen is

De [ForcePose README](https://github.com/MichiganCOG/ForcePose/blob/1bfa0c477b00e7e940ab6419fcea121840a342a7/README.md)
verwijst naar:

- Gewichten: `https://prism.eecs.umich.edu/natlouis/forcepose/coco_2d.tar.gz`.
- Data: `https://prism.eecs.umich.edu/natlouis/forcepose/forcepose.tar.gz`.
- Bijgewerkte data: `https://prism.eecs.umich.edu/natlouis/forcepose/forcepose_1.1.tar.gz`.

De netwerkproxy van deze onderzoeksomgeving blokkeerde toegang tot deze host
met HTTP 403. Er is daarom geen archief uitgepakt en geen daarin opgenomen
licentie vastgesteld. GitHub-code en licenties waren wel toegankelijk.
De GitHub-issuespagina leverde geen bruikbare discussieresultaten op; daaruit
wordt geen conclusie getrokken over eventuele verklaringen van de auteurs.

**Onbekend** is hier iets anders dan **verboden**. Andersom mag de MIT-licentie
van broncode niet zonder onderbouwing worden toegepast op extern aangeboden
datasets of checkpoints. Voor een distributie moet de daadwerkelijk gekozen
versie worden vastgelegd, inclusief archiefhash en bijbehorende voorwaarden.

De licentie van trainingsdata, de rechten op een getraind model en de
gebruiksvoorwaarden voor een checkpoint zijn verschillende vragen. Uit alleen
een datalicentie volgt niet automatisch een definitief oordeel over alle rechten
op getrainde modellen; dat moet concreet worden onderzocht.

## Sports2D-koppeling

Bronnen:

- [Sports2D BSD-3-Clause](https://github.com/davidpagnon/Sports2D/blob/0cc325a3f2a5a16e4e61a194fc5eb208704a3542/LICENSE).
- [Sports2D afhankelijkheden](https://github.com/davidpagnon/Sports2D/blob/0cc325a3f2a5a16e4e61a194fc5eb208704a3542/pyproject.toml).
- [Pose2Sim BSD-3-Clause op de vastgepinde versie](https://github.com/perfanalytics/Pose2Sim/blob/5bce3d82096200ce248f17211b209b3723f9091a/LICENSE).
- [Pose2Sim afhankelijkheden](https://github.com/perfanalytics/Pose2Sim/blob/5bce3d82096200ce248f17211b209b3723f9091a/pyproject.toml).
- [RTMLib Apache 2.0](https://github.com/Tau-J/rtmlib/blob/03a1693e59e4f7cd84582c0fb30459b3bf18ad42/LICENSE).

ForcePose gebruikt voor deze architectuur 17 COCO-keypoints met twee
beeldcoördinaten. Een adapter naar geschikte Sports2D-uitvoer kan de eigen
ForcePose-detectorroute vervangen. Daardoor hoeft niet automatisch ieder model
dat de ForcePose-auteurs voor hun dataset gebruikten in ons product terecht te
komen. De gekozen Sports2D-detector en het ForcePose-GRF-checkpoint hebben nog
steeds eigen rechtenvragen. Deze adapter is in dit onderzoek niet gebouwd of
gevalideerd.

De 81 frames zijn temporele context, geen maximum voor de videolengte. Dit
technische gegeven verandert de licentievoorwaarden niet.

## Wat schriftelijk bevestigd moet worden

Onderstaande vragen zijn een concept voor een rechtenverzoek; ze zijn niet
verstuurd. Een bevestiging moet de exacte code- en modelversies benoemen en
afkomstig zijn van een bevoegde rechthebbende.

1. Mag de ForcePose-implementatie worden gebruikt in een betaalde Windows-app
   en in een betaalde hosted service? Omvat toestemming ook de overgenomen
   VideoPose3D-code en de PoseFormer-bijdragen, en op grond van welke machtiging?
2. Mag commerciële inferentie plaatsvinden met `2d_81frames.bin` en
   `2d_81frames_t2.bin`? Welke licentie geldt voor deze checkpoints?
3. Mogen de checkpoints worden meegeleverd, opnieuw aangeboden, geconverteerd
   naar ONNX of gekwantiseerd? Welke notices moeten meegeleverd worden?
4. Mag de ForcePose-dataset commercieel worden gebruikt voor training en
   fine-tuning? Mag een daarmee getraind model commercieel worden verspreid?
   Welke voorwaarden gelden voor data van proefpersonen en derden?
5. Is er een rechtenoverzicht of afzonderlijke licentie die deze vragen al
   afdekt? Wie kan eventueel vereiste toestemming van Facebook/Meta of de
   PoseFormer-rechthebbenden verlenen?

Naamsvermelding, een paper citeren of de auteurs bedanken vervangt geen
commerciële toestemming. Een betaalde dienst die de code alleen op een server
draait, omzeilt een beperking op commercieel gebruik evenmin. Een eigen wrapper
of gebruikersinterface verandert de rechten op de onderliggende code niet.

## Advies en mogelijke routes

**Eerste route: rechten ophelderen voordat ForcePose wordt ingebouwd in een
commercieel product.** Bewaar ontvangen toestemming, versienummers, voorwaarden
en notices samen met een dependency-overzicht. Een losse bevestiging dat de
hoofdrepository MIT is, beantwoordt het aangetoonde upstreamprobleem niet.

**Tweede route: onafhankelijk implementeren en een toegestaan checkpoint
gebruiken.** Een eigen implementatie op basis van gepubliceerde methoden kan
het kopiëren van de betrokken broncode vermijden. Dit moet werkelijk zelfstandig
gebeuren en is geen automatische vrijgave van de bestaande gewichten. Ook andere
rechten, waaronder eventuele octrooien, zijn hiermee niet onderzocht.

**Derde route: eigen model met geschikt gelicentieerde gepaarde kinematica- en
krachtdata.** Dit geeft meer controle over de herkomst, maar vereist een nieuwe
training en validatie. Een permissieve transformer-implementatie alleen maakt
een dataset nog niet commercieel bruikbaar.

**Alternatief: eigen 2D-fysische GRF-schatting.** Daarmee kan afhankelijkheid van
ForcePose-gewichten worden vermeden. Verdeling over twee voeten en het
aangrijpingspunt van de kracht vereisen wel aanvullende aannames of metingen.
Heel strike en toe off identificeren contactfasen, maar bepalen die krachten
niet volledig. Betrouwbaarheid en licentiezekerheid zijn afzonderlijke criteria.

Voor HU3D kinematics is de aanbeveling om ForcePose voorlopig als technische
onderzoeksoptie te behandelen en geen commerciële beschikbaarheid te beloven.
De openbare licentiebevindingen zijn hierboven vastgelegd; de modelarchieven,
schriftelijke toestemmingen en de uiteindelijke productafhankelijkheden moeten
nog worden beoordeeld.
