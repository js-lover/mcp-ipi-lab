# S2 KOŞUSU — Response-kanalı savunma değerlendirmesi (d5 dahil)

d5 (Tool Result Parsing) savunması eklendi ve mock ile doğrulandı
(d5 tüm 7 payload'da enjeksiyonu düşürüyor, başlıkları koruyor; CheckTool provenance çalışıyor).
Matris artık: d0, d1, d2, d3, d4, **d5**, d3+d4, **d2+d5** × 7 payload.

## Önkoşul
Ollama çalışıyor olmalı ve model inmiş olmalı:
    ollama list
    ollama pull qwen2.5:7b     # yoksa

## 1) Hızlı doğrulama (birkaç dakika)
    cd "~/Documents/xox/iuc-yuksek-lisans/LLM Güvenlik/Claude/documents/xox/llm-security-experiments/mcp-ipi-lab"
    python3 run_experiment.py --backend ollama --model qwen2.5:7b --quick

## 2) S2 TAM KOŞU (asıl deney; qwen2.5:7b, n=30, kontrol satırı)
    python3 run_experiment.py --backend ollama --model qwen2.5:7b --trials 30 --control --yes

## 3) (Opsiyonel) ikinci model — ters ölçekleme (P03/P04) için
    python3 run_experiment.py --backend ollama --model qwen2.5:3b --trials 30 --control --yes
    # veya bir reasoning model (ör. qwen3) inmişse onunla

## Çıktı
results/summary.csv + results/trials.jsonl + results/run_meta.json
Bu dosyaları bırak (klasörde kalsın) — sonradan matris F/G/H + sunum güncellenecek.

## Beklenti (hipotez)
- d0/d1/d3: propagation yüksek (içeriğe/response'a dokunmaz)
- d2/d4: propagation düşük ama d4 v4_obfuscated'e kırılgan olabilir
- d5: propagation ~0 + exfil 0, utility korunur (ParseData body'yi tipler, CheckTool provenance keser)
- exfil ASR: statik payload'da hizalı modelde çoğunlukla 0 → S1 (adaptif) gerekli
