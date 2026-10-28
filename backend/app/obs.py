"""Prometheus metrics. Exposed at GET /metrics (outside /api, so the public nginx does not proxy it;
optionally protected with METRICS_TOKEN). Request metrics use the route *template* as the label
(e.g. /api/v1/runs/{rid}), never the raw path, so label cardinality stays bounded."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "robotops_http_requests_total",
    "HTTP requests by route template, method and status code",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "robotops_http_request_duration_seconds",
    "HTTP request latency by route template",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=REGISTRY,
)
EVENTS_INGESTED = Counter(
    "robotops_device_events_total",
    "Device events by ingestion result (accepted, duplicate, rejected_offline)",
    ["result"],
    registry=REGISTRY,
)
RUNS_RECORDED = Counter(
    "robotops_runs_total",
    "Validation runs recorded by this process, by outcome",
    ["status"],
    registry=REGISTRY,
)
# The gauges below are computed from the database at scrape time, so they are correct no matter
# which process (API or a separate worker container) did the work.
JOBS = Gauge(
    "robotops_simulation_jobs",
    "Simulation jobs currently in each state",
    ["status"],
    registry=REGISTRY,
)
OLDEST_QUEUED = Gauge(
    "robotops_queue_oldest_job_age_seconds",
    "Age of the oldest queued simulation job (0 if the queue is empty). Growth means the worker is stalled.",
    registry=REGISTRY,
)
DEVICES = Gauge(
    "robotops_devices",
    "Registered devices by connectivity",
    ["online"],
    registry=REGISTRY,
)
