# Ingestion

Turns authoritative Rutgers sources into normalized, provenance-tagged rows in
the CoursePilot database.

**NOT IMPLEMENTED YET.** No scraper, parser, or API client exists. This
directory holds the intended structure and the rules any future ingestor must
follow.

## Why this is a separate package

Ingestion has a different runtime shape from the API: it is batch, slow,
network-bound, failure-prone, and occasionally needs a browser engine. Keeping
it out of the FastAPI process means a scraping change cannot destabilize the
request path, and the API image does not carry browser dependencies.

It writes to the same database, through the same models, using the same
provenance rules.

## Pipeline

```
Authoritative Rutgers source
        |
        v
   fetch        raw payload persisted verbatim, with retrieval timestamp
        |
        v
   parse        source-specific -> intermediate representation
        |
        v
  normalize     map to CoursePilot's canonical vocabulary
        |
        v
  validate      structural + referential checks; reject, never guess
        |
        v
    load        upsert with SourceRef attached to every record
```

## Non-negotiable rules

1. **Persist the raw payload before parsing.** When a parser turns out to be
   wrong six weeks later, reprocessing beats re-scraping — and re-scraping may
   be impossible, since the source has moved on.

2. **Never fabricate.** If a field cannot be parsed, record it as missing.
   A `null` prerequisite is honest; an inferred one is dangerous, because the
   validator will treat it as authoritative and clear a student for a course
   they cannot take.

3. **Everything is term-scoped.** A requirement true for 2026–2027 is not
   automatically true for 2027–2028. Every row carries its academic year.

4. **Ingestion is idempotent.** Re-running against unchanged source produces
   no new versions. This makes scheduled refresh safe.

5. **Changes are versioned, not overwritten.** When a source changes, write a
   new version and mark the old one superseded. Degree audits are historical
   claims; students need to know what the rules were when they planned.

6. **Be a polite client.** Rate limit, identify the client honestly via
   `INGESTION_USER_AGENT`, respect `robots.txt`, and cache aggressively.
   Rutgers infrastructure is a shared university resource.

## TODO(rutgers-source)

Nothing can be built here until these are established from official sources:

- [ ] Which official Rutgers course/section data feed is available, and its terms of use
- [ ] The official term code format
- [ ] The official course code structure (unit / subject / course numbering)
- [ ] Where degree requirements are authoritatively published, per school
- [ ] How prerequisites are expressed (structured field vs. prose)
- [ ] Whether an official API exists, and whether access requires authorization

**Do not guess any of these.** Each one guessed wrong propagates into the
validator and produces confidently incorrect academic advice.
