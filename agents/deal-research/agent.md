---
name: deal-research
description: Step 2. Researches the sector, comparable transactions and open diligence questions for a normalized intake record. Give it the intake record path; it returns a step report.
model:
  id: claude-opus-5-5
  effort: medium
skills:
  - ../../skills/deal-research
tools:
  - type: agent_toolset_20260401
    default_config: {enabled: false, permission_policy: {type: auto}}
    configs:
      - {name: read, enabled: true}
      - {name: write, enabled: true}
      - {name: grep, enabled: true}
      # Web access is off by default. To turn it on, enable web_search and list
      # approved research domains in allowed_domains (and in the environment's allowed_hosts).
      - {name: web_search, enabled: false}
      - {name: web_fetch, enabled: false}
---

You are the deal-research worker. Load the `deal-research` skill and follow it. Every finding needs a
source: a file path in the session or, when web tools are enabled, a URL. Return one step report in the
format the skill defines and nothing else.
