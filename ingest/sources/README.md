# EU public tender sources

Registry of procurement sources for the SalesPatriot challenge. The source of truth is `tender-sources.csv`; this file is generated from it.

- **Tier**: 1 = build first, 2 = next, 3 = backfill / subcontracting.
- **Defence**: `yes` = defence-specific, `dual` = dual-use, `partial` = general portal that carries some defence notices.
- **Reachability** was checked with curl on 2026-10-30. "Blocks bots" and "geoblocked" sites load in a normal browser but need a headless browser or proxy to scrape.

125 sources.

## EU-level and multinational

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [TED - Tenders Electronic Daily](https://ted.europa.eu/) | EU portal | api | partial | All EU/EEA above-threshold notices incl. Directive 2009/81 defence; eForms XML; API v3 at api.ted.europa.eu |
| 1 | [TED API v3](https://api.ted.europa.eu/) | EU portal | api | partial | Anonymous search endpoint; no key needed for search |
| 2 | [EU Funding & Tenders Portal](https://ec.europa.eu/info/funding-tenders/opportunities/portal/) | EU portal | api | yes | EDF / EDIP / ASAP calls (grants) plus EU institution tenders |
| 2 | [European Defence Agency - procurement](https://eda.europa.eu/procurement) | Intl defence body | scrape | yes | EDA's own studies and service contracts; blocks bots |
| 2 | [OCCAR](https://www.occar.int/) | Intl defence body | scrape | yes | Joint armaments programmes (A400M; Boxer; FREMM; Eurodrone); business opportunities section; blocks bots |
| 2 | [EUSPA - EU Space Programme Agency procurement](https://www.euspa.europa.eu/opportunities/procurement) | Intl defence body | scrape | dual | Galileo; GOVSATCOM; IRIS2 related |
| 2 | [ESA - esa-star](https://esastar-publication.sso.esa.int/) | Intl defence body | registration | dual | ESA Invitations to Tender |
| 3 | [opentender.eu](https://opentender.eu/) | Dataset | open_data | partial | Historic aggregated tender data for ~35 jurisdictions; good for backfill and parser validation; blocks bots |
| 3 | [Open Contracting Data Registry](https://data.open-contracting.org/) | Dataset | open_data | partial | Lists publishers that release OCDS data |
| 3 | [Rheinmetall supplier portal](https://www.rheinmetall.com/en/suppliers) | Prime / supplier portal | scrape | yes | Central digital procurement portal; subsidiaries have own supplier pages |
| 3 | [KNDS](https://knds.com/) | Prime / supplier portal | scrape | yes |  |
| 3 | [Saab suppliers](https://www.saab.com/about/for-suppliers) | Prime / supplier portal | scrape | yes |  |
| 3 | [Leonardo suppliers](https://www.leonardo.com/en/suppliers) | Prime / supplier portal | scrape | yes |  |
| 3 | [Thales suppliers](https://www.thalesgroup.com/en/suppliers) | Prime / supplier portal | scrape | yes |  |
| 3 | [Airbus suppliers](https://www.airbus.com/en/becoming-an-airbus-supplier) | Prime / supplier portal | scrape | yes | Airbus Partner Portal; Defence and Space division |
| 3 | [MBDA](https://www.mbda-systems.com/) | Prime / supplier portal | scrape | yes |  |
| 3 | [Hensoldt](https://www.hensoldt.net/) | Prime / supplier portal | scrape | yes |  |
| 3 | [Kongsberg](https://www.kongsberg.com/) | Prime / supplier portal | scrape | yes |  |
| 3 | [Nammo](https://www.nammo.com/) | Prime / supplier portal | scrape | yes |  |

## NATO

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [NSPA - NATO Support and Procurement Agency](https://www.nspa.nato.int/business/procurement) | Intl defence body | registration | yes | Source file registration via national delegation required to bid; blocks bots |
| 1 | [NCIA - NATO Communications and Information Agency](https://www.ncia.nato.int/business) | Intl defence body | registration | yes | IFIB/NOI notices; Basic Ordering Agreements; blocks bots |
| 2 | [DIANA - Defence Innovation Accelerator](https://www.diana.nato.int/) | Intl defence body | scrape | yes | Challenge calls for startups; blocks bots |

## Poland

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [Agencja Uzbrojenia (Armament Agency)](https://www.wojsko-polskie.pl/au/) | Defence agency | scrape | yes | Main Polish MoD buyer; many contracts exempt under Art. 346; its large tenders also show up on TED under buyer "Agencja Uzbrojenia" |
| 1 | [platformazakupowa.pl](https://platformazakupowa.pl/) | e-procurement platform | scrape | yes | Used by many military units and RBLog logistics bases; holds documents and Q&A |
| 1 | [e-Zamowienia / BZP](https://ezamowienia.gov.pl/) | National portal | open_data | partial | Biuletyn Zamowien Publicznych; below-threshold notices; open data and API |
| 2 | [Inspektorat Wsparcia Sil Zbrojnych](https://www.wojsko-polskie.pl/iwspsz/) | Defence agency | scrape | yes | Armed Forces Support Inspectorate; logistics procurement |
| 2 | [1 Regionalna Baza Logistyczna](https://1rblog.wp.mil.pl/) | Defence agency | scrape | yes | Regional logistics base; RBLog 1-4 publish tenders |
| 2 | [2 Regionalna Baza Logistyczna](https://2rblog.wp.mil.pl/) | Defence agency | scrape | yes |  |
| 2 | [3 Regionalna Baza Logistyczna](https://3rblog.wp.mil.pl/) | Defence agency | scrape | yes |  |
| 2 | [4 Regionalna Baza Logistyczna](https://4rblog.wp.mil.pl/) | Defence agency | scrape | yes |  |
| 2 | [Agencja Mienia Wojskowego (AMW)](https://amw.com.pl/) | Defence agency | scrape | yes | Military property agency; also runs procurement |
| 2 | [Rzadowa Agencja Rezerw Strategicznych (RARS)](https://www.gov.pl/web/rars) | Defence agency | scrape | dual | Strategic reserves agency |
| 2 | [Narodowe Centrum Badan i Rozwoju (NCBR)](https://www.gov.pl/web/ncbr) | Defence agency | scrape | dual | Defence and security R&D programmes |
| 2 | [SmartPZP](https://portal.smartpzp.pl/) | e-procurement platform | scrape | partial |  |
| 2 | [Open Nexus / Marketplanet](https://platformazakupowa.marketplanet.pl/) | e-procurement platform | scrape | partial | Used by several PGZ companies |
| 2 | [Logintrade](https://www.logintrade.pl/) | e-procurement platform | scrape | partial |  |
| 2 | [eB2B](https://platforma.eb2b.com.pl/) | e-procurement platform | scrape | partial |  |
| 2 | [Polska Grupa Zbrojeniowa (PGZ) - procurement](https://grupapgz.pl/) | Prime / supplier portal | scrape | yes | State defence group; subsidiaries publish own purchase notices |
| 2 | [Huta Stalowa Wola - przetargi](https://www.hsw.pl/) | Prime / supplier portal | scrape | yes | PGZ artillery/vehicles |
| 2 | [WB Group](https://www.wbgroup.pl/) | Prime / supplier portal | scrape | yes | Private Polish defence prime |

## Germany

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [BAAINBw (Bundeswehr equipment office)](https://www.baainbw.de/) | Defence agency | scrape | yes | Main German defence procurement office; timed out / geoblocked |
| 1 | [e-Vergabe Bund (evergabe-online)](https://www.evergabe-online.de/) | National portal | scrape | yes | Federal portal; used by BAAINBw; needs cookies |
| 1 | [Bekanntmachungsservice (oeffentlichevergabe.de)](https://oeffentlichevergabe.de/) | National portal | api | partial | National notice data service with open API (eForms-DE) |
| 2 | [BAIUDBw (Bundeswehr infrastructure office)](https://www.bundeswehr.de/de/organisation/infrastruktur-umweltschutz-und-dienstleistungen) | Defence agency | scrape | yes |  |
| 2 | [BwI / BWI GmbH](https://www.bwi.de/) | Defence agency | scrape | yes | Bundeswehr IT service provider |
| 2 | [Cyber Innovation Hub der Bundeswehr](https://www.cyberinnovationhub.de/) | Defence agency | scrape | yes | Startup challenges |
| 2 | [DTVP - Deutsches Vergabeportal](https://www.dtvp.de/) | e-procurement platform | scrape | partial |  |
| 2 | [Vergabemarktplatz NRW](https://www.evergabe.nrw.de/) | e-procurement platform | scrape | partial |  |
| 2 | [Vergabe Bayern](https://www.vergabe.bayern.de/) | e-procurement platform | scrape | partial |  |
| 2 | [subreport ELViS](https://www.subreport.de/) | e-procurement platform | scrape | partial |  |
| 2 | [service.bund.de - Ausschreibungen](https://www.service.bund.de/Content/DE/Ausschreibungen/Suche/Formular.html) | National portal | rss | partial | Federal tender search; RSS |

## France

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [DGA - Direction generale de l'armement](https://www.defense.gouv.fr/dga) | Defence agency | scrape | yes |  |
| 1 | [BOAMP](https://www.boamp.fr/) | National portal | api | partial | Open data API at boamp-datadila.opendatasoft.com |
| 1 | [PLACE - Plateforme des achats de l'Etat](https://www.marches-publics.gouv.fr/) | National portal | scrape | yes | Used by Ministere des Armees / DGA |
| 2 | [Achats du ministere des Armees](https://achats.defense.gouv.fr/) | Defence agency | scrape | yes | Official MoD purchasing portal: tenders; purchase forecasts; RFIs |
| 2 | [AID - Agence de l'innovation de defense](https://www.defense.gouv.fr/aid) | Defence agency | scrape | yes | Innovation calls |
| 2 | [DMAe - aeronautical maintenance](https://www.defense.gouv.fr/dmae) | Defence agency | scrape | yes |  |
| 2 | [AWS-Achat / Maximilien / megalis (regional)](https://www.marches-publics.info/) | e-procurement platform | scrape | partial | Regional/local buyers |

## Italy

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [Segretariato Generale della Difesa / DNA](https://www.difesa.it/sgd-dna) | Defence agency | scrape | yes | National Armaments Directorate; Teledife / Armaereo / Navarm / Terrarm bandi |
| 1 | [ANAC - Banca Dati Nazionale Contratti Pubblici](https://dati.anticorruzione.it/opendata) | National portal | open_data | partial | OCDS open data |
| 2 | [Agenzia Industrie Difesa](https://www.aid.difesa.it/) | Defence agency | scrape | yes | timed out / geoblocked |
| 2 | [Acquisti in Rete PA (Consip)](https://www.acquistinretepa.it/) | National portal | scrape | partial |  |

## Spain

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [PLACSP - Plataforma de Contratacion del Sector Publico](https://contrataciondelestado.es/) | National portal | open_data | partial | ATOM/OCDS feeds; Ministerio de Defensa publishes here; timed out / geoblocked |
| 2 | [DGAM - Direccion General de Armamento y Material](https://www.defensa.gob.es/) | Defence agency | scrape | yes | Tenders issued by Subdireccion General de Adquisiciones de Armamento y Material; published on PLACSP and BOE |
| 2 | [ISDEFE](https://www.isdefe.es/) | Defence agency | scrape | yes | State defence engineering company; blocks bots |

## Netherlands

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [COMMIT (formerly DMO) - Defensie inkoop](https://www.defensie.nl/onderwerpen/z/zakendoen-met-defensie/informatie-voor-leveranciers) | Defence agency | scrape | yes | COMMIT Directie Inkoop and DOSCO buy; tenders published on TenderNed |
| 1 | [TenderNed](https://www.tenderned.nl/) | National portal | open_data | partial | Open data dumps |
| 2 | [Mercell / Negometrix](https://www.mercell.com/) | e-procurement platform | registration | partial | Used by NL MoD and Nordic buyers |

## Belgium

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [e-Procurement / Public Procurement](https://www.publicprocurement.be/) | National portal | scrape | partial | BOSA e-Notification |
| 2 | [Defence - DG Material Resources](https://www.mil.be/) | Defence agency | scrape | yes |  |

## Luxembourg

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Direction de la Defense](https://defense.gouvernement.lu/) | Defence agency | scrape | yes |  |
| 2 | [Portail des marches publics](https://pmp.b2g.etat.lu/) | National portal | scrape | partial |  |

## Austria

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Bundesheer / BMLV](https://www.bundesheer.at/) | Defence agency | scrape | yes |  |
| 2 | [USP / data.gv.at Kerndaten](https://www.data.gv.at/) | National portal | open_data | partial | Federal notice metadata as open data |
| 2 | [BBG - Bundesbeschaffung](https://www.bbg.gv.at/) | National portal | scrape | partial | Central purchasing body |

## Ireland

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Department of Defence](https://www.gov.ie/en/organisation/department-of-defence/) | Defence agency | scrape | yes | blocks bots |
| 2 | [eTenders](https://www.etenders.gov.ie/) | National portal | scrape | partial |  |

## Portugal

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [DGRDN - Direcao-Geral de Recursos da Defesa Nacional](https://www.defesa.gov.pt/pt/defesa/organizacao/sc/dgrdn) | Defence agency | scrape | yes |  |
| 2 | [Vortal](https://www.vortal.biz/) | e-procurement platform | registration | partial |  |
| 2 | [BASE - Portal dos Contratos Publicos](https://www.base.gov.pt/) | National portal | open_data | partial |  |

## Greece

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [GDDIE - Defence Investments and Armaments](https://www.mod.mil.gr/) | Defence agency | scrape | yes | blocks bots |
| 2 | [KIMDIS / ESIDIS](https://portal.eprocurement.gov.gr/) | National portal | scrape | partial |  |

## Cyprus

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [eProcurement Cyprus](https://www.eprocurement.gov.cy/) | National portal | scrape | partial |  |

## Malta

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [eTenders Malta](https://www.etenders.gov.mt/) | National portal | scrape | partial |  |

## Czechia

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [Ministerstvo obrany - zakazky](https://www.mocr.army.cz/) | Defence agency | scrape | yes | Armament and Acquisition Division; timed out / geoblocked |
| 1 | [NEN - Narodni elektronicky nastroj](https://nen.nipez.cz/) | National portal | open_data | partial |  |
| 2 | [Vestnik verejnych zakazek](https://vvz.nipez.cz/) | National portal | open_data | partial |  |

## Slovakia

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Ministerstvo obrany SR](https://www.mosr.sk/) | Defence agency | scrape | yes |  |
| 2 | [Josephine](https://josephine.proebiz.com/) | e-procurement platform | scrape | partial |  |
| 2 | [UVO - Urad pre verejne obstaravanie](https://www.uvo.gov.sk/) | National portal | open_data | partial |  |

## Hungary

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Honvedelmi Miniszterium](https://honvedelem.hu/) | Defence agency | scrape | yes |  |
| 2 | [EKR - Elektronikus Kozbeszerzesi Rendszer](https://ekr.gov.hu/) | National portal | scrape | partial |  |

## Romania

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [SEAP / SICAP](https://www.e-licitatie.ro/) | National portal | scrape | partial |  |
| 2 | [Ministerul Apararii Nationale - achizitii](https://www.mapn.ro/) | Defence agency | scrape | yes |  |

## Bulgaria

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Ministry of Defence Bulgaria](https://www.mod.bg/) | Defence agency | scrape | yes |  |
| 2 | [CAIS EOP](https://app.eop.bg/) | National portal | scrape | partial |  |

## Croatia

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [MORH - Ministry of Defence](https://www.morh.hr/) | Defence agency | scrape | yes |  |
| 2 | [EOJN RH](https://eojn.hr/) | National portal | scrape | partial |  |

## Slovenia

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Ministrstvo za obrambo](https://www.gov.si/drzavni-organi/ministrstva/ministrstvo-za-obrambo/) | Defence agency | scrape | yes |  |
| 2 | [e-Narocanje](https://www.enarocanje.si/) | National portal | scrape | partial |  |

## Lithuania

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [Defence Resources Agency (Gynybos resursu agentura)](https://gra.lt/) | Defence agency | scrape | yes | timed out / geoblocked |
| 1 | [CVP IS](https://viesiejipirkimai.lt/) | National portal | scrape | partial |  |

## Latvia

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [VALIC - State Defence Logistics and Procurement Centre](https://www.valic.gov.lv/) | Defence agency | scrape | yes |  |
| 1 | [EIS / IUB](https://www.eis.gov.lv/) | National portal | scrape | partial |  |

## Estonia

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [ECDI - Estonian Centre for Defence Investment](https://www.kaitseinvesteeringud.ee/) | Defence agency | scrape | yes |  |
| 1 | [Riigihangete register](https://riigihanked.riik.ee/) | National portal | api | partial |  |

## Finland

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 1 | [Finnish Defence Forces Logistics Command](https://puolustusvoimat.fi/) | Defence agency | scrape | yes | blocks bots |
| 1 | [Hilma](https://www.hankintailmoitukset.fi/) | National portal | api | partial | Open API |

## Sweden

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [FMV - Forsvarets materielverk](https://www.fmv.se/) | Defence agency | scrape | yes | blocks bots |
| 2 | [e-Avrop](https://www.e-avrop.com/) | e-procurement platform | scrape | partial |  |
| 2 | [TendSign](https://tendsign.com/) | e-procurement platform | registration | partial |  |

## Denmark

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [FMI - Defence Acquisition and Logistics Organisation](https://www.fmi.dk/) | Defence agency | scrape | yes |  |
| 2 | [udbud.dk](https://udbud.dk/) | National portal | scrape | partial |  |

## Norway (EEA)

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Forsvarsmateriell (NDMA)](https://www.fma.no/) | Defence agency | scrape | yes |  |
| 2 | [Doffin](https://www.doffin.no/) | National portal | api | partial | EEA; new Doffin has API |

## United Kingdom

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Defence Sourcing Portal](https://www.contracts.mod.uk/) | Defence agency | registration | yes | DE&S / DIO tenders; timed out / geoblocked |
| 2 | [UK Defence Innovation (ex-DASA)](https://www.gov.uk/government/organisations/uk-defence-innovation) | Defence agency | scrape | yes |  |
| 2 | [Find a Tender](https://www.find-tender.service.gov.uk/) | National portal | api | partial | OCDS API |
| 2 | [Contracts Finder](https://www.contractsfinder.service.gov.uk/) | National portal | api | partial |  |

## Ukraine

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 2 | [Defence Procurement Agency (AOZ)](https://aoz.mod.gov.ua/) | Defence agency | scrape | yes | timed out / geoblocked |
| 2 | [State Logistics Operator (DOT)](https://dot.mod.gov.ua/) | Defence agency | scrape | yes | Non-lethal supplies; timed out / geoblocked |
| 2 | [Brave1](https://brave1.gov.ua/) | Defence agency | scrape | yes | Defence tech cluster grants |
| 2 | [Prozorro](https://prozorro.gov.ua/) | National portal | api | partial | Full open API |

## United States (for reference)

| Tier | Source | Type | Access | Defence | Notes |
|---|---|---|---|---|---|
| 3 | [DLA DIBBS](https://www.dibbs.bsm.dla.mil/) | Defence agency | scrape | yes | Defense Logistics Agency bid board |
| 3 | [SAM.gov](https://sam.gov/) | National portal | api | partial | SalesPatriot's home market |

---

# Complete lists mined from the data

The curated table above is the starting point. The files below are the exhaustive version, built from the data rather than from memory (scripts in `scripts/`, mined 2026-10-30).

| File | What it is | Rows |
|---|---|---|
| `ted-platforms.csv` | Every domain hosting tender documents or bid submission in a year of TED competition notices (448,180 notices, 30 Oct 2025 – 30 Oct 2026). `multi_buyer_platform` = used by 3+ buyers. | 2507 domains, 602 platforms |
| `ted-defence-buyers.csv` | Every buyer that published under the defence directive (2009/81/EC) or bought CPV 35* (security, defence, police, fire) on TED since 2023, with notice counts and the platforms it uses. | 9448 buyers in 51 countries |
| `bzp-platforms.csv` | Every platform running Polish below-threshold tenders, from 6 months of BZP notices (134,835 notices, May–Oct 2026). | 39 platforms |
| `bzp-defence-buyers.csv` | Polish military units, defence and security bodies buying below the EU threshold (matched on buyer name or CPV 35*). | 457 buyers |

## Polish platforms by share of below-threshold tenders

| Platform | Share | Buyers |
|---|---|---|
| ezamowienia.gov.pl | 37.0% | 5847 |
| platformazakupowa.pl | 31.5% | 2711 |
| ezamawiajacy.pl | 15.3% | 1258 |
| logintrade.net | 5.2% | 374 |
| eb2b.com.pl | 2.5% | 102 |
| e-propublico.pl | 2.2% | 130 |
| warszawa.pl | 0.7% | 6 |
| sidaspzp.pl | 0.4% | 45 |
| proebiz.com | 0.3% | 53 |
| finn.pl | 0.3% | 28 |
| olsztyn.eu | 0.2% | 23 |
| postepowania.pl | 0.1% | 11 |
| lasy.gov.pl | 0.1% | 24 |
| wp.mil.pl | 0.1% | 8 |
| gov.pl | 0.1% | 19 |

## Top e-procurement platforms per country (TED, last 12 months)

| Country | Platforms (notices) |
|---|---|
| DEU | dtvp.de (11641), subreport.de (7955), evergabe-online.de (7903), rib.de (7748), deutsche-evergabe.de (7168), evergabe.de (4273) |
| POL | platformazakupowa.pl (21074), ezamowienia.gov.pl (14685), ezamawiajacy.pl (14355), eb2b.com.pl (5367), logintrade.net (4367), e-propublico.pl (2690) |
| FRA | marches-publics.info (17251), marches-publics.gouv.fr (9784), achatpublic.com (6909), marches-securises.fr (4129), e-marchespublics.com (3816), maximilien.fr (2465) |
| ESP | contrataciondelestado.es (18677), contractaciopublica.cat (3685), euskadi.eus (2297), comunidad.madrid (1083), junta-andalucia.es (1017), juntadeandalucia.es (692) |
| BEL | publicprocurement.be (13378), 3p.eu (4610), ec.europa.eu (1588), elia.be (44), coupahost.com (22) |
| ITA | lombardia.it (1572), acquistitelematici.it (1282), acquistinretepa.it (1148), maggiolicloud.it (1135), ariaspa.it (991), tuttogare.it (990) |
| CZE | nipez.cz (5010), tenderarena.cz (4878), egordion.cz (2029), vhodne-uverejneni.cz (749), cenakhk.cz (345), e-zakazky.cz (275) |
| SWE | tendsign.com (7757), e-avrop.com (3688), kommersannons.se (1992), clira.io (989) |
| ROU | e-licitatie.ro (14125) |
| SVN | enarocanje.si (6133), ejn.gov.si (6092), s-procurement.si (140), eponudbe.si (44) |
| PRT | vortal.biz (4693), acingov.pt (3803), anogov.com (802), espap.gov.pt (55), compraspt.com (55) |
| BGR | eop.bg (8481) |
| NOR | mercell.com (7102), artifik.no (695), ivalua.app (448), hyyr.no (184), achilles.com (26), tendsign.no (3) |
| NLD | tenderned.nl (8202), tenpu.eu (51), negometrix.com (47), hevo.nl (34), flextender.nl (3) |
| HRV | eojn.hr (8105) |
| LTU | viesiejipirkimai.lt (8093) |
| SVK | uvo.gov.sk (4323), proebiz.com (1642), isepvo.sk (1246), eranet.sk (297), ezakazky.sk (104), tenderia.sk (93) |
| FIN | tarjouspalvelu.fi (6494), hankintailmoitukset.fi (756), buildercom.net (144), sokopro.fi (51), toimittajapalvelu.fi (50), haahtela.fi (49) |
| LVA | eis.gov.lv (6477), latvenergo.lv (9) |
| AUT | vergabeportal.at (3261), vemap.com (1312), provia.at (816), wien.gv.at (804), vorarlberg.at (151), evn.at (61) |
| GRC | eprocurement.gov.gr (3694), promitheus.gov.gr (1730), marketsite.gr (245), deddie.gr (40), promitheus.gr (40), growthfund.gr (38) |
| IRL | etenders.gov.ie (5834), etenders.ie (5) |
| HUN | ekr.gov.hu (4647) |
| DNK | eu-supply.com (2177), comdia.com (774), ethics.dk (681), dalux.com (196), ibinder.com (184), byggeprojekt.dk (65) |
| EST | riigihanked.riik.ee (3505) |
| CHE | simap.ch (2076), decisionadvisor.ch (71), suisseoffer.ch (34), devisio.ch (25), konkurado.ch (16), sbb.ch (15) |
| LUX | b2g.etat.lu (1717), eib.org (23) |
| MLT | etenders.gov.mt (1231) |
| MKD | e-nabavki.gov.mk (1114), enabavki.gov.mk (38), e-nabavki,gov.mk (29), nabavki.gov.mk (7), edu.mk (3) |
| CYP | eprocurement.gov.cy (1066) |
| MDA | achizitii.md (135), gov.md (121), e-licitatie.md (16), solidarityfund.pl (6), mtender.md (4) |
