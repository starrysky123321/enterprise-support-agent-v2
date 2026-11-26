"""Generate the former held-out set (synthetic, deterministic).

Written after the first dev set (generate_hard_dataset.py), with:
different code prefix, service names, ticket prefix, paraphrase facts and unanswerable styles.
Its failures were inspected during refusal development on 2026-11-21, so it is now a
development regression set. The filename is retained for historical reproducibility.
Run from the project root:  python eval/generate_holdout_dataset.py
"""
import json
import random
from pathlib import Path

DATA = Path(__file__).resolve().parent / "datasets"
rng = random.Random(77)
docs: list[dict] = []
cases: list[dict] = []


def add_doc(doc_id, doc_type, chunks):
    docs.append({
        "id": doc_id, "product_name": "Nebula Gateway", "product_version": "3.2",
        "document_type": doc_type, "visibility": "workspace", "department": "payments",
        "workspace": "nebula-support", "deleted": False,
        "chunks": [{"id": f"chunk-{i}", "text": t} for i, t in enumerate(chunks, 1)],
    })


def add_case(case_id, category, question, doc_id=None, chunk_id=None, quote=None):
    ok = doc_id is not None
    cases.append({
        "id": case_id, "category": category, "question": question, "actor": "engineer",
        "version": "3.2", "single_doc_id": None, "answerable": ok,
        "evidence": [{"doc_id": doc_id, "chunk_id": chunk_id, "quote": quote}] if ok else [],
        "forbidden_doc_ids": [], "expect_access_denial": False,
    })


# A. error codes with a new prefix --------------------------------------------
codes = ["7310", "73100", "7301", "7130", "7311", "7312", "73101", "7031", "7320", "7131", "7302", "7313", "73110", "7321", "7132", "7303"]
conds = [
    "the egress queue is full", "a peer rejected the mutual TLS handshake", "the shard map is older than the router state",
    "the circuit breaker opened for a backend group", "two listeners requested the same bind address",
    "the signing key rotation did not finish", "the buffer pool was fragmented beyond its limit",
    "a traffic mirror target stopped responding", "the policy bundle failed its checksum",
    "the load balancer saw an unknown zone label", "the idle reaper closed an active stream",
    "the cluster clock drifted past the allowed skew", "a webhook receiver returned too many errors",
    "the session cache was evicted during a rollout", "the gRPC deadline was shorter than the backend timeout",
    "the license file expired",
]
acts = [
    "drain the egress queue and raise its capacity", "reissue the peer certificate and retry the handshake",
    "refresh the shard map from the control plane", "reset the breaker after the backend group is healthy",
    "assign a distinct bind address to each listener", "rerun the key rotation job to completion",
    "restart the worker to rebuild the buffer pool", "point the mirror at a reachable target",
    "download the policy bundle again", "add the zone label to the balancer inventory",
    "extend the idle timeout for streaming routes", "resync the cluster clocks with NTP",
    "disable the webhook until the receiver recovers", "warm the session cache before the next rollout",
    "lengthen the gRPC deadline", "install a renewed license file",
]
for n, c, a in zip(codes, conds, acts):
    add_doc(f"net-{n}", "manual", [
        f"NET-{n} means {c}. It is reported by the data plane at error level.",
        f"To recover from NET-{n}, {a}. Confirm the result in the status page.",
    ])
    add_case(f"holdout-net-meaning-{n}", "holdout-error-code", f"What does NET-{n} mean?", f"net-{n}", "chunk-1", c)
for n, a in list(zip(codes, acts))[:8]:
    add_case(f"holdout-net-recover-{n}", "holdout-error-code", f"How do I recover from NET-{n}?", f"net-{n}", "chunk-2", a)
for n, c in list(zip(codes, conds))[8:14]:
    add_case(f"holdout-net-nohyphen-{n}", "holdout-error-code", f"What is the meaning of NET{n}?", f"net-{n}", "chunk-1", c)
add_case("holdout-net-lower", "holdout-error-code", "what does net-7310 mean", "net-7310", "chunk-1", conds[0])
add_case("holdout-net-explain", "holdout-error-code", "Explain NET-7301 for me", "net-7301", "chunk-1", conds[2])

# B. near-duplicate service runbooks ---------------------------------------------
services = ["mailer", "scheduler", "tracker", "vault", "indexer", "uploader", "resolver", "archiver"]
for i, s in enumerate(services):
    mb, hours, depth = 5 + 3 * i, 2 + i, 100 + 40 * i
    add_doc(f"svc2-{s}", "sop", [
        f"The {s} service accepts payloads up to {mb} MB. Larger uploads are rejected by the {s} front end.",
        f"A {s} session lasts {hours} hours before the client must sign in again.",
        f"The {s} queue holds at most {depth} pending jobs.",
    ])
    add_case(f"holdout-svc-payload-{s}", "holdout-near-duplicate", f"What is the largest payload the {s} service accepts?", f"svc2-{s}", "chunk-1", f"payloads up to {mb} MB")
    add_case(f"holdout-svc-session-{s}", "holdout-near-duplicate", f"How many hours does a {s} session last?", f"svc2-{s}", "chunk-2", f"{hours} hours")
    add_case(f"holdout-svc-queue-{s}", "holdout-near-duplicate", f"How many pending jobs can the {s} queue hold?", f"svc2-{s}", "chunk-3", f"at most {depth} pending jobs")

# C. paraphrased facts (typed and open-ended) -------------------------------------
para = [
    ("Idle sessions on the admin console are closed after 25 minutes.", "closed after 25 minutes", "After how much inactivity is an operator logged out of the management UI?"),
    ("The status page refreshes every 45 seconds.", "every 45 seconds", "How frequently does the health dashboard update itself?"),
    ("Mirrored traffic is limited to 2 percent of production requests.", "2 percent of production requests", "What portion of live requests is copied to the shadow target?"),
    ("The control plane listens for operators on port 8443.", "port 8443", "Which socket does the management API accept connections on?"),
    ("Certificate expiry alerts fire at 09:30 local time.", "09:30 local time", "When during the day do notices about lapsing certificates go out?"),
    ("A drained node keeps serving existing streams for up to 120 seconds.", "up to 120 seconds", "How long may an evacuated server finish ongoing sessions?"),
    ("Policy bundles are signed with the release key before distribution.", "signed with the release key", "What protects rule packages from tampering in transit?"),
    ("Operators approve emergency routing changes in the incident channel.", "incident channel", "Where do on-call staff sign off on urgent path modifications?"),
    ("The shadow environment is rebuilt from scratch every Sunday.", "every Sunday", "How often is the staging replica recreated?"),
    ("Each tenant may create up to 40 custom routes.", "up to 40 custom routes", "What is the limit of user-defined paths per customer?"),
    ("Failed deploys are rolled back automatically after 3 failed health checks.", "after 3 failed health checks", "How many bad readiness probes trigger an automatic revert of a release?"),
    ("Archived tickets move to cold storage after 90 days.", "after 90 days", "When are closed cases relocated to long-term storage?"),
    ("The audit export is delivered as a compressed CSV file.", "compressed CSV file", "In what format does the compliance report arrive?"),
    ("The replay tool re-sends captured requests against the staging gateway.", "re-sends captured requests", "What does the traffic playback utility do?"),
    ("Latency histograms use 15 buckets between 1 ms and 10 s.", "15 buckets", "How many bins make up the response time distribution?"),
    ("API keys are rotated by the owner and expire after 180 days.", "expire after 180 days", "What is the validity period of a programmatic credential?"),
    ("Support must page the secondary on-call if the primary does not answer within 10 minutes.", "within 10 minutes", "How quickly must the backup engineer be alerted when the first one is silent?"),
    ("Rollouts are frozen during the last week of each quarter.", "last week of each quarter", "When are releases prohibited around financial period end?"),
    ("The edge cache is purged with the invalidate command.", "invalidate command", "How can stale cached objects at the perimeter be flushed?"),
    ("Maintenance windows start at 03:00 UTC on Tuesdays.", "03:00 UTC on Tuesdays", "At what moment does the weekly service downtime begin?"),
]
for i, (text, quote, q) in enumerate(para, 1):
    add_doc(f"para2-{i:02d}", "faq", [text, "The rule applies to all production gateways in the support workspace."])
    add_case(f"holdout-para-{i:02d}", "holdout-paraphrase", q, f"para2-{i:02d}", "chunk-1", quote)

# D. identifier lookups with a new prefix -------------------------------------------
sym = ["stalled batch jobs", "garbled email subjects", "duplicate invoices", "slow dashboard tiles", "missing shipping labels", "failed webhook retries", "expired tokens in the portal", "stuck upload progress bars", "truncated exports", "wrong time zones in reports", "blank audit pages", "repeated login prompts", "lost mobile sessions", "delayed push messages", "unreadable attachments", "frozen search suggestions"]
cause = ["a worker pool with zero threads", "a charset mismatch in the mail library", "a retried payment callback", "an unindexed dashboard query", "a printer service outage", "a webhook secret that changed", "a token signer using the wrong clock", "a proxy that buffered request bodies", "a row limit left at its default", "a locale file that failed to load", "a template cache that never refreshed", "a cookie scoped to the wrong domain", "a push gateway certificate that expired", "a queue consumer that crashed silently", "a virus scanner that held files", "a suggestion index that was not rebuilt"]
fix = ["restore the worker thread count", "set the mail charset to UTF-8", "deduplicate the payment callback", "add the missing index", "restart the printer service", "update the webhook secret", "sync the signer clock", "disable proxy buffering", "raise the row limit", "reload the locale files", "clear the template cache", "scope the cookie to the root domain", "renew the push certificate", "restart the queue consumer", "whitelist the attachment type", "rebuild the suggestion index"]
for k in range(16):
    n = 4101 + 3 * k
    add_doc(f"tck-{n}", "ticket", [
        f"Ticket TCK-{n} reported {sym[k]} in the support portal.",
        f"The cause of TCK-{n} was {cause[k]}. Support resolved it to {fix[k]}.",
    ])
    add_case(f"holdout-tck-cause-{n}", "holdout-identifier", f"What caused TCK-{n}?", f"tck-{n}", "chunk-2", cause[k])
    if k % 2 == 0:
        add_case(f"holdout-tck-symptom-{n}", "holdout-identifier", f"What problem did TCK-{n} report?", f"tck-{n}", "chunk-1", sym[k])

# E. unanswerable (varied styles) ---------------------------------------------------
unans = [
    "What does NET-9999 mean?", "How do I recover from NET-8123?", "What caused TCK-9001?", "What problem did TCK-4102 report?",
    "What is the largest payload the notifier service accepts?", "How many hours does a gallery session last?",
    "How many retries does the mailer service use?", "What is the idle timeout of the scheduler service?",
    "How many pending jobs can the archiver backlog hold overnight?", "What is the encryption algorithm used by the vault service?",
    "How long do scheduled jobs keep their logs?", "Which port does the mailer service listen on?",
    "How often is the production database vacuumed?", "What is the maximum number of tenants per cluster?",
    "How many hours of audit history can the tracker service display?", "What percentage of requests is sampled for tracing?",
    "At what time are invoices generated?", "Who is the vendor of the load balancer hardware?",
    "What is the monthly cost of a gateway license?", "Which programming language is the control plane written in?",
    "How long is the warranty on gateway appliances?", "What is the CEO's email address?",
    "How many employees work in the support team?", "What is the default password of the admin console?",
]
for q in unans:
    add_case(f"holdout-none-{len(cases)}", "holdout-unanswerable", q)

(DATA / "support-corpus-holdout.jsonl").write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n", encoding="utf8")
(DATA / "support-eval-holdout.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf8")
print(len(docs), "documents,", sum(len(d["chunks"]) for d in docs), "chunks,", len(cases), "cases,", sum(c["answerable"] for c in cases), "answerable")
