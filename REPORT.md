# Technical Report — ZafyaLM: Offline Medical Q&A and Patient Education for the 8GB Laptop

**Team ID:** TODO-confirm-after-devpost-create-project
**Domain:** healthcare_medical
**Model:** Qwen2.5-1.5B-Instruct-Q4_K_M

---

## Problem

Healthcare access across Africa is constrained less by clinical knowledge than by the infrastructure needed to deliver it: unreliable connectivity, intermittent power, and the cost of sustained cloud API usage. A nurse in a rural primary health center, a community health worker doing door-to-door outreach, or a patient trying to understand their own diagnosis often has no realistic path to a cloud-hosted medical AI assistant — not because the technology doesn't exist, but because the network and the electricity bill do not cooperate.

ZafyaLM is a fully offline, on-device medical Q&A and patient-education assistant that runs entirely on the commodity laptops already sitting in African clinics, classrooms, and homes — an 8GB machine with no discrete GPU. It targets two audiences with two registers:

- **Clinicians** get concise, differential-oriented, decision-support language — the kind of quick second opinion useful during a busy clinic day with limited specialist access.
- **Patients** get plain-language explanations, written for someone without medical training, with practical next steps and adherence guidance (e.g. why to finish a full course of medication).

Zero cloud dependency means it works identically whether the clinic has fiber, a patchy 2G signal, or no connection at all — and it costs nothing per query, which matters when the alternative is a metered API bill in a facility running on a tight budget.

---

## Design Decisions

**Base model: Qwen2.5-1.5B-Instruct**, chosen after head-to-head benchmarking against **Gemma-2-2B-it** — both at Q4_K_M quantization, both evaluated on the same hardware and prompts.

- **Quantization: Q4_K_M** — the standard "sweet spot" for CPU-only inference: meaningfully smaller and faster than Q5/Q8 variants, without the quality collapse seen at Q2/Q3 on models this size.
- **Why Qwen2.5-1.5B over Gemma-2-2B:**
  - **Completeness of output**: given the same 100-token generation budget, Qwen consistently produced a *complete* answer to our diabetes-explanation test prompt, while Gemma was cut off mid-sentence in both of two independent test runs (on two different machines). An incomplete medical explanation is a real quality defect, not just a stylistic difference — for a domain where a truncated answer could omit safety-relevant information, this alone is disqualifying for Gemma.
  - **Throughput**: Qwen decoded at 33.61 t/s vs Gemma's 17.53 t/s on representative x86 hardware (nearly 2x faster) — well above the ADTC's provisional `TPS_REFERENCE = 15.0`.
  - **Memory efficiency**: Qwen's peak RSS was 2.65GB vs Gemma's 3.30GB against the 7GB budget, giving a materially better `Seff` score.
- **Alternatives considered and rejected:**
  - **Gemma-2-2B-it (Q4_K_M)** — rejected per above: slower, heavier, and produced incomplete responses under the same token budget.
  - **Aya-23-8B / Aya-101 (Cohere)** — considered for genuine African-language coverage (would have supported the African Use Case Bonus), but rejected: even at Q4_K_M, an 8B+ model's weight footprint alone approaches or exceeds a large share of the 7GB peak-RAM budget once KV-cache and context overhead are added, creating real OOM-disqualification risk. We prioritized reliability within the hard RAM ceiling over a language-coverage bonus we couldn't validate safely within budget.

---

## Constraints

- **Target hardware**: ADTC Standard Laptop — Intel Core i5 10th-12th gen / AMD Ryzen 5, 8GB DDR4, integrated GPU only, Ubuntu 22.04 LTS reference.
- **No GPU acceleration** — pure CPU inference via `llama.cpp`, no CUDA/ROCm/Metal backend.
- **Zero network dependency at inference time** — `download_model.sh` fetches weights once, from a public Hugging Face URL, before the offline evaluation window begins; no calls are made during inference.
- **Hard 7GB peak-RAM ceiling** — an OOM during evaluation is an automatic disqualification per the competition rules, which directly shaped the decision to prefer the smaller, more RAM-efficient model over a larger one with marginally different qualitative style.
- **Connectivity reality for the target user**: the download step is the *only* point requiring internet access; everything after that — every clinician query, every patient explanation — runs fully offline.

---

## Benchmarks

Initial prototyping was done locally, but the development machine (a 2-physical-core, older Intel Mac) proved unrepresentative: `tg128` throughput actually *decreased* when moving from 2 to 4 threads (0.82 t/s → 0.61 t/s), a clear sign of backend/hardware-specific overhead rather than a genuine performance ceiling. All final benchmark numbers below were instead collected on a GitHub Actions `ubuntu-latest` runner (4 vCPU, x86-64) — a much closer proxy to the ADTC reference hardware.

| Metric | Qwen2.5-1.5B-Instruct (final) | Gemma-2-2B-it (rejected candidate) |
|---|---|---|
| Machine | GitHub Actions `ubuntu-latest`, 4 vCPU x86-64 | GitHub Actions `ubuntu-latest`, 4 vCPU x86-64 |
| Peak RAM | **2.65 GB** | 3.30 GB |
| Prompt processing (pp512) | 65.77 t/s | 42.25 t/s |
| Generation speed (tg128) | **33.61 t/s** | 17.53 t/s |
| 100-token test completion | Complete, coherent | Truncated mid-sentence |
| Thermal throttling | None observed | None observed |

These are self-reported development benchmarks, reproducible via the `.github/workflows/benchmark.yml` CI workflow in this repository. Official scores are measured by the ADTC profiler on the standard evaluation machine.
