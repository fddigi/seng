# Scraping lessons

Levende dokument. Erfaringer fra rigtige projekter bygget på denne skabelon, der
enten (a) blev til genbrugelig kode i `scraper-core` (nævnt kort her, se selve
modulet for detaljer), eller (b) er bevidsthed/metode, der ikke lader sig
generalisere til én funktion. Tilføj nye fund her, som de opstår.

## Blevet til kode i `scraper-core`

- **Fritekst matcher ikke normaliseret søgeterm** — to distinkte varianter:
  glued generation-suffixes (`RCF ART-710A-MK5` vs. `RCF ART 710`) og
  sprogblanding (`jacka`/`kurtka` vs. `jakke`). Se `scraper_core.matching`.
- **Pris-/valutaformater varierer pr. kilde, og gættes aldrig stiltiende** —
  øre/cent vs. kr/EUR, komma vs. punktum som decimal. Se `scraper_core.pricing`.
- **To-fase-scraperes cache husker ikke nye felter** — et boolean
  "fetched"-flag strandser gamle rækker permanent, når detalje-kontrakten
  udvides. Se `scraper_core.detail_cache` (opt-in, ikke default).
- **Én hængende kilde kan blokere en hel kørsel** — se `scraper_core.watchdog`.
- **En stale, forkert konfigureret filsti fejler stille i stedet for tydeligt.**
  Et "credentials ikke tilgængelige"-fallback kan ikke skelne "ikke sat" fra
  "sat, men filen findes ikke længere" (fx efter en mappeflytning) — begge
  endte som samme stille degradering. Se `scraper_core.validate_paths()`,
  kald den én gang ved opstart for hver konfigureret fil-/mappesti.
- **Delta-sync passer ikke til output der bliver fuldstændigt genberegnet
  hver kørsel** (fx et match-resultat mod hele datasættet, ikke en
  inkrementel opdatering). Row-level delta-sync kan give et race condition
  mellem to overlappende kørsler, der skriver delvise resultater ind i
  samme tabel. Se `scraper_core.generations` — atomisk "hele batch'en eller
  intet"-publicering med en "kun hvis nyere"-guard, som et andet
  ligeværdigt mønster ved siden af delta-sync, ikke en erstatning for det.
- **`worker/src/middleware.ts`s autentificeringslogik er delt i en
  framework-uafhængig kerne (`authenticateRequest()`, tager kun primitive
  værdier) + en tynd Hono-adapter (`requireAuth()`).** Et projekt der ikke
  bruger Hono kan genbruge kernen direkte. Se `worker/src/cors.ts` for
  samme idé anvendt på CORS: en produktions-allow-liste + et
  `localhost`/`127.0.0.1`-præfiks-match til lokal udvikling, i stedet for
  enten helt åben `"*"` eller kun ét fast domæne (sidstnævnte bryder lokal
  udvikling helt).

## Bevidsthed/metode — ikke automatiserbart, hold det for øje

- **Klassifikations-rækkefølge er skrøbelig.** Et keyword-baseret "match første
  fund"-tjek fejler på strenge der indeholder flere signaler samtidig (fx "god,
  men brugt" skal klassificeres som "god", ikke "brugt"). Ordn altid mønstre
  fra mest specifik/alvorlig til mest generisk, og test eksplicit mod strenge
  med flere overlappende keywords — ikke kun enkeltord.
- **Et DOM-/tekstbaseret "er vi logget ind"-tjek rådner over tid.** En
  kildeside kan ændre sig uden at nogen af vores egne ændringer udløste det (fx
  et login-specifikt link, der pludselig vises til alle besøgende). Brug det
  mest specifikke signal muligt, ikke en bred OR-betingelse, og revalidér
  periodisk — antag aldrig at det forbliver korrekt for evigt.
- **Foretræk netværks-interception frem for URL-gætteri ved API-opdagelse.**
  De bedste datakilder findes ved at observere ægte netværkstrafik fra en
  rigtig side (browser devtools/Playwright), ikke ved at gætte
  endpoint-navne. Et gættet endpoint kan se rimeligt ud og stadig aldrig
  være det, den rigtige frontend faktisk bruger.
- **Shadow-DOM kræver Playwrights egen locator.** Native
  `document.querySelectorAll`/`page.content()` giver nul træf på indhold
  inde i et shadow-DOM-webkomponent, selvom data er synligt i browseren.
  Playwrights `page.locator()` piercer shadow-DOM som standard — brug den,
  ikke rå DOM-parsing, når en kilde bruger webkomponenter.
- **Bot-mure varierer i type pr. kilde, og er stigende, ikke tøj-/domænespecifikke.**
  Forvent forskellige mekanismer (rate-limit-headers, reCAPTCHA på specifikke
  flows, DataDome der kan trigge selv med gyldigt login) — byg kilde-specifik
  detektion, antag ikke ét universelt signal dækker alle kilder.
- **Ingen cross-platform duplikat-identitet, med vilje.** Når flere strukturelt
  urelaterede markedspladser scrapes, findes der typisk ingen fælles
  identifikator for "samme vare, to steder". Den sikre default er at
  behandle hvert kilde-fund som separat og præfiksere nøgler med kildenavn
  (`source:item_key`) — IKKE forsøge at gætte identitet på tværs af kilder.
  Dette er et designprincip, ikke en bug der skal rettes.
- **Ekskludér indsamlings-metadata fra change-detection-hashen, ikke kun
  tidsstempler.** Ethvert felt der afspejler HVORNÅR/HVORDAN/HVILKEN SØGNING
  fandt en vare (ikke selve indholdet) kan legitimt variere mellem kørsler
  for en uændret vare, og skal ekskluderes fra `hash_payload` — se
  `scraper_core.local_db.LocalStore.upsert_if_changed()`s docstring.
- **Når forretningslogik porteres "uændret" til et nyt skema, tjek eksplicit
  for hardkodede kolonnenavne først.** Kode der refererer specifikke
  SQL-kolonnenavne fra det gamle skema kompilerer/importerer fint efter en
  migrering, men fejler først ved faktisk kørsel mod den nye tabel.
- **En rød multi-job CI-workflow kan skjule flere, urelaterede røde jobs.**
  Hvis ét job (fx et secret-scan) fejler konsekvent, er det let kun at
  undersøge DET jobs logs hver gang — og aldrig opdage at et helt andet,
  usammenhængende job i samme workflow-kørsel også har været rødt siden dag
  ét, af en helt anden årsag. Tjek eksplicit status for ALLE jobs i en
  workflow-kørsel, ikke kun det du allerede leder efter.
- **Verificér altid et tredjeparts-navngivningsmønster (CLI-flags,
  release-asset-navne) direkte mod den faktiske kilde, før det lægges ind i
  en workflow — selv når forslaget kommer fra en tidligere, tilsyneladende
  fornuftig anbefaling (inklusive dine egne).** Samme klasse fejl er set
  flere gange uafhængigt af hinanden i denne skabelons historik (wrangler
  CLI-flags, gitleaks release-asset-navngivning).

## Eftermontering (retrofit) på et allerede levende projekt

Skabelonens dokumentation er skrevet til NYE projekter — at eftermontere et
mønster (fx auth-modellen) på et projekt der allerede er i aktiv brug, er en
reel overgang, ikke kun en usynlig backend-udskiftning:
- En auth-model-ændring ugyldiggør allerede-udstedte adgangskoder/sessions
  for eksisterende brugere/klienter — det er en UX-hændelse, planlæg den.
- Et nyt Cloudflare KV-namespace (fx til rate limiting) er en reel
  infrastruktur-provisionering, ikke kun et funktionskald — det skal
  oprettes eksplicit, uanset om projektet i forvejen har en Worker kørende.
- For simple værktøjer til én bruger/husstand er brugernavn+kodeord ofte
  overkill — at hashe ét delt kodeord (springe per-bruger-kontolagring over
  helt) er en gyldig, bevidst forenkling, ikke en genvej man "burde" undgå.
