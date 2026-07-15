---
name: dream-reflection
description: Reflect on recent work, keep private adaptation local, and record only generalized transferable candidates.
---

# Dream reflection

Use this skill only for an explicit dream run.

1. Review recent sessions, local memory, outcomes, corrections, repeated tool patterns, and failed approaches.
2. Classify each observation as private personal/team context, private cache, candidate transferable procedure, candidate transferable domain knowledge, or do not store.
3. Update private memory or instance-local skills only for the first two classes. Never copy those details into a transferable record.
4. For each useful transferable candidate, generalize it so it contains no people, customers, tenants, message text, document excerpts, credentials, identifiers, internal URLs, or user-specific paths.
5. Append the candidate only through:

   `python3 /app/learning.py append --record '<one JSON object>'`

The candidate object must contain:

- `classification`: `transferable_procedural` or `transferable_domain`
- `title`
- `generalizedLearning`
- `rationale`
- `evidence`: 1-5 objects with only `sourceType` and a generalized `summary`
- `confidence`: number from 0 to 1
- `proposedTarget`: `{"kind":"skill","path":"skills/<name>"}` or `{"kind":"knowledge","path":"knowledge/<name>.md"}`

The validator assigns record identity, timestamp, and privacy status. If it rejects a record, keep the material private; do not weaken or bypass the validator.

Finish with a concise count of private updates, accepted transferable records, rejected candidates, and observations intentionally not stored.
