"""Generate the hard retrieval set (synthetic, deterministic).

Run from the project root:  python eval/generate_hard_dataset.py
Writes eval/datasets/support-corpus-hard.jsonl and support-eval-hard.jsonl.
Families: near-neighbour error codes, near-duplicate service runbooks,
paraphrased questions with little lexical overlap, identifier lookups,
and same-topic questions whose answer is absent.
"""
import json
import random
from pathlib import Path

DATA = Path(__file__).resolve().parent / "datasets"
rng = random.Random(20261120)
docs: list[dict] = []
cases: list[dict] = []


def add_doc(doc_id: str, doc_type: str, chunks: list[str]) -> None:
    docs.append({
        "id": doc_id, "product_name": "Nebula Gateway", "product_version": "3.2",
        "document_type": doc_type, "visibility": "workspace", "department": "payments",
        "workspace": "nebula-support", "deleted": False,
        "chunks": [{"id": f"chunk-{i}", "text": t} for i, t in enumerate(chunks, 1)],
    })


def add_case(case_id: str, category: str, question: str, doc_id: str | None,
             chunk_id: str | None, quote: str | None) -> None:
    ok = doc_id is not None
    cases.append({
        "id": case_id, "category": category, "question": question, "actor": "engineer",
        "version": "3.2", "single_doc_id": None, "answerable": ok,
        "evidence": [{"doc_id": doc_id, "chunk_id": chunk_id, "quote": quote}] if ok else [],
        "forbidden_doc_ids": [], "expect_access_denial": False,
    })


# A. near-neighbour error codes -------------------------------------------------
codes = ["5120", "51200", "5102", "5210", "5121", "5112", "5012", "51020", "5201", "5220",
         "5122", "5110", "5021", "52100", "5111", "5101", "5212", "5202", "51210", "5120A",
         "5130", "5103", "5230", "5113"]
conds = [
    "the upstream connection pool exceeded its maximum size", "a TLS certificate is within its renewal window",
    "the request router found two rules with the same priority", "the rate limiter lost contact with its counter store",
    "a plugin returned an invalid response header", "the health checker marked every node in a pool unhealthy",
    "the configured keepalive interval is below the minimum", "the access policy references a deleted role",
    "a canary stage exceeded its error budget", "the DNS resolver returned an empty answer",
    "the audit sink rejected a batch of events", "a websocket upgrade was denied by policy",
    "the compression module ran out of buffer space", "the session store reported a version mismatch",
    "a retry budget was exhausted during a deployment", "the schema validator rejected a staged configuration",
    "the metrics exporter could not bind its port", "a backend returned more redirects than allowed",
    "the request body exceeded the configured size limit", "the leader election timed out",
    "an API key was presented from a blocked network", "the log shipper fell behind by more than a minute",
    "a route target resolved to a private address", "the failover controller found no standby region",
]
actions = [
    "raise pool.max_size or reduce concurrent callers", "run the certificate renewal job",
    "give each routing rule a unique priority", "restart the counter store client and replay the window",
    "disable the plugin and inspect its output", "check network reachability from the probe nodes",
    "restore the interval to at least 5 seconds", "reassign the policy to an existing role",
    "roll the canary back to stage zero", "verify the resolver address list",
    "reduce the audit batch size", "review the websocket allow list",
    "increase the compression buffer", "flush the session store and reconnect",
    "extend the retry budget for the rollout", "fix the schema errors reported in the diff",
    "free the exporter port or change it", "shorten the redirect chain at the backend",
    "raise body.max_bytes for the route", "restart the quorum members one at a time",
    "remove the address from the block list", "scale out the log shipper workers",
    "point the route at a public target", "register a standby region before enabling failover",
]
for n, cond, act in zip(codes, conds, actions):
    code = f"CFG-{n}"
    add_doc(f"code-{n.lower()}", "manual", [
        f"{code} means {cond}. The gateway logs it as a warning for the control plane.",
        f"To recover from {code}, {act}. Reload the gateway configuration afterwards.",
    ])
for n, cond in zip(codes, conds):
    add_case(f"hard-code-meaning-{n.lower()}", "hard-error-code", f"What does CFG-{n} mean?",
             f"code-{n.lower()}", "chunk-1", cond)
for n, act in list(zip(codes, actions))[:12]:
    add_case(f"hard-code-recover-{n.lower()}", "hard-error-code", f"How do I recover from CFG-{n}?",
             f"code-{n.lower()}", "chunk-2", act)
for n, cond in list(zip(codes, conds))[12:20]:
    add_case(f"hard-code-nohyphen-{n.lower()}", "hard-error-code", f"What is the meaning of CFG{n}?",
             f"code-{n.lower()}", "chunk-1", cond)

# B. near-duplicate service runbooks -------------------------------------------
services = ["auth", "billing", "ledger", "search", "notify", "report", "export", "import", "audit", "cache"]
for i, s in enumerate(services):
    idle, retries, backoff = 20 + 7 * i, 2 + (i % 5), 100 + 50 * i
    add_doc(f"svc-{s}-runbook", "sop", [
        f"The {s} service uses an upstream idle timeout of {idle} seconds. Keep it below the load balancer limit.",
        f"The {s} service retries failed requests {retries} times with a backoff of {backoff} milliseconds.",
    ])
    add_case(f"hard-svc-idle-{s}", "hard-near-duplicate", f"What is the idle timeout of the {s} service?",
             f"svc-{s}-runbook", "chunk-1", f"idle timeout of {idle} seconds")
    add_case(f"hard-svc-retry-{s}", "hard-near-duplicate", f"How many times does the {s} service retry a failed request?",
             f"svc-{s}-runbook", "chunk-2", f"retries failed requests {retries} times")

# C. paraphrased questions -------------------------------------------------------
para = [
    ("The gateway closes a backend connection after it has been unused for 45 seconds.", "unused for 45 seconds",
     "How long can an upstream link sit inactive before it is dropped?"),
    ("Certificates are renewed automatically 14 days before they expire.", "14 days before they expire",
     "When does the system refresh TLS credentials ahead of their end date?"),
    ("A request body larger than 8 MB is rejected with status 413.", "larger than 8 MB is rejected",
     "What is the biggest payload accepted from clients?"),
    ("Access logs are kept for 30 days and then deleted.", "kept for 30 days",
     "How long are traffic records retained?"),
    ("Operators can pause a rollout by running the freeze command.", "freeze command",
     "How do I temporarily halt a deployment in progress?"),
    ("Each API key allows 600 requests per minute before throttling starts.", "600 requests per minute",
     "What throughput cap applies to a credential before it is slowed down?"),
    ("Health probes hit the /ready path every 10 seconds.", "every 10 seconds",
     "How often does the liveness check poll the service endpoint?"),
    ("Session tokens expire after 12 hours of inactivity.", "expire after 12 hours",
     "After what period of idleness is a login invalidated?"),
    ("Configuration changes are validated against a schema before they are applied.", "validated against a schema",
     "What check runs before new settings take effect?"),
    ("The primary region fails over to the standby region within 90 seconds.", "within 90 seconds",
     "How quickly does traffic move to the backup datacenter during an outage?"),
    ("Metrics are exported in Prometheus format on port 9102.", "port 9102",
     "Which network port serves monitoring data?"),
    ("Dead letter messages are stored for 7 days before purging.", "stored for 7 days",
     "How long are undeliverable events kept?"),
    ("Compression is enabled for responses larger than 1 KB.", "larger than 1 KB",
     "From what response size does the gateway shrink outgoing data?"),
    ("The audit trail records the username and the source address of every admin change.", "username and the source address",
     "Which details are captured about who modified administrative settings?"),
    ("Rate limit counters reset at the start of every minute.", "start of every minute",
     "When does the request quota window start over?"),
    ("A canary receives 5 percent of traffic during the first stage.", "5 percent of traffic",
     "What share of users is routed to the trial instance initially?"),
    ("Backups run nightly at 02:00 UTC.", "nightly at 02:00 UTC",
     "At what hour is the daily snapshot taken?"),
    ("The maximum number of simultaneous connections per node is 20000.", "per node is 20000",
     "What is the concurrency ceiling for one server instance?"),
    ("Unhealthy nodes are removed from rotation after 3 consecutive failed checks.", "after 3 consecutive failed checks",
     "How many bad probes lead to a node being taken out of service?"),
    ("DNS answers are cached for 60 seconds.", "cached for 60 seconds",
     "For how long are name resolution results remembered?"),
    ("Support engineers must open a change ticket before editing production routes.", "open a change ticket",
     "What paperwork is required prior to modifying live routing?"),
    ("Debug logging expires automatically after 2 hours.", "expires automatically after 2 hours",
     "How long does verbose diagnostic output stay on?"),
    ("WebSocket connections are limited to 1 hour and clients must reconnect.", "limited to 1 hour",
     "What is the longest lifetime of a persistent streaming link?"),
    ("Static assets are served with a cache lifetime of 1 day.", "cache lifetime of 1 day",
     "How long may browsers reuse downloaded images and scripts?"),
]
for i, (text, quote, q) in enumerate(para, 1):
    add_doc(f"para-{i:02d}", "faq", [text, "This behaviour applies to every 3.2 gateway in the support workspace."])
    add_case(f"hard-para-{i:02d}", "hard-paraphrase", q, f"para-{i:02d}", "chunk-1", quote)

# D. identifier lookups ----------------------------------------------------------
symptoms = ["intermittent 502 responses", "slow login pages", "failed nightly exports", "duplicate notifications",
            "stale search results", "missing audit entries", "certificate warnings", "dropped websocket sessions",
            "high memory usage", "failed configuration reloads", "slow report generation", "login redirect loops",
            "empty metrics dashboards", "delayed invoices", "rejected uploads", "timeouts on import jobs",
            "inconsistent rate limits", "unavailable status page", "slow DNS lookups", "failed health probes",
            "missing request logs", "stuck deployments", "unexpected 404 responses", "queue backlog growth"]
causes = ["an expired intermediate certificate", "a misconfigured keepalive interval", "a full disk on the log node",
          "two rules sharing one priority", "a stale DNS cache entry", "a counter store failover",
          "an oversized request header", "a plugin that blocked the event loop", "a leaked connection pool",
          "an unreleased leader lock", "a clock skew between nodes", "a wrong redirect target",
          "a missing index on the ticket table", "an exhausted retry budget", "a blocked exporter port",
          "an outdated schema version", "a session store mismatch", "a paused canary stage",
          "a broken health probe path", "a saturated log shipper", "a mistyped route target",
          "a disabled compression module", "a throttled API key", "a lost standby registration"]
fixes = ["renew the certificate chain", "set the interval to 10 seconds", "expand the log volume",
         "assign unique priorities", "flush the DNS cache", "restart the counter client",
         "raise the header limit", "disable the plugin", "restart the pool owner", "release the lock manually",
         "resync the node clocks", "correct the redirect URL", "add the missing index", "extend the budget",
         "free the exporter port", "migrate the schema", "flush the session store", "resume the canary",
         "fix the probe path", "scale the shippers", "correct the route", "enable compression",
         "raise the key limit", "register the standby"]
svcs = ["auth", "billing", "ledger", "search", "notify", "report", "export", "import", "audit", "cache"]
order = list(range(24))
rng.shuffle(order)
for k, idx in enumerate(order):
    n = 7001 + k
    svc = svcs[k % len(svcs)]
    add_doc(f"inc-{n}", "ticket", [
        f"Incident INC-{n} affected the {svc} service. The symptom was {symptoms[idx]}.",
        f"The root cause of INC-{n} was {causes[idx]}. The fix was to {fixes[idx]}.",
    ])
    add_case(f"hard-inc-cause-{n}", "hard-identifier", f"What was the root cause of INC-{n}?",
             f"inc-{n}", "chunk-2", causes[idx])

# E. unanswerable, same topic -----------------------------------------------------
for q in ["What does CFG-5999 mean?", "How do I recover from CFG-5777?",
          "What is the idle timeout of the telemetry service?", "How many times does the gateway service retry a failed request?",
          "What was the root cause of INC-7999?", "What was the root cause of INC-6500?",
          "How long are password reset links valid?", "Which port serves the admin console?",
          "What is the maximum number of routes per gateway?", "How often are access keys rotated?"]:
    add_case(f"hard-none-{len(cases)}", "hard-unanswerable", q, None, None, None)

(DATA / "support-corpus-hard.jsonl").write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n", encoding="utf8")
(DATA / "support-eval-hard.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf8")
print(len(docs), "documents,", sum(len(d["chunks"]) for d in docs), "chunks,", len(cases), "cases,",
      sum(c["answerable"] for c in cases), "answerable")
