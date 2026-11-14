# Autopilots on Azure: dvoukolové architektonické review

**Závěr:** současný základ nepřestavovat. Největší přínos má dokončení několika porušených kontraktů po zjednodušení interaktivní cesty: oddělit autoritu schvalování od runtime, opravit lifecycle plánované práce, úplně započítat aktivní gateway turny a obnovovaný kontext Teams. Nejzajímavější další zjednodušení představují přímé autentizované callbacky Connector Namespace a atomický původ cron jobů v runtime. Obojí vyžaduje ověření konkrétního kontraktu, ne jen existenci obecné produktové funkce.

Datum syntézy: **12. 11. 2026**. Jde o doporučení, nikoli implementační změny nebo autorizaci nasazení.

## 1. Rozsah, aktuálnost a metoda

Review začalo až po potvrzení cílového koncepčního stavu session „Architektura Autopilots“ dne 11. 11. 2026 v 21:38 CET. Zdroj pravdy byl **aktuální pracovní strom včetně necommitnutých a untracked změn**, nikoli pouze HEAD `766c5aaa59b5026cd6ab487af3a26fd3474cccc3`. Astra při druhém kole znovu porovnal osm rozhodujících source fingerprintů; shodovaly se.

| Expert | Model | Reasoning | Postup |
|---|---|---|---|
| Astra | `gpt-6-astra` | `max` | Nezávislé první kolo, potom oponentura obou ostatních |
| Opus | `claude-opus-5.5` | `max` | Stejný rozsah, vlastní závěry a cílené ověření sporných upstream delta |
| Gemini | `gemini-4-carbon` | `high`, nejvyšší podporovaná úroveň | Nezávislé první kolo, oponentura a úzké opravné addendum |

Všichni dostali stejnou orientaci bez předepsaného řešení. Druhé kolo obdrželo shody, šest konkrétních rozporů a unikátní nálezy každého experta. Níže platí **opravená stanoviska druhého kola**, ne neaktuální tvrzení z prvního. Shoda tří modelů sama není důkaz: přednost má konkrétní control-flow, tagovaný source a relevantní produktový kontrakt.

Výzkum pokrývá lokální implementaci, veřejné primární zdroje Azure a upstream Hermes. Komunitní průzkum byl na přání vlastníka vyloučen. Interní větev byla po počátečním problému s přihlášením prohledána a uložena do **samostatné neveřejné přílohy mimo repozitář**. Tento dokument nepřebírá interní obsah ani interní odkazy; všechny jeho věcné závěry mají lokální nebo veřejný podklad.

**Meze:** neproběhlo nové nasazení, živé Teams/Azure ověření, spouštění testů ani měření úspor. „Potvrzená chyba“ zde znamená staticky doložený nesoulad, ne nově pozorovaný incident. Dříve hlášené lokální testy implementátora nejsou validací tohoto review ani aktuálního nasazení. Historické latence ve SPEC nejsou nové výsledky.

Lokální cesty níže jsou relativní k `autopilots-on-azure`; řádky odkazují na posuzovaný pracovní strom a při dalších úpravách se mohou posunout.

## 2. Co je správně a má zůstat

**Na tomto směru je shoda všech tří:**

| Prvek | Proč jej zachovat |
|---|---|
| Teams / Agent 365 → gateway → Hermes native `/v1/runs` | Odstraňuje connector polling z interaktivní latence a paralelní operátorské invocation API |
| Service Bus pouze pro cron, Dreaming a deterministické document retries | Poskytuje externí probuzení spícího Workera; není chatový request/reply transport |
| Oddělené gateway a runtime compute i identity | Stabilnější Teams endpoint při výměně runtime a oddělení oprávnění gateway od prostředí agentních nástrojů |
| Memory suspend, lokální Hermes home, native backup do Blob volume | Zachovává rychlé obnovení a single-writer SQLite; přesun na Data Disk by změnil suspend kontrakt |
| Native run/occurrence identity, receipts a explicitní unknown stav | Chrání proti slepému opakování inference a externích efektů |
| Explicitní Learning Packet prepare/review/approve/export a lidská promotion | Dreaming není automatická distribuce změn ostatním Workerům |
| Agent Identity / Agent User federace a metadata-only telemetry | Managed identity runtime není zaměnitelná s obchodní identitou digitálního spolupracovníka |

Podkladem jsou `SPEC.md`, [ADR 0023](adr/0023-direct-teams-and-scheduling-only-service-bus.md), `bridge\runtime\hermes.py`, `scripts\native_transport.py` a [Sandbox lifecycle][P1].

Přijaté riziko přímé Teams cesty je nutné dál pojmenovávat: **nemá vlastní durable admission/replay záruku**. Keepalive není crash recovery, Memory suspend není záloha a broker settlement není exactly-once externí doručení. Toto review nedoporučuje zrušenou activity-recovery queue vracet.

## 3. Zásadní doporučení se silnou shodou

Pořadí prvních dvou bodů závisí na rizikovém apetitu; oba patří do první vlny. Relativní rozsah není časový odhad.

| Priorita | Doporučení | Shoda po oponentuře | Dopad / jistota / rozsah |
|---|---|---|---|
| R1 | Oddělit lidskou approval autoritu od credentialu dostupného runtime | 3/3 | Vysoký / vysoká / střední |
| R2 | Jeden úplný lifecycle plánované práce: outcome, recovery, journal a settlement | 3/3 | Vysoký / vysoká pro mechanismus / střední |
| R3 | Úplný active-work a drain scope gateway | 3/3 | Vysoký / vysoká pro drain bug / malý–střední |
| R4 | Opravit obnovenou Teams conversation cache | 3/3; liší se pouze umístění v shortlistu | Střední / velmi vysoká / malý |
| R5 | Opravit korelaci skutečných Runs a telemetry allowlist | 3/3 | Střední / vysoká pro wiring / střední |

### R1. Platný podpis není nezávislé lidské schválení

Gateway schvalovací routy ověřují `X-Autopilot-Key` proti stejnému `API_SERVER_KEY`, který má runtime. `approvedBy` přichází jako text v body. Gateway sice drží Ed25519 klíč odděleně, ale držitel runtime credentialu může autorizovat cestu ke schválení vlastního připraveného packetu.

**Důležitá hranice nálezu:** gateway nepodepisuje libovolné bajty. Ověřuje přesný pending digest; runtime následně kontroluje podpis, Worker/release, governed-state hash a ledger. Tyto kontroly nejsou rozbité. Chybí však nezávislá autorita člověka. Přesun stejného klíče do Key Vault nebo jeho generování v gateway sám tuto chybu neopraví.

**Doporučení:** runtime credential nesmí opravňovat operátorský approval/export workflow. Skutečného schvalujícího odvozovat z autorizovaného principalu, nikoli z body stringu. Zachovat přesný digest i čerstvou kontrolu stavu. Současně rozdělit přetížené credential role a promyslet rotaci; pouhé přidání dalších tajemství není cílová architektura.

**Volba, nikoli konsenzuální jediná oprava:** packet signing lze přesunout do lokálního CLI, čímž odpadne cloudový signer a část proxy rout. Dnešní Teams approval staged skill writes je jiná operace než podpis Learning Packetu, takže tím nutně nezmizí existující Teams UX. Přesto jde o rozhodnutí o budoucím workflow.

Evidence: `bridge\app.py:892–896,938–951`; `bridge\runtime\hermes.py:572–611`; `scripts\sandbox_runtime.py:461–469`; `runtimes\hermes\collective_learning.py:341–405`.

**Brána opravy:** credential dostupný runtime neautorizuje schválení; oprávněný člověk schválí pouze zkontrolovaný digest; pozdější změna governed state export odmítne. Jde o požadavky následného ověření, ne provedený útok.

### R2. Sjednotit výsledek práce, zotavení a potvrzení brokeru

`event_worker` zapisuje běžně `running` a potom `completed`, ale cesty pro `unknown`/`failed` nejsou napojené. Dream může vrátit `{"status": "uncertain"}` jako normální návratovou hodnotu a worker ji potvrdí brokeru jako úspěch. Výjimky mohou zanechat neterminální řádky. Dokumentovaná karanténa tím není splněná.

Nejde jen o kosmetický stav. Cap 32 neterminálních řádků blokuje **nová MessageId**, nikoli staré redeliveries. Uvízlý řádek může blokovat i source update. Selhání **před native claimem** navíc nemusí samo obnovit další naplánování; po claimu se naopak recurring schedule může posunout, i když selže doručení výsledku. Nelze všechny případy shrnout do tvrzení „každá chyba zastaví cron“.

Další související problém: po timeoutu může retry Dreamu narazit na `dream_started` a skončit jako uncertain, místo aby se znovu připojil ke známému native run ID.

**Doporučení:** jeden explicitní kontrakt rozlišující úspěch/duplicate/stale, retryable selhání před efektem, známý pokračující run a skutečně neznámý výsledek. Každý stav musí mít konzistentní journal, settlement a repair cestu. Doplnit kontrolovanou terminalizaci, operátorské resolve a schedule-health signal; sledovat DLQ a stáří posledního úspěšného či explicitně přeskočeného Dreamu.

**Nezjednodušovat nebezpečně:** expired lease, starý `dueAt` nebo vyčerpaný počet pokusů nejsou důkaz, že je bezpečné opakovat efekt. Re-arm musí respektovat native occurrence identity a broker deduplication. Retence receipts musí pokrýt skutečný replay horizon, včetně řízeného zpracování staré DLQ; samotné čtrnáctidenní TTL není univerzální bezpečné pravidlo mazání.

Evidence: `bridge\event_worker.py:119–164,218–251`; `bridge\durable_transport.py:111–145`; `bridge\app.py:1341–1382`; `bridge\runtime\hermes.py:466–529`; `runtimes\hermes\azure_cron_provider.py:240–294`; [pinned cron jobs][P8]; [Service Bus DLQ][P5].

**Brána opravy:** uncertain nekončí `Complete`; známý run pokračuje bez nové inference; exact redelivery neopakuje hotový efekt; terminální selhání trvale nevyčerpá admission ani drain; před-claim selhání má explicitní bezpečnou repair cestu. Časy do zaplnění journalu ani četnost duplicit nebyly měřeny.

### R3. Gateway musí vědět o celé přijaté práci

Maintenance/status dnes počítá scheduling tasks, nikoli celý přímý Teams turn a `background_turns`. Runtime může být už neaktivní, zatímco gateway stále zpracovává přílohu, learning/card continuation nebo odesílá finální zprávu. Update pak může považovat gateway za volnou a přerušit přijatou práci.

**Doporučení:** sjednotit in-process active-work scope pro úplnou životnost turnů a continuations; použít jej pro admission barrier a drain. Bounded keepalive vázat na skutečně živou práci. Zachovat lokální **fail-closed** runtime guard; veřejný `active_agent_work_count()` z pinu není jeho úplná náhrada, protože při chybě může vrátit nulu a nepokrývá všechny lokální worker-thread/model-tool scopes.

Evidence: `bridge\app.py:136,153–156,2716–2735`; `bridge\event_worker.py:218–240`; `scripts\sandbox_services.py:414–445`; `runtimes\hermes\runtime_routes.py:100–126`; [pinned activity counter][P9].

**Co není prokázáno:** přesný Agent User ingress timeout a suspend přesně v čase 300 s. Konfigurace idle timeoutu sama není pozorovaný výpadek. Doporučení prostě zapnout `long_running_messages=True` bylo staženo: lokální Agents SDK 1.1.0 má v této cestě nekompatibilní kwargs a samo volání awaituje.

**Brána opravy:** source update neprojde během příloh, background pokračování ani závěrečného Teams sendu. Samostatně změřit dlouhý skutečný turn bez podpůrného health pollingu, který by sám bránil suspendu.

### R4. Úzká oprava kontextu po obnově gateway

Obnovený deque se správným `maxlen` se nevloží do `_teams_memory`. Append přidá novou událost do dočasné instance; následné načtení pro persistenci znovu obnoví starý stav a novou událost ztratí.

**Doporučení:** obnovenou instanci uložit do cache a append/save provést nad stejným objektem. Zachovat bounded historii i současný journal. Není důvod přidávat další databázový produkt.

Evidence: `bridge\app.py:589–611`. Nález původně přinesl Astra; oba ostatní jej potvrdili.

**Brána opravy:** předem uložená historie po vyprázdnění cache přijme nový event, zachová ho po reloadu a stále dodrží limit posledních N událostí.

### R5. Korelovat používaný run lifecycle, ne starý endpoint

Trace handoff stále rozpoznává starou session-chat routu, zatímco adapter používá `/v1/runs`. Bridge-side filesystem handoff navíc nemůže přejít mezi oddělenými Sandboxy. Samostatně allowlist nepovoluje `transport.process`, takže span degraduje na `internal`.

**Doporučení:** předat metadata korelace na runtime hranici a navázat je na skutečný admission/executor/run lifecycle, i po HTTP 202. Opravit span naming a privacy-safe atributy. Nezaměnit přidání `run_id` pro diagnostické spojení za automaticky správné parent-child spans.

Evidence: `bridge\runtime\hermes.py:1023–1130`; `runtimes\hermes\start_hermes.py:692–741`; `bridge\hermes_telemetry.py:187–210`; `bridge\telemetry.py:36–53,175–182`.

Foundry registrace podle runtime typu místo jména Workera je další **modelovací volba**. Worker ID v atributech nezmizí; nebylo prokázáno zničení všech traces ani dopad do Purview. Unikátní entity zavést, pokud je cílem per-Worker produktová karta.

**Brána opravy:** skutečný pinned Hermes proces propojí Teams i Dream s model/tool prací, nesmíchá dvě sessions a neexportuje prompty, dokumenty ani tool argumenty. Upgrade ani komunitní OTel plugin nejsou důkazem opravy.

## 4. High-impact zjednodušení s podmínkami

Shoda je zde na hodnotě cíle či experimentu, **ne na ověřené zaměnitelnosti produktu**.

| Směr | Co může skutečně odpadnout | Podmínka před změnou |
|---|---|---|
| **E1: Connector → autentizované HTTPS gateway** | `executeShellCommand`, shell/base64 trampoline a broad SandboxGroup Data Owner pro tento callback | Skutečný trigger probudí suspendovaný OnDemand port; projde správná MI/audience, cizí principal ne; zachová se envelope, dlouhý lease a settlement |
| **E2: Connection access policy pro gateway principal** | Remote caller-owned creator bootstrap, dočasné RBAC granty a část deployment choreografie | Ne-creator identita obnoví a dokončí právě trigger-owned PeekLock přes správnou connection; obyčejný úspěšný `invoke` nestačí |
| **E3: Trusted cron origin v runtime** | Globální inventory diff a dodatečné bind/reconcile HTTP operace při každém turnu; následně část velkého lock scope | Origin je atomicky svázaný s jobem z důvěryhodného invocation contextu; dvě konverzace, timeout a souběžný cron nevedou k cizímu bindingu |
| **E4: Behaviorální learning release brána** | Response-only eval jako jediný důkaz kvality promotion | Reálný omezený Worker/test tenant ověří native skill discovery, skutečné tool/result outcome a holdout; bez klonu soukromé paměti |
| **E5: Lazy MCP a release-bound schema cache** | Zbytečné startup warming/discovery vlastních nepoužitých serverů | Koordinovaná lazy/cache/invalidation policy funguje po změně role, identity a schématu i token expiry; měřit cold, replacement a Memory resume odděleně |
| **E6: Jedna regionální topologie, případně sdílené demo MCP** | Cross-region peering a/nebo dvě servisní kopie na každého Workera | Ověřit konkrétní regiony/kvóty/private ingress, multi-principal authorization a přijatelný change blast radius; nezaměnit počet resources za cenu |

### Největší příležitost kolem Azure: dva oddělené connector experimenty

[Sandbox Triggers][P2] a [Connectors][P3] popisují přímý webhook/port směr; [Connectors preview CLI][P4] popisuje access policies. To je dobrý důvod k malému PoC. **E1 a E2 nejsou jeden kontrakt:** úspěšný HTTPS callback nedokazuje delegované lease settlement a úspěšná access policy nedokazuje OnDemand wake s danou identitou.

Po úspěchu odstranit skutečně nahrazený shell/bootstrap/RBAC kód, ne ponechat druhou kompatibilní cestu. Při neúspěchu nepřidávat relay nebo nový orchestrátor jen kvůli demonstraci. Současné receipts, lease ownership a fail-closed unknown semantics musí zůstat.

### Největší vnitřní zjednodušení: původ cronu, potom souběh

Opus upozornil, že globální zámek chrání i správnost before/after diffu cron inventáře. Astra to doložil konkrétním interleavingem a Gemini svůj původní návrh plošného odemčení stáhl.

**Pořadí je zásadní:** trusted origin/binding v runtime → odstranit globální diff → teprve bounded concurrency mezi konverzacemi. Native FIFO jedné session nechrání globální inventář. Model nesmí mít možnost zvolit cizí delivery reference jako „trusted origin“.

Nyní lze oddělit některá informativní čtení. Learning/document mutace nelze hromadně uvolnit jen proto, že používají file lock nebo atomic rename; read/check/write celé operace tím ještě není atomické.

Evidence: `bridge\runtime\hermes.py:358–455`; `runtimes\hermes\cron_runtime.py:150–172`; `runtimes\hermes\native_learning.py:437–503`; `autopilots_identity\document_operations.py:432–529`.

### Learning: věrnější ověření, ne složitější distribuované učení

Text skillu přiložený přímo do promptu a substring kontrola odpovědi neprokazují, že jej Worker umí sám najít a správně použít. To je důležité před rozšířením změny do více Workerů.

Shoda je na malé reálné behaviorální bráně; podoba harnessu není rozhodnutá. Zachovat owner review, privacy/ledger kontroly a explicitní promotion. Nezavádět neodsouhlasené fixture simulace, swarm ani „Dream jako PR“ nad kopií živé osobní paměti. Evidence: `scripts\evaluate_learning.py:33–41`.

## 5. Co druhé kolo opravilo nebo odmítlo

| Původní návrh/tvrzení | Výsledný závěr |
|---|---|
| „Stačí odstranit globální lock“ | Ne: nejprve opravit trusted cron origin a konkrétní mutační invarianty |
| „Zapnout long-running flag, protože Agent 365 má jistě 15s timeout“ | Staženo: přesná Agent User deadline nebyla doložena; SDK 1.1.0 navíc blokuje tento jednoduchý návrh |
| „Všechny operace bezpečně pokryje public activity counter“ | Ne: zachovat lokální fail-closed guard a worker-thread/model-tool scopes |
| „V novém Hermes získáme `/stop`, cron claims, cache a curator lock“ | Tyto příklady nejsou doloženým upgrade delta; relevantní schopnosti jsou již v pinu |
| „Nový OTel plugin opraví korelaci“ | Ne: katalogová položka existuje již v pinu a deklaruje full-content capture; to není metadata-only řešení |
| „Odstranění warm-up hned ušetří startup a Memory resume“ | Podmíněné: lazy je výchozí false; eager discovery může udělat stejnou práci. Startup lifespan se při Memory resume neopakuje |
| „Schema cache přežije replacement díky native backupu“ | Ne: pinned backup ji vylučuje. Cache na root disku není automaticky součástí restore archivu |
| „Default-deny egress je univerzální low-risk fix“ | Jen volitelný profil role: jinak se odebere zamýšlený webový výzkum |
| „Egress MI Transform nahradí Agent User proxy“ | Ne: přímý MI token není Blueprint → Agent Identity → `user_fic` se stejnou identitou, consentem a oprávněními |
| „Trace chyba je kritický výpadek / cold start bude pod 7 s“ | Stažena neprokázaná závažnost a neměřené latence; microVM start není application readiness |

### Hermes upgrade: rozhodovat podle používaného source kontraktu

Posuzovaný pin je **v0.22.6**. Poslední v tomto výzkumu načtený stable release byl **v0.23.1**, publikovaný 10. 11. 2026 v 01:42:15 UTC; nejde o nepřetržité ověření latest.

Nejsilnější druhokolová kontrola Opusu porovnala tagované git trees a konkrétní source. Provider, části cron kontraktu, API route tabulka, schema cache a OTel katalog už v pinu existují; některé mají mezi tagy identické bloby. Nové ověřené hooks `on_session_open`, `post_curator_run` a `conversation_id` mohou pomoci origin bindingu, ale není prokázáno, že automaticky nesou vše potřebné pro tento API-server tok.

Naopak lokální provider **skutečně monkeypatchuje** `save_job_output` a `run_one_job`; cíl `cron/scheduler.py` se mezi tagy změnil. Původní Opus tvrzení, že monkeypatche nejsou, bylo opraveno.

**Doporučení:** žádný upgrade jen podle release headline. Přínos doložit proti skutečnému pinu a pustit kontraktní bránu pro Runs/idempotency/recovery, cron output capture, backup/restore a learning hooks. Netvrdit, že mezi verzemi nejsou jiné užitečné změny; jejich přínos pro tuto integraci nebyl kompletně ověřen. Závislosti případně ověřit ve schváleném feedu, nic se v tomto review neinstalovalo.

Primární source: [pinned scheduler provider][P6], [pinned Runs][P7], [pinned schema cache][P10], [backup][P11], [OTel katalog][P12], [candidate plugins/hooks][P13].

## 6. Menší dopad a exotičtější směry

| Nápad | Hodnocení |
|---|---|
| Dream watermark nad novou meaningful Work History | Rozumný omezený experiment. Při nulové nové práci přeskočit inferenci, ale zapsat skipped/heartbeat a správně posunout schedule; neměřit aktivitu vlastními Dream logy |
| Journal GC a stáří záloh | Důležitá provozní hygiena. Nejdřív korektní terminalizace; retenci odvodit od replay politiky. Interval pokusu 60 s není hard RPO ≤1 minuta |
| Manifest upstream dependency surface | Levná pomůcka: sledovat používané soubory/hooky a blob změny při bumpu. Nenahrazuje testy nezměněných rozhraní ovlivněných tranzitivní změnou |
| Sterilní committed images pro thin services | Může odstranit instalaci závislostí z replacement cesty. Necommitovat živý Worker home, credentials ani identitu; application-ready latenci změřit |
| Sdílení token-exchange helperu | Odstranit duplicitu kódu při zachování identity/audience/scope semantics; nepřidávat nový sidecar jen kvůli refaktoringu |
| Změna observe-unmentioned defaultu | Volba vlastníka mezi ambientní spoluprací a cenou, ne zjevná chyba; nejprve změřit počet tichých modelových turnů |
| Jednotný Work IQ MCP | Zajímavá budoucí redukce integrací, ale gated na skutečný Agent User token, licenci a oprávnění; generická dostupnost konektoru nestačí |
| Egress Transform/hook pro tokeny mimo runtime | Exotičtější experiment pro vhodné interní hopy; federovaný Agent User kontrakt nemusí odstranit, pouze přesunout vlastní broker |
| Dokumentační a drobný kódový úklid | Opravit neexistující `demo_ops status --invoke`, pozůstatky recovery diagramů, duplicitní helper a staré docstringy; odvozené HTML/PDF regenerovat až podle opraveného zdroje |

**Praktické pořadí:** nejprve R1–R4 a korektní scheduled lifecycle; potom R5 a malá kontraktní/live ověřovací matice. Z architektonických experimentů prioritizovat E1/E2 a E3. Ostatní dělat až podle změřené režie a zamýšleného classroom scénáře. Zjednodušení má odstranit skutečnou odpovědnost, ne pouze přesunout stejný stavový automat jinam.

## 7. Evidence manifest a auditovatelnost

### Pokrytí tříd zdrojů

| Požadovaná třída | Stav | Nástroje / skutečný rozsah | Omezení |
|---|---|---|---|
| Aktuální lokální source, konfigurace, testy a SDK | searched | `view`, `rg`, `glob`, read-only Git/PowerShell; tři nezávislé posudky, druhokolová kontrola konkrétních toků | Testy se pouze četly; SDK 1.1.0 není aktuální online API reference |
| Oficiální Azure / Entra / Agent 365 dokumentace | searched | Microsoft Learn search/fetch, veřejné Sandbox docs a upstream Connectors docs; v R1 Astra 14 retrievalů, Gemini 12, Opus smíšený soupis | Počty nejsou počet nezávislých důkazů; preview není live podpora tohoto nasazení |
| Hermes upstream releases a source | searched | Tagované `gh api`/GitHub reads/search; R1 Astra 7, Gemini 11; R2 Astra 8, Opus přibližně 14 fetchů v přibližně 7 voláních | Některé raw čtení byla cílená, ne audit celého souboru; compare endpoint byl oříznut a nepoužit |
| Schválený package feed | searched | Gemini zkontroloval 4 balíčky; Opus uvedl feed v R1 manifestu | Dostupný wheel není ověřená kompatibilita; nic se neinstalovalo |
| Interní technické materiály OneDrive/SharePoint | searched, samostatná soukromá příloha | Po počátečním auth blokování tři úspěšné WorkIQ grounding retrievaly, 6 + 3 + 3 fragmenty | Žádná interní evidence není rozhodujícím podkladem tohoto dokumentu; bez úplného interního auditu |
| Komunita / posledních 30 dní | excluded | 0 cílených průzkumů | Výslovná volba vlastníka |
| Live Azure/Teams, náklady, dostupnost konkrétního tenantu | neprovedeno | 0 | Nelze tvrdit provozní ověření ani změřené úspory |

### Rozhodující veřejné zdroje

Zdroje jsou citovány podle skutečného načtení v expertních posudcích; koordinátor je znovu nestahoval pouze kvůli potvrzení peer práce. `searched` není záruka celého přečteného dokumentu, pokud manifest uvádí cílený excerpt.

| ID | Zdroj | Verze/datum | Nástroj a stav | Co podkládá |
|---|---|---|---|---|
| P1 | Sandbox lifecycle | Page update 2026-11-09 | `web_fetch`, searched / 1 page | Suspend/resume a podmínky lifecycle |
| P2–P3 | Sandbox Triggers / Connectors | Page update 2026-11-09 | `web_fetch`, searched / 2 pages, v obou kolech | Smysluplnost přímého callback experimentu, ne jeho live úspěch |
| P4 | Azure Connectors preview CLI README | `main`, commit datum nezjištěno | `web_fetch`, searched / 2 navazující části u Astry | Connection access policies, nikoli prokázaná lease equivalence |
| P5 | Service Bus dead-letter queues | Přečteno 2026-11-12 | Learn fetch, searched / 1 page | Broker DLQ, delivery count a skutečnost, že DLQ není prostě běžný TTL cleanup |
| P6 | `cron/scheduler_provider.py` | v0.22.6 / v0.23.1, shodný blob `60939793351e4220c579f5a5600d6c410ef86363` | `gh api`, searched / 2 tagged files | Existující claims/fire kontrakt a experimental interface |
| P7 | `gateway/platforms/api_server_runs.py` | v0.22.6, blob `599812a11751ae3228b29bfcbe2701ec785c2b6e` | `gh api` / GitHub reads, searched | Skutečné pinned Runs routes a run lifecycle |
| P8 | `cron/jobs.py` | v0.22.6, blob `1809214b9df26595d7a964f920ed47bb5bac927e` | GitHub read / `gh api`, searched | Rozlišení load a claim, podklad re-arm analýzy |
| P9 | `gateway/platforms/api_server.py` | v0.22.6, blob `a66cb6d29d7ebc198058b1c1cc85569ddd0434c7` | `gh api` / GitHub read, searched | Counter v pinu a jeho fail-open omezení |
| P10 | MCP schema cache + discovery | v0.22.6 | `gh api` / GitHub reads, searched / 2 files | Cache, lazy default a invalidace |
| P11 | `hermes_cli/backup.py` | v0.22.6, blob `ed6d22dbefcfd11b1d4f515119a2d1c92be397ef` | `gh api`, searched / 1 file | Vyloučení schema cache z backupu |
| P12 | `plugin-catalog/hermes_otel.yaml` | v0.22.6 | `gh api`, searched / 1 manifest | Katalog existuje; default content capture není bezpečný metadata-only režim |
| P13 | `hermes_cli/plugins.py` | v0.22.6 / v0.23.1 | `gh api`, searched / 2 files | Ověřené nové hooks, ne záruka origin contextu |
| P14 | Agent User OAuth flow | Update datum nevráceno | Learn fetch, searched / 1 page | Nezaměnitelnost MI a Agent User federace |
| P15 | Agent 365 message handling | Přečteno 2026-11-12 | Learn fetch, searched / 1 page u Gemini | Relevantní produktový příklad; ne důkaz obecného 15s deadline |
| P16 | Hermes releases | Poslední načtený v0.23.1 published 2026-11-10 UTC | `gh api` / GitHub release reads, searched | Datace a kandidátní změny; release notes nejsou samy pin delta |

Další konzultované, nepoužité, prázdné a blokované zdroje zachovávají **úplné manifesty jednotlivých kol** níže. Zvláště: Geminiho pokus o Azure-Samples sample skončil SAML 403, nepoužitý GitHub compare byl oříznut a code search `path:plugins` nebyl úplným průzkumem katalogu. Tyto mezery se neobcházely a nejsou vydávány za neexistenci schopnosti.

### Původní posudky s úplnými manifesty

Původní posudky a úplné manifesty jsou nepublikované lokální podklady mimo Git checkout. Následující přehled popisuje jejich původ; neobsahuje veřejné odkazy:

| Expert | Kolo 1 | Kolo 2, které má přednost |
|---|---|---|
| Astra | Lokální posudek prvního kola | Lokální posudek druhého kola |
| Opus | Lokální posudek prvního kola | Lokální posudek druhého kola |
| Gemini | Lokální posudek prvního kola | Lokální křížové posouzení druhého kola, včetně §6 opravného addenda |

**Výsledná míra jistoty:** silná u konkrétních lokálních nesouladů R1–R5; podmíněná u skutečného odstranění platformních vrstev E1–E6. Přesné Agent User timeouty, Sandbox idle sémantika otevřeného HTTP, delegované settlement a skutečné nákladové přínosy zůstávají cíli následného ověření. Implementace ani Azure prostředky se tímto review nezměnily.

[P1]: https://sandboxes.azure.com/docs/sandboxes/sandbox/lifecycle
[P2]: https://sandboxes.azure.com/docs/sandboxes/triggers
[P3]: https://sandboxes.azure.com/docs/sandboxes/connectors
[P4]: https://github.com/Azure/Connectors/blob/main/public-preview/connector-namespace-cli/README.md
[P5]: https://learn.microsoft.com/azure/service-bus-messaging/service-bus-dead-letter-queues
[P6]: https://github.com/NousResearch/hermes-agent/blob/v0.22.6/cron/scheduler_provider.py
[P7]: https://github.com/NousResearch/hermes-agent/blob/v0.22.6/gateway/platforms/api_server_runs.py
[P8]: https://github.com/NousResearch/hermes-agent/blob/v0.22.6/cron/jobs.py
[P9]: https://github.com/NousResearch/hermes-agent/blob/v0.22.6/gateway/platforms/api_server.py#L1122-L1132
[P10]: https://github.com/NousResearch/hermes-agent/blob/v0.22.6/tools/mcp_schema_cache.py
[P11]: https://github.com/NousResearch/hermes-agent/blob/v0.22.6/hermes_cli/backup.py#L99-L122
[P12]: https://github.com/NousResearch/hermes-agent/blob/v0.22.6/plugin-catalog/hermes_otel.yaml
[P13]: https://github.com/NousResearch/hermes-agent/blob/v0.23.1/hermes_cli/plugins.py
[P14]: https://learn.microsoft.com/entra/agent-id/agent-user-oauth-flow
[P15]: https://learn.microsoft.com/microsoft-agent-365/developer/message-handling
[P16]: https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1
