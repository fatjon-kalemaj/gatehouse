---
name: deal-memo
description: Writes a two-page screening memo for an investment committee pre-read from a deal intake record and research pack. Use for step 3 of the deal-desk pipeline.
---

# Screening memo

Write Markdown to the path in your brief. Two pages at most.

## Sections, in this order

1. **Recommendation** - one of `Advance to diligence`, `Advance with conditions`, `Pass`, followed by
   two sentences of reasoning.
2. **The deal** - asset class, strategy, sector, geography, size, our check, sponsor, deadline.
3. **Why it could work** - three bullets, each tied to a number or a sourced finding.
4. **What could go wrong** - the high and medium risks from the research pack, each with its mitigant.
5. **Comparables** - a table: name, year, EV/EBITDA, source.
6. **Open questions** - the diligence questions, most important first.
7. **Sources** - every source cited above.

## Rules

- Use only facts from the intake record and research pack. If a number is missing, write "not provided".
- Cite inline as `[S1]`, `[S2]` and list them under Sources.
- No adjectives that the numbers do not support. Write for a reader who will check every figure.
- Status: `ok` when every section is present; `needs_review` when the recommendation depends on a
  missing field.

## Step report

Reply with one JSON object that matches `schemas/step-result.schema.json`: `agent` = `deal-memo`,
`output.artifact` = the memo path, `output.recommendation` = the recommendation, `summary` = one sentence.
