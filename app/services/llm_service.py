"""Protein interaction extraction from paper text.

This module only ever produces REAL data. There is no placeholder, no
regex-based stand-in and no invented fallback. If no LLM is configured, or a
call fails, extraction returns nothing and says so. An empty graph is
recoverable; a graph full of invented edges is not.

The previous version had a "placeholder" backend that regex-matched capitalised
tokens out of each abstract and declared them interaction partners. It
produced nodes like STRING, KEGG, MCF7 and BACKGROUND - databases, cell lines
and section headings. That code is gone.
"""

import asyncio
import json
import os
import re
import logging
from typing import Dict, List, Optional

from app.models.interaction import Interaction

logger = logging.getLogger(__name__)

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Groq rotates its catalogue often, so this is only a starting point. List what
# your key can actually use with:
#   curl -H "Authorization: Bearer $PROTEIN_LLM_API_KEY" ^
#        https://api.groq.com/openai/v1/models
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"

# Extraction only needs the opening of an abstract; sending all 2,000+
# characters of 50 abstracts blows through free-tier token limits.
ABSTRACT_CHARS = int(os.environ.get("PROTEIN_LLM_ABSTRACT_CHARS", "1500"))

# Reasoning models (gpt-oss, o-series, most local models) spend tokens on
# hidden reasoning BEFORE emitting JSON. gpt-oss-120b used 145 reasoning
# tokens for a one-sentence answer, so a 400-token cap made it fail with
# "max completion tokens reached before generating a valid json object".
# The cap is a ceiling, not a cost -- billing follows tokens actually used.
EXTRACTION_MAX_TOKENS = int(os.environ.get("PROTEIN_LLM_MAX_TOKENS", "1200"))

NOT_CONFIGURED_MESSAGE = (
    "No LLM configured. Set PROTEIN_LLM_API_KEY in .env (Groq is assumed) to "
    "enable extraction. This app will not fabricate interactions."
)


class LLMError(RuntimeError):
    """Raised when a real backend call fails."""


class LLMRateLimited(LLMError):
    """HTTP 429 - the provider's token/request budget is exhausted."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


# gpt-oss (and some reasoning models) wrap their final answer in channel tags:
#   <channel>analysis<message>...</message></channel>
#   <channel>final<message>{"interactions": []}</message></channel>
_CHANNEL_RE = re.compile(
    r"<channel>\s*final\s*<message>(.*?)</message>\s*</channel>", re.DOTALL | re.IGNORECASE
)


class LLMBackend:
    async def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        raise NotImplementedError


class OpenAICompatibleBackend(LLMBackend):
    """Client for any OpenAI-compatible /chat/completions endpoint.

    Works with Groq, OpenAI, Together, Fireworks, DeepInfra, vLLM and
    Ollama's OpenAI shim. Not every provider supports every parameter, so
    `response_format` is retried without on rejection.
    """

    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 60,
                 max_retries: int = 5):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        # Minimum spacing between calls. Groq's free tier allows only ~8000
        # tokens/minute, and a single extraction costs ~1000, so a burst of
        # parallel/rapid calls gets 429'd almost immediately.
        try:
            self.min_interval = float(os.environ.get("PROTEIN_LLM_MIN_INTERVAL", "0.6"))
        except ValueError:
            self.min_interval = 0.6
        self._last_call = 0.0
        self._pace_lock = asyncio.Lock()

    async def _pace(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._pace_lock:
            wait = self.min_interval - (loop.time() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = loop.time()

    async def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        messages = [{"role": "user", "content": prompt}]

        base = {"model": self.model, "messages": messages, "temperature": 0,
                "max_tokens": max_tokens}
        attempts = [
            {**base, "response_format": {"type": "json_object"}},
            # (a) Some providers reject response_format outright.
            dict(base),
            # (b) Reasoning models can exhaust max_tokens while still thinking,
            #     producing "max completion tokens reached before generating a
            #     valid json object". Retrying with a bigger budget is the only
            #     thing that fixes that.
            {**base, "max_tokens": max_tokens * 4,
             "response_format": {"type": "json_object"}},
        ]

        last_error: Optional[Exception] = None
        for index, payload in enumerate(attempts):
            try:
                data = await self._post_with_retry(payload)
            except LLMRateLimited:
                # Out of token budget. Retrying in another format will not help.
                raise
            except Exception as exc:
                last_error = exc
                if index < len(attempts) - 1 and self._is_retryable(exc):
                    continue
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc

            content = self._extract_content(data)
            if content is not None:
                return content

            last_error = LLMError(f"unexpected response shape: {str(data)[:200]}")
            if index == 0:
                continue
            raise last_error

        raise LLMError(str(last_error or "all attempts failed"))

    async def _post_with_retry(self, payload: dict):
        """POST once, retrying only on 429 with the provider's own hint."""
        last_exc: Optional[LLMRateLimited] = None
        for retry in range(self.max_retries):
            await self._pace()
            try:
                return await asyncio.to_thread(self._post, payload)
            except LLMRateLimited as exc:
                last_exc = exc
                if retry == self.max_retries - 1:
                    break
                delay = min(exc.retry_after or 2.0, 30.0) * (1.5 ** retry)
                await asyncio.sleep(delay)
        raise last_exc

    def _post(self, payload: dict):
        import requests

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text[:300]
            if response.status_code == 404 and "model" in response.text.lower():
                raise requests.HTTPError(
                    f"HTTP 404: model {self.model!r} is not available to this key. "
                    f"List your models with GET {self.base_url}/models. ({detail})",
                    response=response,
                )
            if response.status_code in (401, 403):
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: key rejected. "
                    f"Check PROTEIN_LLM_API_KEY. ({detail})",
                    response=response,
                )
            if response.status_code == 429:
                # Groq tells us how long to wait: "Please try again in 3.14s".
                hint = 0.0
                match = re.search(r"try again in ([0-9.]+)", response.text)
                if match:
                    try:
                        hint = float(match.group(1))
                    except ValueError:
                        hint = 0.0
                raise LLMRateLimited(
                    f"HTTP 429: rate limited or out of quota. ({detail})",
                    retry_after=hint,
                )
            raise requests.HTTPError(
                f"HTTP {response.status_code}: {detail}", response=response
            )
        return response.json()

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Whether a different request shape might succeed.

        Covers providers that do not implement JSON mode, and reasoning models
        that ran out of tokens before producing valid JSON.
        """
        text = str(exc).lower()
        if "max completion tokens" in text or "json_validate_failed" in text:
            return True
        return "response_format" in text or "json_object" in text or "400" in text

    @staticmethod
    def _extract_content(data) -> Optional[str]:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return None

        # Some reasoning models return the answer inside channel tags.
        for key in ("content", "reasoning_content"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                channel = _CHANNEL_RE.search(value)
                if channel:
                    return channel.group(1).strip()
                if "<channel>" in value:
                    # Only analysis was returned; no usable answer.
                    continue
                return value
        return None


class GroqBackend(OpenAICompatibleBackend):
    def __init__(self, api_key: str, model: str = DEFAULT_GROQ_MODEL, timeout: int = 60):
        super().__init__(api_key, DEFAULT_GROQ_BASE_URL, model, timeout)


class LLMService:
    """Extracts protein-protein interactions and summaries from paper text."""

    EXTRACTION_PROMPT = """You are a biomedical information extraction system.

Task: read the abstract below and list ONLY the protein-protein interactions it actually states or clearly implies.

Strict rules:
- Use official gene symbols in CAPS, e.g. TP53, MDM2, NOT "the p53 protein".
- Report a pair only if a physical or regulatory relationship between the two is stated. Co-occurrence in the same sentence is NOT an interaction.
- Ignore section headers, database names, cell lines, tissue names, disease abbreviations and dataset accessions. They are not proteins.
- The protein of interest is {protein}. At least one side of every pair must be it.
- interaction_type must be exactly one of: physical_binding, regulatory, complex_formation, genetic.
- "context" must be a short verbatim quote from the abstract that justifies the pair.
- If the abstract states no interaction involving {protein}, return an empty list. An empty list is a correct and useful answer.

Abstract:
{abstract}

Respond with JSON only, no commentary:
{{"interactions": [{{"source_protein": "TP53", "target_protein": "MDM2", "interaction_type": "physical_binding", "context": "short quote"}}]}}"""

    SUMMARY_PROMPT = """In 2-3 sentences each, describe the following about {protein}:
1. function
2. pathways
3. subcellular localization

Return JSON only:
{{"function": "...", "pathways": "...", "localization": "..."}}"""

    def __init__(self, backend: LLMBackend = None):
        self.backend = backend or self._build_from_env()
        self._config_error = None
        self._stats = {
            "attempts": 0,
            "succeeded": 0,
            "failed": 0,
            "last_error": None,
            "degraded": False,
        }

    # ------------------------------------------------------------- config

    @staticmethod
    def _build_from_env() -> Optional[LLMBackend]:
        """Return a configured backend, or None plus a reason on `self`.

        Returning None (rather than a fake backend) is deliberate: callers must
        not be able to mistake "no key" for "no interactions found".
        """
        name = os.environ.get("PROTEIN_LLM_BACKEND", "").strip().lower()
        api_key = (os.environ.get("PROTEIN_LLM_API_KEY") or "").strip()
        model = (os.environ.get("PROTEIN_LLM_MODEL") or "").strip()
        base_url = (os.environ.get("PROTEIN_LLM_BASE_URL") or "").strip()

        if not name:
            # Auto-detect: a key with no explicit backend implies Groq, which
            # is the documented default for this project.
            name = "groq" if api_key else ""

        if not name:
            return None

        if name == "groq":
            if not api_key:
                return None
            return GroqBackend(api_key, model or DEFAULT_GROQ_MODEL)

        if name in ("openai", "compatible"):
            if not (api_key and base_url and model):
                return None
            return OpenAICompatibleBackend(api_key, base_url, model)

        if name in ("ollama", "local"):
            return OpenAICompatibleBackend(
                api_key or "ollama",
                base_url or "http://localhost:11434/v1",
                model or "llama3.1",
            )

        return None

    @property
    def is_configured(self) -> bool:
        return self.backend is not None

    @property
    def is_real(self) -> bool:
        """Kept for API compatibility; there is no fake backend any more."""
        return self.is_configured

    @property
    def status(self) -> str:
        if not self.is_configured:
            return "not_configured"
        return "degraded" if self._stats["degraded"] else "ready"

    def health(self) -> dict:
        backend = self.backend
        return {
            "status": self.status,
            "llm_enabled": self.is_configured,
            "backend": type(backend).__name__ if backend else None,
            "model": getattr(backend, "model", None),
            "base_url": getattr(backend, "base_url", None),
            "error": None if self.is_configured else NOT_CONFIGURED_MESSAGE,
            "stats": dict(self._stats),
        }

    # ------------------------------------------------------------- calls

    async def extract_interactions(self, paper_text: str, protein_name: str) -> Dict:
        """Return {"interactions": [...], "source": "llm"|"error"|"none"}.

        Never invents data. If the model is unavailable or a call fails, the
        result is an empty list tagged with the reason.
        """
        if not paper_text or not protein_name:
            logger.warning(
                "extract_interactions called with empty input: paper_text=%s, protein_name=%s",
                bool(paper_text), bool(protein_name),
            )
            return {"interactions": [], "protein_info": {}, "source": "none"}

        if not self.is_configured:
            logger.warning(
                "extract_interactions skipped: LLM not configured for protein '%s'",
                protein_name,
            )
            return {
                "interactions": [],
                "protein_info": {},
                "source": "error",
                "error": NOT_CONFIGURED_MESSAGE,
            }

        self._stats["attempts"] += 1
        # Most abstracts state their interactions in the first paragraph.
        # Truncating keeps the request inside free-tier token budgets.
        abstract_chunk = paper_text.strip()[:ABSTRACT_CHARS]
        logger.info(
            "Extracting interactions: protein='%s', abstract_length=%d, max_tokens=%d",
            protein_name, len(abstract_chunk), EXTRACTION_MAX_TOKENS,
        )
        prompt = self.EXTRACTION_PROMPT.format(
            protein=protein_name, abstract=abstract_chunk
        )
        try:
            raw = await self.backend.complete(prompt, max_tokens=EXTRACTION_MAX_TOKENS)
            interactions = self._parse_interactions(raw, protein_name)
            self._stats["succeeded"] += 1
            # Deliberately do not clear `degraded` here: if any call in this
            # process has failed, the run is partially degraded even if later
            # calls succeed.
            logger.info(
                "Interaction extraction SUCCESS: protein='%s', %d interactions found",
                protein_name, len(interactions),
            )
            return {
                "interactions": interactions,
                "protein_info": {},
                "source": "llm",
            }
        except Exception as exc:
            self._stats["failed"] += 1
            self._stats["last_error"] = str(exc)[:500]
            self._stats["degraded"] = True
            logger.error(
                "Interaction extraction FAILED for protein='%s': %s",
                protein_name, exc,
                exc_info=True,
            )
            return {
                "interactions": [],
                "protein_info": {},
                "source": "error",
                "error": str(exc)[:500],
            }

    async def generate_protein_description(self, protein_name: str) -> Dict:
        if not self.is_configured:
            logger.info(
                "generate_protein_description skipped: LLM not configured for '%s'",
                protein_name,
            )
            return {}
        self._stats["attempts"] += 1
        logger.info(
            "Generating protein description for: '%s' (max_tokens=400)",
            protein_name,
        )
        try:
            raw = await self.backend.complete(
                self.SUMMARY_PROMPT.format(protein=protein_name), max_tokens=400
            )
            data = self._load_json(raw)
            self._stats["succeeded"] += 1
            logger.info(
                "Protein description generated for '%s': %d fields",
                protein_name, len(data),
            )
            return {k: v for k, v in data.items() if isinstance(v, str) and v}
        except Exception as exc:
            self._stats["failed"] += 1
            self._stats["last_error"] = str(exc)[:500]
            self._stats["degraded"] = True
            logger.error(
                "Protein description generation FAILED for '%s': %s",
                protein_name, exc,
                exc_info=True,
            )
            return {}

    # ------------------------------------------------------------ parsing

    def _parse_interactions(self, raw: str, protein_name: str) -> List[Dict]:
        data = self._load_json(raw)
        items = data.get("interactions")
        if items is None:
            raise LLMError("model response has no 'interactions' key")

        out: List[Dict] = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source_protein") or "").strip()
            target = str(item.get("target_protein") or "").strip()
            if not source or not target or source == target:
                continue
            # The prompt requires one side to be the protein of interest; drop
            # hallucinated pairs that involve neither it nor anything we can
            # attribute back to it.
            if protein_name and protein_name.upper() not in (source.upper(), target.upper()):
                continue
            key = tuple(sorted((source, target)))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "source_protein": source,
                    "target_protein": target,
                    "interaction_type": Interaction.normalize_type(
                        item.get("interaction_type", "")
                    ),
                    "context": (str(item.get("context")).strip()[:400]
                                if item.get("context") else None),
                }
            )
        return out

    @staticmethod
    def _load_json(raw: str) -> dict:
        """Parse JSON out of a model response, tolerating chatty wrappers."""
        if raw is None:
            raise LLMError("empty response")
        text = raw.strip()
        if not text:
            raise LLMError("empty response")

        # ```json ... ``` fences
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fall back to the outermost {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMError(f"could not parse JSON: {exc}; got {text[:200]!r}") from exc
        raise LLMError(f"no JSON object in response: {text[:200]!r}")


# Backwards-compatible alias for the name used in older code.
MockLLMService = LLMService
