---
profile: deployment-config
version: 1
card: true
triggers: deploy, config, env, secret, network, offline, service
confidence: observed-pattern
---

# Deployment and configuration

- Discover and cite the actual relevant configuration; never assume defaults
  the repository may contradict.
- A claimed offline behavior needs a controlled network-disabled execution
  or an explicit unverified gap; logs alone do not establish absence.
- Secrets stay in local project data (`.docket/`, baselines, bundles) and
  never in reports, prompts, or frozen packets.
