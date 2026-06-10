# User unblock — one command (OQ-0245-QGIS-PROJECT-CACHE)

The round-3 live gate proved publish works end-to-end (layer lands inside the served `.qgs`), but the **live QGIS Server never re-parses the project file**, so fresh layers return `LayerNotDefined` until a cold start. The fix is two env vars on the Cloud Run service. The infra files (`infra/qgis-server/Dockerfile` + `infra/qgis-server.tf`) already carry the change for future deploys; the **live service** needs your one-time approval (the orchestrator's auto-mode correctly refused to mutate production):

```bash
gcloud run services update grace-2-qgis-server --region us-central1 \
  --update-env-vars QGIS_SERVER_PROJECT_CACHE_STRATEGY=periodic,QGIS_SERVER_PROJECT_CACHE_CHECK_INTERVAL=10000
```

After it deploys (~1 min), the round-3 plume layer (`plume-concentration-01KTRNPCV4...` already in the `.qgs`) should serve immediately — verify Gemini-free:

```bash
curl -s "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities" | grep -c plume-concentration
```

(Alternatively: the service scales to zero on idle — the next natural cold start picks the layer up without any action, but the env fix is what makes publishes appear within ~10s permanently.)
