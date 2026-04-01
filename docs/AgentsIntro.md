# Tahák: cesta k agentům

> Klikni na sekci pro rozbalení detailu.

<details>
<summary><strong>1. Co je rozdíl mezi modelem, agentem a automatizací</strong></summary>

- **Model** generuje odpověď.
- **Automatizace** vykonává předem navržený postup.
- **Agent** dostane cíl, sám volí kroky, používá nástroje, kontroluje výsledek a iteruje.
- Zásadní rozdíl není v tom, že „umí psát text“, ale že **umí aktivně pracovat na dosažení výsledku**.

</details>

<details>
<summary><strong>2. Jazykový model jako komprimovaná reprezentace znalostí</strong></summary>

- LLM je **komprimovaná, ztrátová reprezentace znalostí lidstva**.
- Není to přesná databáze ani úplný archiv.
- Je to **snapshot v čase** - model neví, co se změnilo po jeho natrénování.
- Každý prompt je v principu **nový start**.

</details>

<details>
<summary><strong>3. Analogie: pacient bez nové paměti</strong></summary>

- Model je jako **pacient, který si nodokáže vytvářet nové vzpomínky**.
- Aby fungoval dobře, musí mít u postele „lístečky s kontextem“.
- Bez kontextu je sice chytrý, ale odpovídá **z obecné komprese znalostí**.
- Proto model potřebuje **dobře připravený kontext**.

</details>

<details>
<summary><strong>4. Proč je kritická relevance kontextu</strong></summary>

- Problém není jen „dát modelu data“.
- Kritické je dát mu **správná, relevantní a aktuální data**.
- Špatný kontext znamená:
    - vyšší cenu,
    - vyšší latenci,
    - větší šum,
    - horší rozhodování.
- Dobrá analogie v enterprise:
    - nestačí hodit člověka do knihovny,
    - potřebuje vědět **správný fond, regál, knihu a konkrétní kapitolu**.

</details>

<details>
<summary><strong>5. Enterprise vzory před agenty</strong></summary>

- Ještě před nástupem skutečných agentů se ve firmách prosadily dva hlavní vzory.

**RAG chat nad obsahem**

- model dohledá relevantní obsah,
- model odpoví nad konkrétními firemními znalostmi,
- typický pattern: „najdi a vysvětli“.

**AI workflow**

- úloha je předem navržená a řízená,
- typicky:
    - klasifikace,
    - extrakce,
    - OCR,
    - sumarizace,
    - práce s fakturami,
    - práce se skeny,
    - práce s hlasem.

</details>

<details>
<summary><strong>6. Od pevných workflow ke skutečným agentům</strong></summary>

- Tyto přístupy byly užitečné, ale stále šlo hlavně o:
    - **pasivní systém**,
    - nebo **pevně orchestrovaný proces**.
- Skutečný agent se liší tím, že:
    - dostane **cíl**,
    - sám volí další krok,
    - vybírá nástroje,
    - opravuje chyby,
    - ověřuje hypotézy,
    - iteruje směrem k výsledku.
- Tedy ne jen „odpovídá“, ale **řeší problém**.

</details>

<details>
<summary><strong>7. Zlom v kódování: listopad / prosinec 2025</strong></summary>

- Zlom v agentickém kódování byl vidět **zlom někdy kolem listopadu / prosince 2025**.
- Výstupy přestaly být jen „chat, který napíše kus kódu“.
- **Fázový přechod** - najednou to začalo výborně spolehlivě fungovat

</details>

<details>
<summary><strong>8. Agentické kódování</strong></summary>

- Agent dnes umí:
    - napsat kód,
    - spustit ho,
    - přečíst chybu,
    - opravit ji,
    - zkusit další iteraci.
- Umí také:
    - otevřít browser,
    - udělat screenshot,
    - proklikat UI,
    - testovat aplikaci,
    - ověřit výsledek,
    - případně i nasadit řešení.
- Už to nepřipomíná „chat“.
- Připomíná to **práci softwarového inženýra**.

</details>

<details>
<summary><strong>9. Ekonomická hodnota agentů</strong></summary>

- Licence za **20 nebo 40 dolarů** je málo, dobrý inženýr utratí za AI mnohem víc.
- Hodnota agenta je v tom, že dokáže **akcelerovat drahou lidskou práci**.
- Jensen Huang to popsal velmi ostře:
    - řekl, že inženýři mohou dostávat tokeny zhruba ve výši **poloviny své mzdy**,
    - a že by byl „deeply alarmed“, pokud by inženýr s ročním příjmem **500 000 USD** spotřeboval jen zlomek a ne alespoň kolem **250 000 USD v tokenech**. [\[cnbc.com\]](https://www.cnbc.com/2026/03/20/nvidia-ai-agents-tokens-human-workers-engineer-jobs-unemployment-jensen-huang.html), [\[aol.com\]](https://www.aol.com/articles/jensen-huang-says-deeply-alarmed-040314321.html)
- AI rozevře nůžky mezi nejlepšími s AI (ti porostou třeba 10x v produktivitě) a zbytkem startovního pole
- Dramaticky naroste průměrná produktivita ale v mediánu se může změnit méně (horní část spektra bude benefitovat mnohem víc)

</details>

<details>
<summary><strong>10. OpenClaw jako „ChatGPT moment“ pro agenty</strong></summary>

- Pro širší publikum je užitečné mít **jednoduchý, srozumitelný referenční příklad**, na kterém se dá vysvětlit, co agent dělá.
- OpenClaw se dá použít jako takový „aha moment“:
    - ne jen model,
    - ale systém, který **pracuje, jedná a dokončuje úkol**.
- NVIDIA navíc v březnu 2026 zmiňovala OpenClaw jako lokálně běžícího asistenta na Jetsonu pro automatizaci úloh a práci v reálném čase. [\[blogs.nvidia.com\]](https://blogs.nvidia.com/blog/tag/gtc-2026/), [\[blogs.nvidia.com\]](https://blogs.nvidia.com/?hashtags=gtc-2026)
- Jeho autor Peter Steinberger bude keynote speaker na Microsoft Build 2026
- Z 0 na 145k hvězdiček na Githubu za 14 dní, aktuálně má 350k hvězdiček (nejvíc ze všech non-aggregator projektů)
  - Pro srovnání Linux má 225k, Kubernetes 121k, VS Code 183k, React 244k

</details>

<details>
<summary><strong>11. osobní agent prakticky, například s Copilot Cowork</strong></summary>

- Dobrý příklad agenta v praxi:
    - projde historii callů,
    - projde Teams kontext,
    - projde agendu,
    - navrhne úpravu dalšího setkání.
- Aktivně pracuje:
    - stáhne demo,
    - upraví kód pro zákazníka,
    - doplní logo a branding,
    - přizpůsobí data,
    - nasadí demo,
    - připraví screenshoty,
    - doplní prezentaci,
    - navrhne update pozvánky a agendy.
- To je zásadní rozdíl oproti systému, který jen **poradí**.

</details>

<details>
<summary><strong>12. Agenti v Microsoft 365 a firmě</strong></summary>

- V Microsoft 365 firmě lze agenty chápat jako:
    - **osobního asistenta v chatu**,
    - **specializovaného virtuálního kolegu**,
    - **pomocníka přivolaného do dokumentu nebo procesu**.
- Klíčové je, že pracují nad:
    - firemním kontextem,
    - firemními daty,
    - firemními nástroji,
    - historií komunikace a dokumentů.

</details>

<details>
<summary><strong>13. Jak najít na co se zaměřit</strong></summary>

- Příklad z bezpečnosti - automatizace posouzení žádosti o bezpečnostní výjimku (3 měsíce trvá)
- Zákazník se zaměří na samotné posouzení žádosti, tedy to nejtěžší
- Nicméně mu unikne to, kde je ve skutečnosti problém:
    - neúplné podklady,
    - nejasné požadavky,
    - příliš široká oprávnění,
    - dlouhá a roztříštěná komunikace.
- Typický anti-pattern:
    - organizace chce „zero-shot systém“,
    - ale ignoruje skutečné úzké místo procesu.

</details>

<details>
<summary><strong>14. Příklad agentického nasazení</strong></summary>

- Agent nemusí nahrazovat bezpečáka v rozhodnutí.
- Může ale zásadně odstranit tření v procesu.

**Úplnost a komunikace**

- zkontroluje úplnost žádosti,
- vyžádá chybějící informace,
- komunikuje se zadavatelem,
- komunikuje s technickým týmem,
- pracuje se směrnicemi a historií.

**Kvalita návrhu**

- dělá **pushback**,
- navrhuje **least privilege**,
- doporučuje menší scope,
- doporučuje kratší dobu přístupu,
- navrhuje **PIM / JIT**,
- navrhuje anonymizaci dat,
- navrhuje technicky bezpečnější variantu řešení.

</details>

<details>
<summary><strong>15. Kam firmy směřují</strong></summary>

- Cílem firem není jen „mít nějaké AI“.
- Cílem je:
    - **10x víc AI v horizontu 12 měsíců**,
    - identifikovat použitelné use casy,
    - nečekat půl roku na každé jedno řešení,
    - neřešit každý agentický scénář jako jednorázový projekt.
- Proto firmy potřebují **platformu**, ne jen demo.

**Co ta platforma musí mít**

- governance,
- standardizované komponenty,
- bezpečnost,
- testovatelnost,
- observabilitu,
- katalog agentů,
- administraci,
- auditovatelnost,
- provozní model pro škálování.

</details>

<details>
<summary><strong>16. Co to všechno znamená</strong></summary>

- OpenClaw je **ChatGPT moment pro agenty**.
- Agenti nejsou lepší chatboti, ale systémy, které dostanou cíl, rozhodují se, používají nástroje a **dotahují práci do výsledku**.
- Mají zásadní hodnotu, bude zaměstanec spotřebovávat agenty v hodnotě **půlky své mzdy**?
- Ve firmách budou fungovat jako osobní **coworkeři a autonomní workflow** vrstva.
- Cílem pro tento rok nejsou jednotky vydžených usecase, ale platforma pro **10x více AI** během 12 měsíců.

</details>
