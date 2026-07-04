# TRID3NT (GRACE-2) Developer Documentation Site

This directory contains the MkDocs Material documentation site for TRID3NT / GRACE-2.

## Preview locally

```bash
pip install mkdocs-material
cd docs-site
mkdocs serve
```

Then open http://127.0.0.1:8000 in your browser.

## Build (static HTML)

```bash
cd docs-site
mkdocs build
# Output in docs-site/site/
```

## Publish

### GitHub Pages

```bash
cd docs-site
mkdocs gh-deploy
```

This builds and pushes to the `gh-pages` branch of the current repo.

### ReadTheDocs

Point ReadTheDocs at this repository with:
- Documentation type: MkDocs
- Configuration file: `docs-site/mkdocs.yml`

### Cloudflare Pages

Set the build configuration:
- Build command: `pip install mkdocs-material && mkdocs build`
- Build output directory: `docs-site/site`
- Root directory: `docs-site`

## Structure

```
docs-site/
  mkdocs.yml          Site configuration
  README.md           This file
  docs/
    index.md          Home page
    architecture/
      overview.md     Tier diagram + scale-to-zero islands + lifecycles
      edge-and-web.md CloudFront, Vercel, S3, TiTiler, cold API
      session-tier.md Broker, route table, cold provision, reaper, teardown
      agent.md        Agent process, tools, Bedrock, durable state
      compute-tier.md AWS Batch topology, compute classes, workers
    reference/
      ws-protocol.md  WebSocket envelope protocol, keepalive, reconnect
      worker-contract.md publish_manifest.json + completion.json schemas
      engines.md      SFINCS, MODFLOW, PySWMM, GeoClaw, OpenQuake, Landlab, SWAN
      data-stores.md  DynamoDB tables, S3 buckets, key layouts
    operations/
      deploy.md       How each tier deploys
      runbook.md      Health check, failure modes, cost model, resource IDs
      verification.md Live-verify norm, smoke test, box-off check
    contributing.md   Repo layout, job system, add a tool, add an engine, invariants
```

## Notes

- Do NOT edit `docs/` (the frozen SRS). All docs-site content is under `docs-site/docs/`.
- The SRS at `docs/srs/` documents the original GCP/Gemini-era requirements. This site documents
  the system as built on AWS. Where they differ, this site is authoritative.
