---
name: deal-research
description: Builds the research pack for a normalized deal intake record - sector context, comparable transactions, key risks and open diligence questions, each with a source. Use for step 2 of the deal-desk pipeline.
---

# Deal research

Read the intake record from your brief and write a research pack as JSON to the path in your brief.

## Research pack

```json
{
  "sector_view": "3-5 sentences on demand drivers, cyclicality and margin structure",
  "comparables": [
    {"name": "...", "year": 2025, "ev_ebitda_x": 11.2, "source": "path or URL"}
  ],
  "risks": [
    {"risk": "...", "severity": "low | medium | high", "mitigant": "...", "source": "..."}
  ],
  "diligence_questions": ["..."],
  "sources": ["every path or URL used above"]
}
```

## Rules

- Every comparable and every risk carries a `source`. A finding without a source is dropped, not guessed.
- Use reference files under `/workspace/deal-desk/examples/reference/` when they exist. Use the web only
  if your web tools are enabled; if they are not, say so in `summary` rather than inventing comparables.
- Mark a comparable older than three years with `"stale": true` and do not count it toward the two
  required for `ok`.
- Write at least five diligence questions that the intake record leaves open, starting with the fields
  listed in its `missing_fields`.
- Status: `ok` with at least two sourced comparables and three sourced risks; otherwise `needs_review`.

## Step report

Reply with one JSON object that matches `schemas/step-result.schema.json`: `agent` = `deal-research`,
`output.artifact` = the path you wrote, `summary` = one sentence.
