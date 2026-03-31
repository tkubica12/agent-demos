# Tahák: cesta k agentům

## 1. Co je rozdíl mezi modelem, agentem a automatizací

-   **Model** generuje odpověď.
-   **Automatizace** vykonává předem navržený postup.
-   **Agent** dostane cíl, sám volí kroky, používá nástroje, kontroluje výsledek a iteruje.
-   Zásadní rozdíl není v tom, že „umí psát text“, ale že **umí aktivně pracovat na dosažení výsledku**.

## 2. Jazykový model jako komprimovaná reprezentace znalostí

-   LLM je **komprimovaná, ztrátová reprezentace části znalostí lidstva**.
-   Není to přesná databáze ani úplný archiv.
-   Je to **snapshot v čase** — model neví automaticky, co se změnilo po jeho natrénování.
-   Každý prompt je v principu **nový start**.

## 3. Analogie: pacient bez nové paměti

-   Model je jako **pacient, který si nevytváří novou dlouhodobou pracovní paměť**.
-   Aby fungoval dobře, musí dostat „lístečky s kontextem“.
-   Bez kontextu může působit chytře, ale ve skutečnosti často odpovídá **naslepo z obecné komprese znalostí**.
-   Proto model potřebuje **dobře připravený kontext**.

## 4. Proč je kritická relevance znalostí

-   Problém není jen „dát modelu data“.
-   Kritické je dát mu **správná, relevantní a aktuální data**.
-   Špatný kontext znamená:
    -   vyšší cenu,
    -   vyšší latenci,
    -   větší šum,
    -   horší rozhodování.
-   Dobrá analogie v enterprise:
    -   nestačí hodit člověka do knihovny,
    -   potřebuje vědět **správný fond, regál, signaturu a konkrétní dokument**.

## 5. Enterprise vzory před agenty

-   Ještě před nástupem skutečných agentů se ve firmách prosadily dva hlavní vzory:

### 5.1 RAG chat nad obsahem

-   model dohledá relevantní obsah,
-   model odpoví nad konkrétními firemními znalostmi,
-   typický pattern: „najdi a vysvětli“.

### 5.2 AI workflow

-   úloha je předem navržená a řízená,
-   typicky:
    -   klasifikace,
    -   extrakce,
    -   OCR,
    -   sumarizace,
    -   práce s fakturami,
    -   práce se skeny,
    -   práce s hlasem.

## 6. Od pevných workflow ke skutečným agentům

-   Tyto přístupy byly užitečné, ale stále šlo hlavně o:
    -   **pasivní systém**,
    -   nebo **pevně orchestrovaný proces**.
-   Skutečný agent se liší tím, že:
    -   dostane **cíl**,
    -   sám volí další krok,
    -   vybírá nástroje,
    -   opravuje chyby,
    -   ověřuje hypotézy,
    -   iteruje směrem k výsledku.
-   Tedy ne jen „odpovídá“, ale **řeší problém**.

## 7. Zlom v kódování: listopad / prosinec

-   V agentickém kódování byl vidět **zlom někdy kolem listopadu / prosince**.
-   Výstupy přestaly být jen „chat, který napíše kus kódu“.
-   Začaly být:
    -   strukturovanější,
    -   systematičtější,
    -   schopné práce se zpětnou vazbou,
    -   schopné ověřovat výsledek.

## 8. Agentické kódování

-   Agent dnes umí:
    -   napsat kód,
    -   spustit ho,
    -   přečíst chybu,
    -   opravit ji,
    -   zkusit další iteraci.
-   Umí také:
    -   otevřít browser,
    -   udělat screenshot,
    -   proklikat UI,
    -   testovat aplikaci,
    -   ověřit výsledek,
    -   případně i nasadit řešení.
-   Už to nepřipomíná „chat“.
-   Připomíná to **práci softwarového inženýra**.

## 9. Ekonomická hodnota agentů

-   Hodnota agentů není v licenci za **20 nebo 40 dolarů**.
-   Hodnota je v tom, že dokážou **akcelerovat drahou lidskou práci**.
-   Jensen Huang to popsal velmi ostře:
    -   řekl, že inženýři mohou dostávat tokeny zhruba ve výši **poloviny své mzdy**,
    -   a že by byl „deeply alarmed“, pokud by inženýr s ročním příjmem **500 000 USD** spotřeboval jen zlomek a ne alespoň kolem **250 000 USD v tokenech**. [\[cnbc.com\]](https://www.cnbc.com/2026/03/20/nvidia-ai-agents-tokens-human-workers-engineer-jobs-unemployment-jensen-huang.html), [\[aol.com\]](https://www.aol.com/articles/jensen-huang-says-deeply-alarmed-040314321.html)
-   Pointa:
    -   pokud agent zvyšuje produktivitu vysoce placené práce,
    -   jeho ekonomická hodnota může být **řádově vyšší než cena licence**.

## 10. OpenClaw jako „ChatGPT moment“ pro agenty

-   Pro širší publikum je užitečné mít **jednoduchý, srozumitelný referenční příklad**, na kterém se dá vysvětlit, co agent dělá.
-   OpenClaw se dá použít jako takový „aha moment“:
    -   ne jen model,
    -   ale systém, který **pracuje, jedná a dokončuje úkol**.
-   NVIDIA navíc v březnu 2026 zmiňovala OpenClaw jako lokálně běžícího asistenta na Jetsonu pro automatizaci úloh a práci v reálném čase. [\[blogs.nvidia.com\]](https://blogs.nvidia.com/blog/tag/gtc-2026/), [\[blogs.nvidia.com\]](https://blogs.nvidia.com/?hashtags=gtc-2026)

## 11. Workshop se zákazníkem: praktická ukázka agentičnosti

-   Dobrý příklad agenta v praxi:
    -   projde historii callů,
    -   projde Teams kontext,
    -   projde agendu,
    -   navrhne úpravu dalšího setkání.
-   Aktivně pracuje:
    -   stáhne demo,
    -   upraví kód pro zákazníka,
    -   doplní logo a branding,
    -   přizpůsobí data,
    -   nasadí demo,
    -   připraví screenshoty,
    -   doplní prezentaci,
    -   navrhne update pozvánky a agendy.
-   To je zásadní rozdíl oproti systému, který jen **poradí**.

## 12. Agenti v Microsoft 365 a firmě

-   V Microsoft 365 firmě lze agenty chápat jako:
    -   **osobního asistenta v chatu**,
    -   **specializovaného virtuálního kolegu**,
    -   **pomocníka přivolaného do dokumentu nebo procesu**.
-   Klíčové je, že pracují nad:
    -   firemním kontextem,
    -   firemními daty,
    -   firemními nástroji,
    -   historií komunikace a dokumentů.

## 13. Security příklad: co neautomatizovat

-   V bezpečnosti je důležité neautomatizovat bezhlavě finální rozhodnutí člověka.
-   Rizikové jsou situace, kdy jsou:
    -   neúplné podklady,
    -   nejasné požadavky,
    -   příliš široká oprávnění,
    -   dlouhá a roztříštěná komunikace.
-   Typický anti-pattern:
    -   organizace chce „zero-shot systém“,
    -   ale ignoruje skutečné úzké místo procesu.

## 14. Kde naopak agent v security procesu pomáhá

-   Agent nemusí nahrazovat bezpečáka v rozhodnutí.
-   Může ale zásadně odstranit tření v procesu:

### 14.1 Úplnost a komunikace

-   zkontroluje úplnost žádosti,
-   vyžádá chybějící informace,
-   komunikuje se zadavatelem,
-   komunikuje s technickým týmem,
-   pracuje se směrnicemi a historií.

### 14.2 Kvalita návrhu

-   dělá **pushback**,
-   navrhuje **least privilege**,
-   doporučuje menší scope,
-   doporučuje kratší dobu přístupu,
-   navrhuje **PIM / JIT**,
-   navrhuje anonymizaci dat,
-   navrhuje technicky bezpečnější variantu řešení.

## 15. Kam firmy směřují

-   Cílem firem není jen „mít nějaké AI“.
-   Cílem je:
    -   **10× víc AI v horizontu 12 měsíců**,
    -   identifikovat použitelné use casy,
    -   nečekat půl roku na každé jedno řešení,
    -   neřešit každý agentický scénář jako jednorázový projekt.
-   Proto firmy potřebují **platformu**, ne jen demo.

### 15.1 Co ta platforma musí mít

-   governance,
-   standardizované komponenty,
-   bezpečnost,
-   testovatelnost,
-   observabilitu,
-   katalog agentů,
-   administraci,
-   auditovatelnost,
-   provozní model pro škálování.

## 16. Co to všechno znamená

-   OpenClaw je **ChatGPT moment pro agenty**.
-   Agenti nejsou lepší chatboti, ale systémy, které dostanou cíl, rozhodují se, používají nástroje a **dotahují práci do výsledku**.
-   Mají zásadní hodnotu, bude zaměstanec spotřebovávat agenty v hodnotě **půlky své mzdy**?
-   Ve firmách budou fungovat jako osobní **coworkeři a autonomní workflow** vrstva.
-   Cílem pro tento rok nejsou jednotky vydžených usecase, ale platforma pro **10× více AI** během 12 měsíců.
