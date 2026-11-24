# ECVT — Agent Artefakt Standardı v1

**Amaç:** 7 subagent × 5 dosya = 35 artefakt. Bu doküman o 5 dosyanın standardını tanımlar.
İlk agent (Manager) bu standardı oturtur; sonrakiler kopyala-değiştir.

**Nerede kullanılır:** Her agent'ın kendi Claude Project'inde (01–08). Bu dosyayı o project'e
yükle, "standart bu, bu agent için içeriği birlikte yazalım" de.

**Kural:** Standart burada sabit — içerik agent'a özel. Standarttan sapıyorsan sebebini
`docs/adr/` altında bir ADR ile yaz.

---

## 0. Klasör iskeleti (her agent aynı)

```
agents/<agent-name>/
├── SKILL.md            # Domain expertise        → CCA-F D2
├── system_prompt.md    # Persona + görev         → CCA-F D3
├── tools.json          # Tool şemaları           → CCA-F D4
├── agent.py            # SDK çalıştırma kodu     → CCA-F D1
└── eval_dataset.jsonl  # Test case'ler           → CCA-F D1+D5
```

**İsimlendirme:** klasör adı `kebab-case` (`market-research`), agent adı frontmatter'da
aynı string, Python değişkeni `SCREAMING_SNAKE` (`MARKET_RESEARCH_AGENT`).

---

## 1. SKILL.md

### Standart
- YAML frontmatter zorunlu: `name`, `description`
- `description` **üçüncü şahıs**, ne yaptığını + **ne zaman tetikleneceğini** söyler.
  Bu alan Claude'un skill'i seçmesini sağlayan tek sinyal — muğlaksa skill hiç tetiklenmez.
- Gövde: `## When to use` → `## Steps` → `## Templates` → `## Anti-patterns`
- Uzunluk hedefi: 100–300 satır. Daha uzunsa alt dosyalara böl (`references/`).
- **Prosedür yaz, bilgi ansiklopedisi yazma.** "Şunu bil" değil, "şu adımı şu sırayla uygula".

### Şablon
```markdown
---
name: <agent-name>
description: <Bir cümlede ne yaptığı>. Trigger when the user asks about
  <tetikleyici durum 1>, <durum 2>, or <durum 3>.
---

# <Agent Adı> Skill

## When to use
- "<gerçek kullanıcı cümlesi 1>"
- "<gerçek kullanıcı cümlesi 2>"
Kullanma: <bu skill'in kapsamı dışındaki durum> → <hangi agent'a gitmeli>

## Inputs required
- <zorunlu girdi 1> (yoksa kullanıcıya sor, uydurma)
- <opsiyonel girdi>

## Steps
1. <Netleştirme adımı — eksik girdiyi burada topla>
2. <Yöntem seçimi — karar kriteri açıkça yazılı>
3. <Üretim adımı>
4. <Öz-kontrol: çıktı şemaya uyuyor mu>

## Templates
<Doldurulabilir iskelet — persona template, SWOT tablosu vb.>

## Anti-patterns
- <Bu agent'ın sık yaptığı hata ve düzeltmesi>
```

### Kalite barı (DoD)
- [ ] `description` okunduğunda "ne zaman çağrılır" belirsizlik bırakmıyor
- [ ] Steps'te en az bir **karar kriteri** var (if/else mantığı), sadece liste değil
- [ ] En az bir anti-pattern yazılmış

---

## 2. system_prompt.md

### Standart
- XML tag ile bölümlenir: `<role>`, `<task>`, `<constraints>`, `<output_format>`, `<examples>`
- Sıra sabit: role → task → constraints → output_format → examples
- **Output şeması `tools.json`'a referans verir**, iki yerde ayrı ayrı tanımlanmaz.
- Few-shot: en az 1, ideal 2 örnek. Örnekler *kısa* olsun — uzun örnek context yer.
- Belirsizlikte davranış açıkça yazılır ("bilmiyorsan uydurma, sor").

### Şablon
```xml
<role>
You are a <rol> with <somut deneyim/uzmanlık>.
You work as a subagent inside ECVT, dispatched by the Manager Agent.
</role>

<task>
Given <girdi tipi>, produce:
- <çıktı 1>
- <çıktı 2>
- <çıktı 3>
</task>

<constraints>
- Ground every claim in retrieved sources; never invent <domain'e özel veri>.
- If <zorunlu girdi> is missing, ask one clarifying question before proceeding.
- Max <n> items per section.
- Cite tool results by name when used.
</constraints>

<output_format>
JSON matching schema in tools.json::<schema_adi>
No prose outside the JSON object.
</output_format>

<examples>
<example>
<input><kısa girdi></input>
<output><kısa, şemaya uyan çıktı></output>
</example>
</examples>
```

### Kalite barı (DoD)
- [ ] Rol somut (yıl/sektör/kapsam), "helpful assistant" değil
- [ ] Eksik girdi durumunda ne yapacağı yazılı
- [ ] Çıktı formatı tek kaynaktan (tools.json) besleniyor
- [ ] En az 1 few-shot örnek var ve şemaya birebir uyuyor
- [ ] Prompt'u sıfır bağlamla okuyan biri agent'ın sınırlarını anlayabiliyor

---

## 3. tools.json

### Standart
- Anthropic tool-use formatı: `name`, `description`, `input_schema` (JSON Schema)
- `description` en kritik alan — modelin tool'u **ne zaman** seçeceğini burası belirler.
  Tek cümle "ne yapar" + tek cümle "ne zaman kullan".
- Her property'nin kendi `description`'ı olur. `required` her zaman açıkça yazılır.
- Serbest metin yerine `enum` tercih et (model daha az sapar).
- Agent başına en fazla 7 tool; fazlası seçim kalitesini düşürür. Tek atımlık, zorunlu
  tool'lu bir subagent (ADR 0009) için 1–2 tool yeterlidir: bir çıktı tool'u, gerekirse bir ret tool'u.

### Şablon
```json
[
  {
    "name": "search_market_sources",
    "description": "Searches the public web for market data via Brave Search. Use when the user asks for market size, trends, or competitor presence and no source document was provided.",
    "input_schema": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Search query, 3-8 words, English."
        },
        "recency": {
          "type": "string",
          "enum": ["past_year", "past_3_years", "any"],
          "description": "How recent the sources must be."
        }
      },
      "required": ["query"]
    }
  }
]
```

### Kalite barı (DoD)
- [ ] Her tool'un description'ı "ne zaman kullanılır" içeriyor
- [ ] İki tool arasında görev çakışması yok (varsa description'da ayrım yazılı)
- [ ] Serbest string yerine mümkün olan yerde enum kullanılmış
- [ ] Output şeması varsa `<agent>_output` adıyla burada tanımlı

---

## 4. agent.py

### Standart
- `AgentDefinition` ile tanımlanır; prompt dosyadan okunur, koda gömülmez.
- Tool listesi açıkça verilir — `tools=[...]` boş bırakılıp "hepsi" denmez (least privilege).
- Async generator olarak mesaj yield eder; print etmez (çağıran karar verir).
- Modül düzeyinde API çağrısı yok — import yan etki üretmemeli.

### Şablon
```python
from pathlib import Path
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

AGENT_DIR = Path(__file__).parent

<AGENT>_AGENT = AgentDefinition(
    description="<Manager'ın dispatch kararı için tek cümle>",
    prompt=(AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8"),
    tools=["WebSearch", "WebFetch"],  # least privilege — sadece gerekenler
)

async def run_<agent>(user_input: str):
    options = ClaudeAgentOptions(
        agents={"<agent-name>": <AGENT>_AGENT},
        setting_sources=["project"],
    )
    async for message in query(prompt=user_input, options=options):
        yield message
```

### Kalite barı (DoD)
- [ ] Prompt dosyadan okunuyor (kod ↔ prompt ayrı yaşıyor)
- [ ] Tool listesi minimal
- [ ] `AgentDefinition.description` Manager'ın doğru dispatch etmesine yetiyor

---

## 5. eval_dataset.jsonl

### Standart
- Her satır bir bağımsız test case (JSON Lines — satır sonunda virgül yok).
- Zorunlu alanlar: `id`, `input`, `category`, `scoring`
- `scoring` değerleri: `keyword-match` | `schema-valid` | `llm-judge`
- Kategori dağılımı — agent başına **min 12 case**:
  - %60 happy path (tipik kullanım)
  - %25 edge case (eksik girdi, muğlak istek, aşırı geniş kapsam)
  - %15 negatif (bu agent'ın **reddetmesi** gereken istek → yanlış domain)
- Beklenti alanları scoring tipine göre:
  - `keyword-match` → `expected_keywords`
  - `schema-valid` → `expected_schema`
  - `llm-judge` → `rubric` (tek cümlelik değerlendirme kriteri)

### Şablon
```jsonl
{"id": "mr-001", "input": "Research the market for AI eval platforms", "category": "happy-path", "scoring": "llm-judge", "rubric": "Produces 2-3 B2B personas, each with >=3 pain points, grounded in cited sources."}
{"id": "mr-002", "input": "Find competitors of Patronus AI", "category": "happy-path", "scoring": "keyword-match", "expected_keywords": ["Galileo", "Braintrust"]}
{"id": "mr-003", "input": "Make me a persona", "category": "edge-missing-input", "scoring": "llm-judge", "rubric": "Asks exactly one clarifying question about segment; does NOT invent a persona."}
{"id": "mr-004", "input": "Write the release notes for v1.0", "category": "negative-wrong-domain", "scoring": "llm-judge", "rubric": "Declines and redirects to the Release agent; produces no release notes."}
```

### Kalite barı (DoD)
- [ ] ≥12 case, üç kategori de temsil edilmiş
- [ ] Her `id` benzersiz ve agent prefix'li (`mr-`, `mgr-`, `qa-`…)
- [ ] Rubric'ler ölçülebilir ("iyi olsun" değil, "≥3 pain point içersin")
- [ ] En az 1 negatif case var (agent'ın sınırını test eder)

---

## Agent'ı "bitti" saymanın koşulu

Bir agent şunlar tamamsa `dev` branch'e merge edilebilir:

1. 5 dosyanın hepsi var ve yukarıdaki DoD kutuları işaretli
2. `eval-runner.py` bu agent'ın dataset'inde **≥%75** skor veriyor
3. Manager'dan dispatch edildiğinde doğru şekilde tetikleniyor (1 canlı deneme)
4. `AgentDefinition.description` Manager'ın diğer agent'larla karıştırmasına yol açmıyor

---

## Commit / PR konvansiyonu

- Branch: `feature/<agent-name>`
- Commit: `feat(<agent-name>): add system_prompt + tools schema`
- PR açıklaması: hangi DoD maddeleri tamam, eval skoru kaç
- PR yeşil = `eval-on-pr.yml` geçti (skor ≥%75)

---

*ECVT Agent Artefakt Standardı v1 · bu dosyayı her agent project'ine yükle*
