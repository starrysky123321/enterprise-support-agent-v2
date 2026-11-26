"""Check explicit fact requests before returning local extractive answers."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class EvidenceText:
    text: str
    product_version: str | None = None
    semantic_score: float | None = None


_CLAUSE = re.compile(r"\band\s+(?=(?:at\s+)?(?:what|which|how|when|why|who)\b)|[?？\n]+", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|[。！？；\n]+")
_TERM = re.compile(r"[a-z0-9]+(?:[_.-][a-z0-9]+)*", re.I)
_QUESTION_WORDS = frozenset(
    "a about after an and are as at be before can could current did do does for from had has have "
    "how in is it may must of on or should that the this to was were what when where which "
    "who why will with would by their per many long often time number maximum minimum "
    "default service gateway use used uses".split()
)
_VERSION = re.compile(r"(?<![a-z0-9_.])v?(\d+\.\d+(?:\.\d+)*)(?![a-z0-9_]|\.\d)", re.I)
_DEFAULT = re.compile(r"\bdefault\b|默认", re.I)
_TIMEOUT = re.compile(r"\btime[ -]?outs?\b|超时", re.I)
_DEFAULT_TIMEOUT_QUESTION = re.compile(
    r"^\s*(?:what|which)\b|\bhow\s+(?:long|many)\b|多少|多久|多长", re.I,
)
_DURATION = re.compile(
    r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"[\s-]*(?:milliseconds?|ms|seconds?|secs?|minutes?|mins?|hours?|days?|weeks?)\b"
    r"|[零一二两三四五六七八九十百\d]+(?:\.\d+)?\s*(?:毫秒|秒|分钟|小时|天|周)",
    re.I,
)
_FREQUENCY = re.compile(
    r"\bevery\b|\beach\b|\b(?:hourly|daily|nightly|weekly|monthly|quarterly|yearly|annually)\b"
    r"|\bper (?:hour|day|week|month|year)\b|\bonce an?\b|每(?:天|周|月|小时|年)", re.I,
)
_PORT = re.compile(r"\bport\s*(?:number\s*)?(?:is\s*|:\s*)?\d{2,5}\b|端口\s*\d{2,5}", re.I)
_PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent)", re.I)
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
_COUNT = re.compile(r"\b\d[\d,]*\b")
_HTTP_STATUS = re.compile(r"\bHTTP\s+[1-5]\d{2}\b|\bstatus(?:\s+code)?\s+[1-5]\d{2}\b", re.I)
# Calibrated for the local BGE/Chroma L2 setup, not probabilities of answerability.
SEMANTIC_STRONG = 0.60
SEMANTIC_WEAK = 0.57
_LOOKUPS = (
    (re.compile(r"\b(?:HTTP\s+)?status code\b|HTTP状态码", re.I), _HTTP_STATUS),
    (
        re.compile(r"\b(?:what|which)\b.*\bdate\b|哪天|几月几日|什么日期|日期.{0,10}多少", re.I),
        re.compile(
            r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"
            r"|\b(?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+\d{1,2}\b"
            r"|(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]",
            re.I,
        ),
    ),
    (
        re.compile(r"\b(?:what|which)\b.*\b(?:shell |cli )?command\b|什么命令|哪条命令|命令是什么", re.I),
        re.compile(
            r"(?m)^\s*\$\s+[a-zA-Z]\S*[^\n]*"
            r"|(?i:command|run|execute|命令|执行)[:：]?\s*`[a-zA-Z][^\s`]*(?: [^`\n]+)?`"
            r"|\x60\x60\x60(?:bash|shell|sh|console)\s*\n[^\x60]+",
        ),
    ),
    (
        re.compile(r"\btable name\b|数据库表名|表名.{0,8}(?:什么|哪|是)", re.I),
        re.compile(
            r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?[\w.\"\x60]+"
            r"|\btable(?:\s+name)?(?:\s+is|\s*[:=])\s*`?[A-Za-z_][\w.]*"
            r"|\btable\s+`[A-Za-z_][\w.]*`|表名(?:是|为|[:：])\s*`?[A-Za-z_][\w.]*",
            re.I,
        ),
    ),
    (
        re.compile(r"\bhow long\b|\bhow many (?:milli)?seconds\b|"
                   r"\bhow many (?:minutes|hours|days|weeks)\b|多久|多长时间|多少(?:毫秒|秒|分钟|小时|天)", re.I),
        _DURATION,
    ),
    (re.compile(r"\bhow often\b|多久一次|频率", re.I), _FREQUENCY),
    (re.compile(r"\b(?:which|what)\s+(?:network\s+)?port\b|哪个端口|端口是多少", re.I), _PORT),
    (
        re.compile(r"\bwhat\s+(?:share|percent(?:age)?|proportion|fraction)\b|多少比例|占比", re.I),
        _PERCENT,
    ),
    (re.compile(r"\bat what (?:hour|time)\b|\bwhat time\b|几点", re.I), _CLOCK),
    (
        re.compile(r"\bhow many\b|\b(?:maximum|minimum|max|min)(?:imum)?\s+number\b|\bnumber of\b|多少个", re.I),
        _COUNT,
    ),
)

# Identifiers such as INC-7999 or CFG-5120 must appear verbatim in the evidence that answers a question.
_IDENTIFIER = re.compile(r"\b[a-z]{2,}-\d+[a-z]?\b", re.I)
# "the <name> service" asks about one named service; evidence about a different service cannot answer it.
_SERVICE_SUBJECT = re.compile(r"\b([a-z][a-z0-9-]{2,})\s+service\b", re.I)
_NAMED_SUBJECT = re.compile(r"\b(?:a|an|the)\s+([a-z][a-z0-9-]{2,})\s+(?:session|queue|backlog)\b", re.I)
_PER_SUBJECT = re.compile(r"\bper\s+([a-z][a-z0-9-]+)\b", re.I)
_RETENTION_SUBJECT = re.compile(
    r"\bhow long (?:do|does)\s+(.+?)\s+(?:keep|retain|store)\b", re.I,
)
_GENERIC_SUBJECTS = frozenset(
    "the this that each every any which what our your same another other support web cloud "
    "backend upstream downstream internal external managed shared delivery pending active idle "
    "login user current retry".split()
)
_FOCUS = re.compile(
    r"^(?:what|which|who)\s+(?:is|are|was|were)\s+(?:the\s+|a\s+|an\s+)?"
    r"(.+?)(?:\s+(?:of|for|in|on|used|that|per)\b|[?？]|$)"
    r"|^how many\s+(.+?)(?:\s+(?:does|do|can|are|is|must|should|will)\b|[?？]|$)",
    re.I,
)
_GENERIC_FOCUS = frozenset(
    "root exact current meaning limit times hour second minute day date calendar port "
    "shell command table name status code".split()
)


def _focus_supported(question: str, text: str) -> bool:
    """An explicit requested attribute must occur in the candidate, not just its subject."""
    question = re.sub(r"\bnumber of\s+", "", question, flags=re.I)
    question = re.sub(r"\bkind of\s+", "", question, flags=re.I)
    question = re.sub(r"^how long (?:is|are)\s+", "What is ", question, flags=re.I)
    if not re.match(r"^(?:what|which)\s+(?:is|are|was|were|does|do)\b", question, re.I):
        question = re.sub(
            r"^(which|what) ((?:[a-z-]+\s+){0,2}[a-z-]+) (?:is|does|do|can)\b",
            r"What is \2 of", question, flags=re.I,
        )
    match = _FOCUS.search(question.strip())
    if not match:
        return True
    focus = _terms(next(group for group in match.groups() if group)) - _GENERIC_FOCUS
    if not focus:
        return True
    return len(focus & _terms(text)) >= max(1, (len(focus) + 1) // 2)


def _grounded(question: str, evidence_text: str) -> bool:
    """Reject evidence that never mentions an identifier or service the question names."""
    haystack = evidence_text.casefold()
    for subject in _PER_SUBJECT.findall(question):
        if re.search(rf"\b{re.escape(subject)}s?\b", haystack, re.I) is None:
            return False
    retention = _RETENTION_SUBJECT.search(question)
    if retention and not (_terms(retention[1]) & _terms(evidence_text)):
        return False
    for identifier in _IDENTIFIER.findall(question):
        if re.search(rf"(?<![a-z0-9-]){re.escape(identifier.casefold())}(?![a-z0-9-])", haystack) is None:
            return False
    for subject in (*_SERVICE_SUBJECT.findall(question), *_NAMED_SUBJECT.findall(question)):
        subject = subject.casefold()
        if subject not in _GENERIC_SUBJECTS and re.search(rf"\b{re.escape(subject)}\b", haystack) is None:
            return False
    return True


def _terms(text: str) -> set[str]:
    return {
        term.removesuffix("s")
        if term.isalpha() and len(term) >= 4 and not term.endswith(("ss", "us")) else term
        for term in _TERM.findall(text.casefold())
        if term not in _QUESTION_WORDS
    }


def _versions(text: str) -> set[str]:
    return set(_VERSION.findall(_DURATION.sub(" ", text)))


def _supporting_excerpt(
    evidence: EvidenceText, value: re.Pattern[str], *,
    default_timeout: bool, versions: set[str],
) -> str | None:
    version = (evidence.product_version or "").strip().casefold().removeprefix("v")
    if default_timeout and versions and version and version not in versions:
        return None
    if not default_timeout:
        match = value.search(evidence.text)
        if match is None:
            return None
        if match.end() <= 280:
            return evidence.text
        return evidence.text[max(0, match.start() - 100):match.end() + 100]

    document_versions = {version} if version else _versions(evidence.text)
    for sentence in _SENTENCE.split(evidence.text):
        if not (_DEFAULT.search(sentence) and _TIMEOUT.search(sentence) and value.search(sentence)):
            continue
        matching_versions = _versions(sentence) or document_versions
        if not versions or versions <= matching_versions:
            return sentence
    return None


def question_clauses(question: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSE.split(question) if clause.strip()]


def select_local_evidence(
    question: str, candidates: list[EvidenceText], *, limit: int,
) -> list[tuple[int, str]]:
    """Prefer explicit values for typed lookups and refuse when a required value is absent.

    Similarity belongs to each candidate. A related chunk cannot lend its score or its
    identifier to another chunk that contains an unrelated number.
    """
    # Ignore isolated matches such as 'rate' in unrelated finance and telemetry questions.
    def supported(candidate: EvidenceText, clause: str, *, typed: bool) -> bool:
        if not _grounded(clause, candidate.text) or not _focus_supported(clause, candidate.text):
            return False
        overlap = len(_terms(clause) & _terms(candidate.text))
        needed = 2 if typed and len(_terms(clause)) >= 3 else 1
        if len(_terms(clause)) < 3 or _IDENTIFIER.search(clause) or "_" in clause:
            needed = 0 if not re.search(r"[a-z]", clause, re.I) else 1
        if overlap >= needed:
            return True
        threshold = SEMANTIC_WEAK if typed and overlap else SEMANTIC_STRONG
        return candidate.semantic_score is not None and candidate.semantic_score >= threshold

    selected: dict[int, str] = {}
    for clause in question_clauses(question):
        default_timeout = bool(
            _DEFAULT.search(clause) and _TIMEOUT.search(clause) and _DEFAULT_TIMEOUT_QUESTION.search(clause)
        )
        lookup = _DURATION if default_timeout else next(
            (value for intent, value in _LOOKUPS if intent.search(clause)), None,
        )
        if lookup is None:
            matches = [
                (index, candidate.text) for index, candidate in enumerate(candidates)
                if supported(candidate, clause, typed=False)
            ]
            if not matches:
                return []
            for index, text in matches[:limit]:
                selected.setdefault(index, text)
            continue

        versions = _versions(clause) if default_timeout else set()
        for index, candidate in enumerate(candidates):
            if not supported(candidate, clause, typed=True):
                continue
            excerpt = _supporting_excerpt(
                candidate, lookup, default_timeout=default_timeout, versions=versions,
            )
            if excerpt is not None:
                if index in selected and excerpt not in selected[index]:
                    selected[index] += "\n" + excerpt
                else:
                    selected.setdefault(index, excerpt)
                break
        else:
            return []

    if len(selected) > limit:
        return []
    if not _grounded(question, " ".join(selected.values())):
        return []
    return list(selected.items())
