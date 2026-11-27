# ECVT Roadmap · Etap 2 (v1.x → v2.0.0)

Son güncelleme: 27 Kas 2026 · Kaynak: ECVT Master Plan v7 (Claude Project 00)

**Durum bu dosyada tutulur.** Her PR ilgili kutuyu işaretler ve yanına PR linkini yazar.
Bir milestone; kapı listesi tam, `python scripts/gate_check.py Mx` PASS ve tag atılmışsa kapanır.

## Etap 1

- [x] M0 · kurulum + wallet-api · `v0.0.1`
- [x] M1 · Scenario → Execution → Evaluation zinciri · `v0.3.0`
- [x] M2 · 5 agent, Manager üzerinden · `v0.5.0`
- [x] M3 · plugin, MCP eval, rapor, n8n, arayüz, landing + 60 sn video · `v1.0.0` → `v1.0.1`
  - G3 PASS + tag `v1.0.0` (#47) · arayüz #49, #50 (ADR 0016) · roadmap #51 · kapanış: v1.0.1 PR · video ertelendi (ADR 0015)
- [x] G2 resmî kapanış: main'de `gate_check.py M2`; demo maddesi G3'e taşındı · 2026-11-27: 9/9 PASS (G2-4: a01 %100, a02 %83,3 havuzlanmış), `fix/correction-answers-every-tool-use` ile

## İlkeler

1. **Agent yalnız karar gereken yerde.** Yeni her rol önce şu kararla başlar: agent döngüsü, tek LLM adımı ya da kod (ADR 0017).
2. **Ölçüm disiplini.** `eval_runner --runs N` (N ≥ 5; iki konfigürasyon kıyası n ≥ 10), havuzlanmış sonuç, her seferinde tek değişiklik.
3. **Ground truth** agent'a, prompt'a ve few-shot örneklerine girmez.
4. **Hedef sistemden gelen her metin güvenilmez girdidir;** prompt'a talimat olarak girmez.
5. **Haiku varsayılan.** Daha büyük model yalnız ölçümle gerekçelendirilir.
6. **Maliyet** koşu başına `run.json`'a yazılır.

## Rol kararları (öneri · ADR 0017 ile kesinleşir)

| Rol | MS | Biçim | Girdi → çıktı |
| --- | --- | --- | --- |
| a07 Regression | M5 | Kod + tek LLM adımı (fark açıklaması) | koşu geçmişi → fark raporu + alarm |
| a04 Acceptance | M5 | a03 modu + rapor kodu | kabul kriterleri → kriter etiketli senaryolar + kapsam tablosu |
| a08 Reporting | M6 | Kod + tek LLM adımı (Business özeti) | run.json + skorlar → Dev / QA / Business raporu |
| a09 Knowledge | M7 | Agent | koşu geçmişi → pattern'ler + a03 ipuçları |

## M4 · Güvenilirlik ve ölçüm · `v1.1.0` · 28 Kas – 6 Ara

- [x] M4-T1 a03 strict tool use / structured outputs: "Field required" 0/8 · ölçüldü: 0/8, ilk denemede 8/8 (main 4/8), catch rate 7/8/8 · ADR 0019 · #54
- [ ] M4-T2 DomainModel'e operation parametreleri alanı (di-010). ADR 0012 = B gereği DomainModel a03'e verilmez
- [x] M4-T3 Manager: LLM pipeline'ı seçer, deterministik kod yürütür; sabit zincirle catch rate + maliyet kıyası (n ≥ 10); ardından Haiku eval, ≥ %80 ise Haiku · planner 10/10, catch 6/7/9 (zincir 7/7/8), $0.021/koşu (eski döngü $0.077); a01 Haiku %100 → varsayılan Haiku · ADR 0020 · #56
- [ ] M4-T4 Maliyet: yetim thread iptali, judge payı, `model_usage` ile ADR 0009'u yeniden değerlendir
- [ ] M4-T5 Model matrisi: a03 için Haiku / Sonnet / Opus, konfigürasyon başına 5 koşu
- [ ] M4-T6 Baseline: Claude Code + Opus tek oturum, Schemathesis (AI'sız) ve ECVT; 5'er koşu; hangi bug'ı kim yakaladı, varyans, maliyet
- [ ] M4-T7 Koşu bazlı izleme (OpenTelemetry veya self-host Langfuse)

Kapı G4

- [ ] 10 ardışık `ecvt run --manager` %100 tamamlanır
- [ ] a03 ilk denemede geçerlilik ≥ 7/8; Manager yolu catch rate'i sabit zincirin altında değil (n ≥ 10)
- [ ] Model matrisi + baseline raporu `docs/09-evaluation/` altında
- [ ] `gate_check.py M4` PASS + tag `v1.1.0`

## M5 · Regression + Acceptance · `v1.2.0` · 7–13 Ara

- [ ] ADR 0017: rol biçimleri (yukarıdaki tablo)
- [ ] M5-T1 a07 Regression: koşu geçmişi deposu, sürümler arası fark, eşik alarmı
- [ ] M5-T2 `eval-on-pr.yml`: PR'da otomatik koşu + regresyon yorumu
- [ ] M5-T3 Acceptance modu: a03'e kabul kriteri girdisi, kriter id'li senaryolar, kapsam tablosu kodla
- [ ] M5-T4 Judge girdisi izolasyonu + prompt injection testi

Kapı G5

- [ ] Bilerek regresyon eklenmiş bir PR, PR yorumunda yakalanır
- [ ] Acceptance modu ve a07 eval ≥ %80
- [ ] Enjeksiyon içeren hedef yanıtı judge skorunu değiştiremez (test)
- [ ] `gate_check.py M5` PASS + tag `v1.2.0`

## M6 · Gerçek domain + raporlama · `v1.3.0` · 14–20 Ara

- [ ] M6-T1 IKYS plugin: Azure VM'de staging kopya, anonimleştirilmiş veri; production'a asla dokunmaz
- [ ] M6-T2 Bug replay: IKYS git geçmişinde düzeltilmiş 5–10 bug geri alınır → ground truth
- [ ] M6-T3 Plugin ABC'ye opsiyonel `discover_domain` (ADR 0013)
- [ ] M6-T4 a08 Reporting: Dev, QA, Business; subdomain filtresi
- [ ] M6-T5 Statik HTML dashboard (GitHub Pages): koşu raporu + trend

Kapı G6

- [ ] `ecvt run --plugin ikys` tamamlanır; bug replay X/N raporlanır
- [ ] Üç rapor formatı tek komutla; dashboard yayında
- [ ] Repoda ve raporlarda kişisel veri yok (kontrol script'i)
- [ ] `gate_check.py M6` PASS + tag `v1.3.0`

## M7 · Knowledge + kapanış · `v2.0.0` · 21–30 Ara

- [ ] M7-T1 a09 Knowledge: fail pattern'leri, bilinen sorunlar, başarılı senaryo kütüphanesi; embedding ile benzer geçmiş fail → a03'e ipucu (bayrakla)
- [ ] M7-T2 Knowledge ölçümü: ipuçlu ve ipuçsuz a03, 5'er koşu
- [ ] M7-T3 README v2 + Mermaid mimari seti + case study (≤5 sayfa)
- [ ] M7-T4 Kapı G7 + `v2.0.0` + 2026 retrosu

Kapı G7

- [ ] Dokuz rolün tamamı (agent, LLM adımı veya kod olarak) `ecvt run --manager` altında çağrılabilir
- [ ] a09 eval ≥ %80; Knowledge ölçümü raporlu
- [ ] `gate_check.py M7` PASS + tag `v2.0.0`

**Kesme kuralı:** 20 Aralık'ta G6 kapanmadıysa M7 daralır: a09 yalnız pattern çıkarımı (embedding yok) + M7-T3. G7, 30 Aralık'ı geçmez.

## 2026 dışında · vizyon

Birim bazında erişim izolasyonu ve birimler arası etki bildirimi · kurum içi model sağlayıcı (Bedrock, Azure, yerel) · Jev A/B · LangGraph karşılaştırması.
