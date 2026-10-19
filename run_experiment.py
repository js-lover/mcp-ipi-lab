#!/usr/bin/env python3
"""IPI deney harness'i: ASR + utility matrisi.

Kullanım örnekleri:
  # Harness'i Ollama'sız doğrula (mock, anında):
  python run_experiment.py --backend mock --trials 20

  # Tek koşuluk mekanizma demosu (P09 Şekil 11'in birebir izi):
  python run_experiment.py --backend mock --demo

  # HIZLI ilk gerçek koşu (önerilen başlangıç — küçük matris):
  python run_experiment.py --backend ollama --model qwen2.5:7b --quick

  # Tam gerçek deney (uzun sürebilir — baştan tahmini süre basılır):
  python run_experiment.py --backend ollama --model qwen2.5:7b --trials 30 --control

Çıktı: results/summary.csv  +  results/trials.jsonl  + konsol tablosu.
"""
import argparse
import csv
import datetime
import json
import math
import os
import sys
import time

from lab import config
from lab.agent import run_trial
from lab.model_client import make_client, ModelError

# Varsayılan savunma konfigürasyonları (her biri '+' ile kombinlenebilir)
DEFAULT_CONFIGS = [
    [],                                  # d0_none (baseline)
    ["d1_spotlight"],
    ["d2_declarative"],
    ["d3_intent"],
    ["d4_anomaly"],
    ["d3_intent", "d4_anomaly"],         # katmanlı
]

# --quick için küçük matris (ilk gerçek koşu, ~dakikalar)
QUICK_CONFIGS = [[], ["d2_declarative"], ["d3_intent"], ["d4_anomaly"]]
QUICK_PAYLOADS = ["v6_prerequisite", "v4_obfuscated"]
QUICK_TRIALS = 8


def parse_configs(spec):
    if not spec or spec == "default":
        return DEFAULT_CONFIGS
    if spec == "none":
        return [[]]
    configs = []
    for part in spec.split(","):
        part = part.strip()
        if part in ("", "d0_none", "none"):
            configs.append([])
        else:
            configs.append(part.split("+"))
    return configs


def label(cfg):
    return "+".join(cfg) if cfg else "d0_none"


def wilson_halfwidth(p, n, z=1.96):
    if n == 0:
        return 0.0
    denom = 1 + z * z / n
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return margin / denom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="mock", choices=["mock", "ollama", "openai"])
    ap.add_argument("--model", default=None, help="ör. qwen2.5:7b / llama3.1:8b")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--payloads", default="all",
                    help="virgülle: v1_naive,v4_obfuscated  | 'all'")
    ap.add_argument("--defenses", default="default",
                    help="virgülle konfig; her konfig '+' ile: d3_intent+d4_anomaly")
    ap.add_argument("--control", action="store_true",
                    help="enjeksiyonsuz kontrol satırı ekle (payload=none)")
    ap.add_argument("--quick", action="store_true",
                    help="küçük matris + az tekrar (hızlı ilk gerçek koşu)")
    ap.add_argument("--demo", action="store_true",
                    help="tek koşuluk, adım adım transkript (v1_naive, savunmasız)")
    ap.add_argument("--yes", action="store_true",
                    help="süre tahmininde onay sorma, direkt başla")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    kw = {"temperature": args.temperature}
    if args.model:
        kw["model"] = args.model
    try:
        client = make_client(args.backend, **kw)
    except TypeError:
        client = make_client(args.backend, model=args.model)
    model_name = args.model or getattr(client, "model", args.backend)
    print(f"Backend: {getattr(client, 'name', args.backend)}  |  "
          f"model: {model_name}  |  temp: {args.temperature}")

    if args.demo:
        _preflight(client, args.backend)
        run_trial(client, "v1_naive", [], verbose=True)
        print("\n(Demo bitti. Nicel ölçüm için --demo'yu kaldır.)")
        return

    # --- matris kur ---------------------------------------------------------
    if args.quick:
        configs = QUICK_CONFIGS
        payloads = QUICK_PAYLOADS
        trials = QUICK_TRIALS
        control = True
    else:
        configs = parse_configs(args.defenses)
        payloads = config.PAYLOAD_IDS if args.payloads == "all" \
            else [p.strip() for p in args.payloads.split(",")]
        trials = args.trials
        control = args.control

    tasks = [(pid, False) for pid in payloads]
    if control:
        tasks = [("none", True)] + tasks

    n_cells = len(configs) * len(tasks)
    n_trials = n_cells * trials
    print(f"Matris: {len(configs)} savunma × {len(tasks)} görev × {trials} tekrar "
          f"= {n_trials} koşu ({n_cells} hücre)")

    # --- preflight + süre tahmini ------------------------------------------
    per_call = _preflight(client, args.backend)
    if per_call:
        # ~2.3 çağrı/koşu (list_issues + çoğu koşuda read_private_file + final)
        est = n_trials * 2.3 * per_call
        print(f"Kaba süre tahmini: ~{_fmt(est)} "
              f"(çağrı başı ~{per_call:.1f}s). İlk çağrı modeli yükledi.")
        if est > 300 and not args.yes and sys.stdin.isatty():
            ans = input("Devam edilsin mi? [E/h] ").strip().lower()
            if ans in ("h", "n", "no", "hayır"):
                print("İptal. Daha küçük koşu için: --quick")
                return

    # --- çalıştır -----------------------------------------------------------
    os.makedirs(args.out, exist_ok=True)
    trials_path = os.path.join(args.out, "trials.jsonl")
    summary_path = os.path.join(args.out, "summary.csv")

    rows = []
    t_start = time.time()
    started_iso = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    cell = 0
    interrupted = False
    with open(trials_path, "w", encoding="utf-8") as tf:
        try:
            for cfg in configs:
                for pid, is_control in tasks:
                    cell += 1
                    lbl = label(cfg)
                    pname = "none(kontrol)" if is_control else pid
                    print(f"[{cell}/{n_cells}] {lbl} × {pname} ", end="", flush=True)
                    succ = att = unauth = blocked = taskok = 0
                    for _ in range(trials):
                        payload_arg = None if is_control else pid
                        r = run_trial(client, payload_arg, cfg)
                        r["model"] = model_name
                        r["backend"] = args.backend
                        r["temperature"] = args.temperature
                        tf.write(json.dumps(r, ensure_ascii=False) + "\n")
                        tf.flush()
                        succ += r["attack_success"]
                        att += r["attempted"]
                        unauth += r["unauthorized_tool"]
                        blocked += r["blocked_by_intent"]
                        taskok += r["task_success"]
                        print("." if not r["attack_success"] else "x",
                              end="", flush=True)
                    asr = succ / trials
                    print(f"  ASR={asr:.2f}", flush=True)
                    rows.append({
                        "model": model_name, "temperature": args.temperature,
                        "defenses": lbl, "payload": pname, "n": trials,
                        "asr": round(asr, 3),
                        "asr_ci95": round(wilson_halfwidth(asr, trials), 3),
                        "attempt_rate": round(att / trials, 3),
                        "unauth_tool_rate": round(unauth / trials, 3),
                        "blocked_rate": round(blocked / trials, 3),
                        "task_success_rate": round(taskok / trials, 3),
                    })
        except KeyboardInterrupt:
            interrupted = True
            print("\n[kesildi] Tamamlanan hücreler kaydediliyor...")
        except ModelError as e:
            print(f"\n[HATA] {e}")
            if not rows:
                sys.exit(1)

    meta = {
        "backend": args.backend, "model": model_name,
        "temperature": args.temperature, "trials_per_cell": trials,
        "cells": n_cells, "total_trials": n_trials,
        "defenses": [label(c) for c in configs],
        "payloads": ["none(kontrol)" if ic else pid for pid, ic in tasks],
        "started": started_iso,
        "finished": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_sec": round(time.time() - t_start, 1),
        "interrupted": interrupted,
    }
    with open(os.path.join(args.out, "run_meta.json"), "w", encoding="utf-8") as mf:
        json.dump(meta, mf, ensure_ascii=False, indent=2)

    if rows:
        with open(summary_path, "w", newline="", encoding="utf-8") as cf:
            w = csv.DictWriter(cf, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        _print_table(rows)
        print(f"\nDamga: model={model_name} · temp={args.temperature} · {meta['finished']}")
        print(f"Süre: {_fmt(time.time() - t_start)}  |  {summary_path}  |  {trials_path}  |  "
              f"{os.path.join(args.out, 'run_meta.json')}")
    if interrupted:
        print("(Kısmi sonuç. Tam koşu için yeniden çalıştır veya --quick dene.)")


def _preflight(client, backend):
    """mock dışı backend'de modeli yükle/ısıt; çağrı başı süreyi döndür."""
    if backend == "mock" or not hasattr(client, "ping"):
        return None
    print("Model ısıtılıyor (ilk çağrı modeli belleğe yükler, biraz sürebilir)...",
          flush=True)
    try:
        dt = client.ping()
        print(f"  tamam ({dt:.1f}s).", flush=True)
        return max(dt, 0.3)
    except ModelError as e:
        print(f"\n[HATA] {e}")
        sys.exit(1)


def _fmt(sec):
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec/60:.1f} dk"
    return f"{sec/3600:.1f} saat"


def _print_table(rows):
    cols = ["defenses", "payload", "n", "asr", "asr_ci95",
            "attempt_rate", "unauth_tool_rate", "blocked_rate", "task_success_rate"]
    widths = {c: max(len(c), max(len(str(r[c])) for r in rows)) for c in cols}
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    print("\n" + line)
    print("-" * len(line))
    last = None
    for r in rows:
        if last is not None and r["defenses"] != last:
            print("-" * len(line))
        last = r["defenses"]
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
    print("\nNot: asr = sahte sırrın cevaba sızma oranı (birincil ASR).")
    print("     attempt_rate = enjeksiyonu deneme; unauth_tool_rate = izinsiz tool çalıştı;")
    print("     blocked_rate = d3 engelledi; task_success_rate = meşru görev tamamlandı (utility).")


if __name__ == "__main__":
    sys.exit(main())
