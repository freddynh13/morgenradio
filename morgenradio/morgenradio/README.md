# Morgenradio: din egen AI-morgenradio

Hver morgen laves der automatisk en ny udsendelse, ca. 20-25 minutter lang:

> 🎙 **God morgen og vejret** → **Nyhederne** → ♪ sang → 🎙 **Sporten** → ♪ sang → 🎙 **Musiknyt** → ♪ sang (gerne af en kunstner fra musiknyt) → 🎙 **Dagens lette historie og farvel** → ♪ sang

To AI-værter (Sofie og Mads) taler sammen som i rigtig morgenradio. Sangene er rigtige numre fra **din egen Spotify**.

## Sådan hænger det sammen

```
GitHub Actions (hver morgen kl. ca. 04:15, gratis)
  1. Henter nyheder fra DR, TV 2, Politiken, Bold, GAFFA, Soundvenue … (RSS)
  2. Samler artikler om samme historie i klynger og rangerer dem
  3. Claude, trin 1 (redaktør): vælger historier og foreslår sange
  4. Spotify: tjekker at sangene findes, og at de ikke er spillet for nylig
  5. Claude, trin 2 (tekstforfatter): skriver manuskriptet med de bekræftede sange
  6. OpenAI: indtaler hver replik med værternes stemmer
  7. Lyden samles med jingler og normaliseres til Spotifys lydniveau
  8. Lægges på din private webside (GitHub Pages)

Radio-afspilleren (websiden)
  Spiller værternes klip → beder din Spotify spille sangen → venter til den er
  færdig → næste klip … Sangene streames fra Spotify og ligger aldrig i vores
  filer. Det er derfor, det er lovligt.
```

Tankegangen er den samme som i dit nyhedsbrev-script: indsamling → klyngning → Claude vælger → Claude skriver. Det sidste trin laver bare lyd i stedet for en mail.

## Det skal du bruge

| Hvad | Pris (ca.) |
|---|---|
| GitHub-konto | Gratis |
| Anthropic API-nøgle (den, du allerede bruger) | Et par kroner pr. udsendelse med Opus, mindre med Sonnet |
| OpenAI API-nøgle (stemmer) | ca. 1 kr. pr. udsendelse (9 min. tale) |
| Spotify **Premium** | Det har du |

---

## Opsætning (ca. 20 minutter, én gang)

### 1. Læg koden på GitHub
1. Opret et nyt repository på github.com, fx `morgenradio`. Gør det **Public** (GitHub Pages fra private repos kræver betalt GitHub Pro).
   Siden har `noindex`, så den dukker ikke op på Google, og der ligger ingen nøgler i koden. Men alle med linket kan lytte med.
2. Upload alle filerne fra denne mappe (træk dem ind under "Add file → Upload files"). Mappen `.github` skal med.

### 2. Lav en Spotify-app
1. Gå til <https://developer.spotify.com/dashboard> → **Create app**.
2. Udfyld navn og beskrivelse.
   **Redirect URI:** `https://DIT-GITHUB-NAVN.github.io/morgenradio/` (præcis sådan, med `/` til sidst).
3. Under "Which API/SDKs": vælg **Web API** og **Web Playback SDK**. Gem.
4. Åbn **Settings** og kopiér **Client ID** og **Client secret**.
5. Under **User Management**: tilføj din egen Spotify-e-mail. (Apps i Development Mode må have op til 5 brugere, og ejeren skal have Premium.)

### 3. OpenAI-nøgle
Opret en nøgle på <https://platform.openai.com/api-keys>, og sæt et lille beløb ind under Billing.

### 4. Læg nøglerne i GitHub
I dit repository: **Settings → Secrets and variables → Actions → New repository secret**. Opret disse fire:

| Navn | Værdi |
|---|---|
| `ANTHROPIC_API_KEY` | din Claude-nøgle |
| `OPENAI_API_KEY` | din OpenAI-nøgle |
| `SPOTIFY_CLIENT_ID` | fra trin 2 |
| `SPOTIFY_CLIENT_SECRET` | fra trin 2 |

### 5. Første kørsel
1. Gå til **Actions** → **Morgenradio** → **Run workflow**. Sæt hak i *dry run* første gang. Så tester du hele kæden gratis uden API-kald.
2. Når den er grøn: **Settings → Pages** → Source: *Deploy from a branch* → Branch: **gh-pages** / root → Save.
3. Efter et minut ligger radioen på `https://DIT-GITHUB-NAVN.github.io/morgenradio/`.
4. Kør workflowet igen **uden** dry run. Nu får du en rigtig udsendelse.

### 6. Lyt
- **På computeren:** åbn siden → **Log ind med Spotify** → vælg "Denne browser" → ▶.
- **På telefonen:** åbn Spotify-appen kort, åbn siden → log ind → tryk **Opdatér** → vælg din telefon → ▶.
  Hold skærmen tændt, mens radioen kører. Siden prøver selv at holde den vågen, for telefoner sætter baggrundsfaner på pause.
- **I bilen eller en podcast-app:** "Hent podcast-version" er hele udsendelsen uden sange. Du kan også abonnere på `…/morgenradio/feed.xml` i en podcast-app, der understøtter RSS-links (fx Pocket Casts eller Overcast).
- Tip: gem siden på hjemmeskærmen ("Føj til hjemmeskærm").

---

## Tilpas den (alt står i `config.yaml`)

- **Værter:** navne, personlighed og stemme. Prøv forskellige stemmer: `marin`, `cedar`, `coral`, `ash`, `sage`, `ballad`, `verse`.
- **Programflade:** rækkefølge, antal historier pr. segment og antal sange. Du kan fx flytte vejret, tilføje en ekstra sang eller fjerne musiknyt.
- **Længde:** `tale_minutter`.
- **Interesser:** dine hold, emner og ord, der skal filtreres helt fra.
- **Musiksmag:** skriv frit, som du ville forklare det til en DJ. Claude bruger det.
- **Kilder:** tilføj eller fjern RSS-feeds. Google News-søgninger virker også (se eksemplerne).
- **Tidspunkt:** ret `cron` i `.github/workflows/morgenradio.yml`.
- **Egne jingler:** læg `jingle_intro.mp3`, `jingle_sweeper.mp3` og `jingle_outro.mp3` i mappen `assets/`. Så bruges de i stedet for de genererede.

## Fejlfinding

| Problem | Løsning |
|---|---|
| Workflowet fejler | Åbn kørslen under Actions. Loggen fortæller på dansk, hvad der mangler (fx en nøgle). |
| En kilde virker ikke | Kør lokalt `python -m radio.main --tjek-kilder`, eller se loggen. En død kilde vælter aldrig udsendelsen. |
| "INVALID_CLIENT: Invalid redirect URI" | Redirect URI i Spotify-appen skal være præcis sidens adresse med `/` til sidst. |
| Telefonen er ikke på listen | Åbn Spotify-appen, afspil noget kort, og tryk Opdatér. |
| Sangen starter ikke i browseren | Browser-afspilning virker i Chrome, Edge og Firefox på computer. Vælg ellers en Spotify-enhed. |
| Planlagt kørsel stoppede | GitHub slår planlagte kørsler fra efter 60 dage uden aktivitet. Klik "Enable workflow" under Actions. |

## Kør lokalt (valgfrit)
```bash
pip install -r requirements.txt          # kræver også ffmpeg
python -m radio.main --dry-run           # test uden nøgler
set ANTHROPIC_API_KEY=...  (osv.)
python -m radio.main                     # rigtig udsendelse → mappen site/
cd site && python -m http.server         # åbn http://localhost:8000
```
(Spotify-login virker kun på den adresse, der står som Redirect URI i Spotify-appen.)

## Filer
```
config.yaml              ← alt det, du retter i
radio/collect.py         RSS, filtrering, klyngning (genbrugt idé fra nyhedsbrevet)
radio/claude.py          redaktør- og manuskript-prompts (structured outputs)
radio/spotify.py         finder sangene på Spotify
radio/tts.py             stemmer (OpenAI gpt-4o-mini-tts)
radio/audio.py           jingler, mix og lydniveau
radio/weather.py         vejret (Open-Meteo, gratis)
radio/main.py            hele forløbet
web/                     radio-afspilleren
.github/workflows/       den daglige kørsel
```
