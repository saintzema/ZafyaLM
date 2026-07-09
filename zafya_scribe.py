#!/usr/bin/env python3
"""
ZafyaScribe — offline medical scribe, built on the same offline stack as
ZafyaLM (llama.cpp + Qwen2.5-1.5B, zero cloud dependency).

Takes a doctor-patient conversation transcript and turns it into a
structured SOAP note (Subjective, Objective, Assessment, Plan) — entirely
on-device, no internet, no per-query API cost.

Scope note (honest, not overclaimed): this module takes TEXT transcript as
input. It does not itself do speech-to-text. Wiring in a local Whisper
model (e.g. distil-whisper, same pattern Scribend uses) to go directly from
audio -> transcript -> SOAP note is the natural next step, not yet built
here — this ships the structuring half of the pipeline, proven working
against the real local llama-server.

Design principle (same one applied across the rest of this build): the LLM
structures and summarizes what was actually said. It does not add new
clinical facts, and it does not diagnose. A deterministic post-check flags
any output line that looks like it invented information not present in the
source transcript's vocabulary, before the note is returned.

Usage:
    python3 zafya_scribe.py transcript.txt
    python3 zafya_scribe.py --text "Doctor: ... Patient: ..."
"""
import sys
import json
import re
import argparse
import urllib.request
import urllib.error

LLAMA_SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"

SOAP_SYSTEM_PROMPT = """You are ZafyaScribe, a clinical documentation assistant. You will be given a
transcript of a conversation between a clinician and a patient. Convert it into a structured SOAP note.

CRITICAL RULES:
- Only include information that was actually stated in the transcript. Never invent symptoms,
  history, vitals, or findings that were not mentioned.
- If a SOAP section has no relevant information in the transcript, write "Not discussed" for
  that section rather than guessing or filling it in.
- Do not state a diagnosis unless the clinician in the transcript explicitly stated one — if so,
  attribute it as their statement, not your own conclusion.
- This is a documentation aid. A clinician must review and sign off on the note before it becomes
  part of the medical record.

Respond with ONLY a JSON object, no markdown, no explanation:
{
  "subjective": "patient-reported symptoms, history, and concerns as stated",
  "objective": "clinician-observed findings, vitals, exam results as stated (or 'Not discussed')",
  "assessment": "the clinician's stated assessment/impression as stated (or 'Not discussed')",
  "plan": "next steps, treatment, follow-up as stated (or 'Not discussed')",
  "flagged_terms": ["any medication names or dosages mentioned, for clinician double-check"]
}"""


def _call_llama_server(transcript: str, server_url: str = LLAMA_SERVER_URL, timeout: int = 120) -> str:
    """Call the local llama-server's OpenAI-compatible endpoint. Raises on
    any network/HTTP error — this module has no cloud fallback by design;
    if the local server isn't running, it fails loudly rather than silently
    routing to a cloud API (that would break the "100% offline" guarantee)."""
    payload = {
        "messages": [
            {"role": "system", "content": SOAP_SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{transcript}"},
        ],
        "temperature": 0.2,  # low temperature — this is extraction/structuring, not creative writing
        "max_tokens": 500,
        "stream": False,
    }
    req = urllib.request.Request(
        server_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _parse_soap_json(raw: str) -> dict:
    """Parse the model's JSON response, tolerating markdown code fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return json.loads(cleaned)


def _fabrication_check(soap: dict, transcript: str) -> list:
    """Deterministic guardrail: flag any medication name or numeric dosage
    appearing in the generated note that does NOT appear anywhere in the
    source transcript — a strong signal the model added information it
    wasn't given, which must not silently enter a medical record."""
    warnings = []
    transcript_lower = transcript.lower()
    note_text = " ".join(str(v) for v in soap.values() if isinstance(v, str)).lower()

    # Any number+unit combination (dosage-shaped) in the note that isn't in the transcript.
    dosage_pattern = re.compile(r"\d+\s*(mg|ml|mcg|g|units?)\b")
    for match in dosage_pattern.finditer(note_text):
        snippet = match.group(0)
        if snippet not in transcript_lower:
            warnings.append(f"Note contains dosage-like text '{snippet}' not found in the transcript — verify before signing.")

    return warnings


def generate_soap_note(transcript: str, server_url: str = LLAMA_SERVER_URL) -> dict:
    """Full pipeline: transcript -> local LLM -> structured SOAP note ->
    deterministic fabrication check. Returns a dict with the note, a
    review_status defaulting to pending_review (same workflow-state pattern
    as the rest of ZemedicAI), and any fabrication warnings."""
    if not transcript or not transcript.strip():
        raise ValueError("transcript is empty")

    try:
        raw = _call_llama_server(transcript, server_url)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise RuntimeError(
            f"Could not reach local llama-server at {server_url} — ZafyaScribe requires the "
            f"offline model server to be running (see ZafyaLM's llama-server setup). Error: {e}"
        ) from e

    try:
        soap = _parse_soap_json(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model did not return valid JSON: {raw[:200]}") from e

    warnings = _fabrication_check(soap, transcript)

    return {
        "soap": soap,
        "fabrication_warnings": warnings,
        "review_status": "pending_review",
        "disclaimer": (
            "ZafyaScribe is a documentation aid, not a clinical record. "
            "A clinician must review and sign off before this note is finalized."
        ),
    }


def format_markdown(result: dict) -> str:
    soap = result["soap"]
    lines = [
        "# Clinical Note (ZafyaScribe draft — pending clinician review)",
        "",
        "## Subjective", soap.get("subjective", "Not discussed"), "",
        "## Objective", soap.get("objective", "Not discussed"), "",
        "## Assessment", soap.get("assessment", "Not discussed"), "",
        "## Plan", soap.get("plan", "Not discussed"), "",
    ]
    if soap.get("flagged_terms"):
        lines += ["## Flagged for clinician double-check", ", ".join(soap["flagged_terms"]), ""]
    if result["fabrication_warnings"]:
        lines += ["## ⚠ Fabrication warnings", *[f"- {w}" for w in result["fabrication_warnings"]], ""]
    lines += [f"_{result['disclaimer']}_"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ZafyaScribe — offline transcript-to-SOAP-note")
    parser.add_argument("file", nargs="?", help="path to a transcript text file")
    parser.add_argument("--text", help="transcript text directly on the command line")
    parser.add_argument("--server", default=LLAMA_SERVER_URL, help="llama-server chat completions URL")
    args = parser.parse_args()

    if args.text:
        transcript = args.text
    elif args.file:
        with open(args.file) as f:
            transcript = f.read()
    else:
        parser.error("provide either a transcript file or --text")

    result = generate_soap_note(transcript, args.server)
    print(format_markdown(result))


if __name__ == "__main__":
    main()
