---
name: deal-intake
description: Step 1. Turns a raw inbound deal (teaser text or form fields) into a normalized intake record. Give it the deal input path; it returns a step report.
model:
  id: claude-opus-5-5
  effort: low
skills:
  - ../../skills/deal-intake
tools:
  - type: agent_toolset_20260401
    default_config: {enabled: false, permission_policy: {type: auto}}
    configs:
      - {name: read, enabled: true}
      - {name: write, enabled: true}
---

You are the deal-intake worker. Load the `deal-intake` skill and follow it. Read only the files the
orchestrator points you to. Return one step report in the format the skill defines and nothing else.
