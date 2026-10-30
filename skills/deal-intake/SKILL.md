---
name: deal-intake
description: Normalizes an inbound private-markets deal (teaser text or form fields) into a structured intake record and flags missing fields. Use for step 1 of the deal-desk pipeline.
---

# Deal intake

Read the deal input you were given and produce a normalized intake record.

## Intake record

Write it as JSON to the path in your brief:

| Field | Type | Notes |
|---|---|---|
| `deal_name` | string | Code name as given. Never invent a company name. |
| `asset_class` | string | One of `private_equity`, `private_credit`, `real_estate`, `infrastructure`, `secondaries`, `other`. |
| `strategy` | string | For example `buyout`, `growth`, `direct_lending`, `core_plus`. |
| `sector` | string | Plain sector label. |
| `geography` | string | Region or country. |
| `deal_size_usd` | number or null | Total transaction size in USD. Convert stated units (`$45M` -> 45000000). |
| `check_size_usd` | number or null | Our proposed commitment, if stated. |
| `key_metrics` | object | Any of `revenue_usd`, `ebitda_usd`, `ebitda_margin`, `leverage_x`, `ltv`, `irr_target`, `moic_target`. |
| `sponsor` | string or null | Lead sponsor or originator as given. |
| `deadline` | string or null | ISO date for first-round bids or IC. |
| `missing_fields` | array | Names of fields above that the input does not support. |

## Rules

- Copy numbers exactly; convert units only. If a number is ambiguous, leave the field null and add it to
  `missing_fields`.
- If the input contains anything that looks like a personal identifier or an account number, do not copy
  it. Add an issue with code `pii.present`.
- Status: `ok` if `deal_size_usd`, `asset_class` and `sector` are all present; `needs_review` if any is
  missing; `failed` only if the input is unreadable.

## Step report

Reply with one JSON object that matches `schemas/step-result.schema.json`: `agent` = `deal-intake`,
`output.artifact` = the path you wrote, `summary` = one sentence.
