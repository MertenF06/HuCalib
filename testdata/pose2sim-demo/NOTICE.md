# Herkomst van de demokalibratie

`Calib_qualisys.toml` is de officiële Qualisys-calibratie van Pose2Sim
`Demo_SinglePerson`, geconverteerd naar TOML met Pose2Sim op commit
`68304b47195dac80bc341cdfd9669140609358c4`.

Bron: https://github.com/perfanalytics/pose2sim/tree/68304b47195dac80bc341cdfd9669140609358c4/Pose2Sim/Demo_SinglePerson

De bijbehorende BSD-licentie staat in `LICENSE-Pose2Sim`. De video's en het
instellingenbestand worden op diezelfde commit gedownload, niet vanuit een
veranderlijke main-branch. Bekende afwijking: twee video's zijn 1080 pixels
breed, terwijl de kalibratie 1088 vermeldt. De testkit past deze parameters
niet aan. Dit is testdata, geen persoonlijk meetprotocol.
