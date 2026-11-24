# a01_manager — Tasarım Notu v1

**Kapsam:** M2-T3, CLI'deki sabit sırayı Manager'a devretmek. **Durum:** tasarım tamam, entegrasyon DevLog_ECVT'de.

## 1. Temel karar: kim neye karar verir

| Katman | Karar verdiği | Nerede |
|---|---|---|
| LLM (Manager, sonnet) | intent etiketi, slot'lar, retry/dur, özet | `system_prompt.md` |
| Kod | plan (intent → stage listesi), sıra, önkoşul, attempt sayısı, state | `agent.py` → `INTENT_PLAN`, `precondition()` |
| AgentDefinition (haiku) | hiçbir şey — ince dispatch adaptörü | `build_subagents()` |
| Subagent (Messages API + zorunlu tool_use) | domain işi | a02/a03/a05/a06 |

"Manager sırayı devralıyor" = sırayı **LLM yürütüyor, kod zorluyor**. Sıra prompt'a emanet değil; üç katmanlı guard var:

1. **Prompt:** plan sırasıyla, tek tek, en fazla 1 retry.
2. **Tool önkoşulu:** her `run_<stage>` state'i okur; plan dışı, sıra dışı, upstream başarısız, 2. denemeden sonra → `blocked`.
3. **PreToolUse hook:** `run_<stage>`'i sadece `agent_type == <stage-subagent>` çağırabilir; Manager main thread'den direkt çağırırsa deny.

## 2. Mimari akış

```
CLI / n8n ──run_manager(text, run_id)──▶ StateStore.create (runs/<run_id>/state.json)
                │
                ▼
     query(system_prompt, agents=4 adapter, mcp_servers={ecvt}, tools=["Agent"])
                │
     Manager ──record_intent──▶ code: slots doğrula, plan + next_action hesapla
                │
                ├─ dispatch ─▶ Agent(domain-intel) ─▶ mcp__ecvt__run_domain_intel ─▶ a02.run() [Messages API]
                │              Agent(scenario-gen) ─▶ ...run_scenario_gen ─▶ a03.run()
                │              Agent(execution)    ─▶ ...run_execution    ─▶ a05.run()
                │              Agent(evaluation)   ─▶ ...run_evaluation   ─▶ a06.run()
                ├─ ask_user / reject / answer_status
                ▼
     manager_output ──▶ code: tutarlılık kontrolü (sahte "completed" reddedilir) ──▶ state.result
                │
     finalize_guard: manager_output yoksa (max_turns / bütçe / crash) run'ı failed kapatır
```

Artefaktlar context'e girmez: stage tool'u artefaktı `runs/<run_id>/<stage>.json`'a yazar, Manager'a sadece `artifact_ref` + ≤500 karakter özet döner.

## 3. Intent taxonomy (7)

| Intent | Plan | Zorunlu slot |
|---|---|---|
| `full_validation` | domain_intel → scenario_gen → execution → evaluation | target_system, plugin |
| `generate_scenarios` | domain_intel → scenario_gen | target_system, plugin |
| `domain_analysis` | domain_intel | target_system, plugin |
| `execute_scenarios` | execution → evaluation | scenario_set_ref, plugin |
| `evaluate_results` | evaluation | execution_ref |
| `run_status` | — | run_ref |
| `out_of_scope` (`wrong_domain` · `stage_not_available` · `framework_request`) | — | — |

`next_action` kodda: out_of_scope → `reject`; eksik/geçersiz slot veya confidence < 0.6 → `ask_user`; run_status → `answer_status`; aksi → `dispatch`. Tie-breaker kuralları `system_prompt.md` ve `SKILL.md`'de.

Yeni stage eklemek (M3: Acceptance/Regression/Reporting/Knowledge) = `STAGES` + `INTENT_PLAN` + bir `run_<stage>` tool + bir AgentDefinition. Prompt'ta sadece taxonomy satırı değişir.

## 4. Stage runner kontratı (a02_domain_intelligence / a03_scenario_generator / a05_execution / a06_evaluation tarafı)

```python
def run(stage_input: dict) -> dict
# stage_input = {"run_id", "stage", "slots", "upstream": {stage: abs_artifact_path}}
# return     = {"artifact": json, "summary": str, "metrics": dict, "usage": dict}
# hata       = raise StageError(msg, retryable=True|False)
```
`retryable=True`: rate limit, timeout, şemaya uymayan tool_use çıktısı. Diğer her exception → retryable=False.

## 5. Pipeline state — örnek (tamamlanmış full_validation, kısaltılmış)

Şema: `pipeline_state.schema.json`. State'i sadece kod yazar.

```json
{
  "schema_version": "1.0",
  "run_id": "run_20261102T163000Z_7f3a",
  "status": "completed",
  "created_at": "2026-11-02T16:30:00+00:00",
  "updated_at": "2026-11-02T16:34:12+00:00",
  "request": {"raw_input": "Validate the IKYS leave approval flow end to end.", "source": "cli"},
  "intent": {
    "name": "full_validation", "confidence": 0.93,
    "rationale": "'Validate end to end' with a named target and plugin.",
    "slots": {"target_system": "IKYS leave approval flow", "plugin": "ikys"},
    "out_of_scope_reason": null, "next_action": "dispatch",
    "missing_slots": [], "invalid_slots": [], "recorded_at": "2026-11-02T16:30:04+00:00"
  },
  "plan": ["domain_intel", "scenario_gen", "execution", "evaluation"],
  "stages": {
    "domain_intel": {"agent": "a02_domain_intelligence", "status": "succeeded", "attempts": 1,
      "started_at": "…", "finished_at": "…",
      "artifact_ref": "run_20261102T163000Z_7f3a/domain_intel.json",
      "summary": "12 entities, 9 business rules, 4 risk areas.", "metrics": {}, "usage": {}, "error": null},
    "scenario_gen": {"agent": "a03_scenario_generator", "status": "succeeded", "attempts": 2, "…": "…"},
    "execution":    {"agent": "a05_execution",    "status": "succeeded", "attempts": 1, "…": "…"},
    "evaluation":   {"agent": "a06_evaluation",   "status": "succeeded", "attempts": 1, "…": "…"}
  },
  "result": {"run_id": "run_20261102T163000Z_7f3a", "status": "completed", "intent": "full_validation", "stages_run": ["…"], "summary": "…"},
  "manager_session": {"session_id": "…", "num_turns": 11, "total_cost_usd": 0.21, "is_error": false}
}
```

## 6. Eval

- 12 case: 7 happy / 3 edge / 2 negative (%58 / %25 / %17). Intent DoD'si = mgr-001…005 (5 keyword case).
- "4 subagent sırayla" DoD'si = mgr-006. llm-judge rubric'i var ama runner bunu deterministik de doğrulayabilir: `state.plan` + stage `started_at` sırası + hepsi `succeeded`.
- `keyword-match` → `record_intent` tool input'u üzerinde aranmalı (final metin değil).
- Eval **stub runner** ile koşar (`run_manager(..., runners=stubs, plugins=[...])`): ucuz ve deterministik. Standarttaki "1 canlı deneme" ayrı.
- Opsiyonel `setup` alanı (standart dışı, ek alan): `seed_runs` (mgr-007), `stub_runners` hata enjeksiyonu (mgr-010). eval-runner'ın bunu desteklemesi gerekiyor.
- Hedef ≥%80 (proje DoD) — standarttaki ≥%75'ten sıkı, çelişki yok.

## 7. Standarttan sapmalar / açık noktalar

| # | Konu | Karar |
|---|---|---|
| 1 | Case sayısı: brief 10, standart min 12 | 12 yazıldı (standarda uyuyor, ADR gerekmez) |
| 2 | `plugin` enum değil, string | Bilinçli: domain-agnostik, registry runtime'da doğruluyor. **ADR önerilir** |
| 3 | Skill `name: a01-manager`, klasör `a01_manager` | Skill adında `_` geçersiz; klasör adı repo konvansiyonu |
| 4 | Adaptör hop maliyeti: stage başına +1 haiku çağrısı (~4/run) | ADR 0009 gereği AgentDefinition var. Alternatif (Manager run_* tool'larını direkt çağırır) daha ucuz ama ADR metnine aykırı. Maliyet ölçülünce tekrar değerlendir |
| 5 | Delegation tool adı `Agent` (SDK 0.2.172) | Eski SDK'da `Task`; requirements'ta sürümü sabitle |
| 6 | a02 Domain Intelligence hâlâ iskelet (`NotImplementedError`) | "4 subagent" DoD'si için a02 en az stub olarak gerekir |
| 7 | Subagent'larda ortak `run(stage_input)` yok (`generate_scenario_set`, `run_scenarios`, `score_scenarios`) | `load_stage_runners()` adaptörleri DevLog'da yazılacak; o zamana kadar `runners=` ile enjekte |
| 8 | Intent taxonomy taslağı (`CLAUDE.md`: UNDERSTAND…FULL_PIPELINE) | Bu tasarımla değiştirildi: UNDERSTAND→domain_analysis, GENERATE→generate_scenarios, RUN→execute_scenarios, SCORE→evaluate_results, FULL_PIPELINE→full_validation; VALIDATE_AC/COMPARE/REPORT→out_of_scope/stage_not_available; +run_status |
| 9 | Dizin kuralı "her değişikliği gerçek Claude çağrısıyla test et" | Henüz yapılmadı; sadece LLM'siz state-machine testi var |

## 8. Doğrulananlar (bu oturumda, LLM'siz)

State makinesi stub runner'larla test edildi: sıra dışı çağrı bloklanıyor, sahte `completed` reddediliyor, retryable hata tam 2 deneme sonra bloklanıyor, upstream artefakt yolları doğru aktarılıyor, `finalize_guard` yarım kalan run'ı kapatıyor, hook main thread ve yanlış subagent'ı reddediyor, üretilen state'ler şemaya uyuyor. `tools.json`, state şeması ve prompt örnekleri JSON Schema ile doğrulandı.
