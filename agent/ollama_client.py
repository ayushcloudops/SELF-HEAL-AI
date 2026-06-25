"""
ollama_client.py
================

A small, dependency-light client for talking to a locally running Ollama
instance. It is used by the AI Self-Healing Terraform Pipeline to send
Terraform troubleshooting prompts to the ``qwen2.5-coder:7b`` model.

Design goals:
    * Use only the ``requests`` library (no heavyweight SDKs).
    * Robust retry logic with exponential backoff.
    * Explicit timeout handling.
    * Defensive exception handling — the agent must never crash the pipeline.
    * Structured parsing of the model response.

The Ollama ``/api/generate`` endpoint returns a JSON object whose ``response``
field contains the generated text. We optionally ask the model for JSON output
(``format="json"``) and attempt to parse it.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("ollama_client")

DEFAULT_ENDPOINT = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5-coder:7b"


class OllamaError(Exception):
    """Raised when the Ollama API cannot be reached or returns an error."""


@dataclass
class OllamaResponse:
    """Structured representation of an Ollama generation result."""

    raw_text: str
    parsed_json: Optional[Dict[str, Any]] = None
    model: str = DEFAULT_MODEL
    eval_count: Optional[int] = None
    total_duration_ns: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_json(self) -> bool:
        return self.parsed_json is not None


class OllamaClient:
    """Thin wrapper around the Ollama ``/api/generate`` endpoint."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout: int = 300,
        max_retries: int = 3,
        backoff_base: float = 2.0,
    ) -> None:
        self.endpoint = endpoint.rstrip()
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        # Derive the base URL (strip the /api/generate path) for tags/health.
        self.base_url = self.endpoint.split("/api/")[0]

    # ------------------------------------------------------------------ #
    # Health / availability helpers
    # ------------------------------------------------------------------ #
    def is_reachable(self, timeout: int = 5) -> bool:
        """Return True if the Ollama server responds to a tags request."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=timeout)
            return resp.status_code == 200
        except requests.RequestException as exc:
            logger.warning("Ollama not reachable: %s", exc)
            return False

    def model_available(self, timeout: int = 5) -> bool:
        """Return True if ``self.model`` is present in the local model list."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=timeout)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            names = {m.get("name", "") for m in models}
            # Ollama tags can include the :latest implicit tag.
            return any(
                name == self.model or name.split(":")[0] == self.model.split(":")[0]
                for name in names
            )
        except requests.RequestException as exc:
            logger.warning("Could not list Ollama models: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        as_json: bool = True,
        temperature: float = 0.1,
        num_ctx: int = 8192,
    ) -> OllamaResponse:
        """Send ``prompt`` to the model and return a structured response.

        Parameters
        ----------
        prompt:
            The full user prompt.
        system:
            Optional system prompt that sets the model persona.
        as_json:
            If True, request JSON-formatted output from the model and attempt
            to parse it.
        temperature:
            Sampling temperature. Low values keep troubleshooting output
            deterministic and focused.
        num_ctx:
            Context window size in tokens.
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }
        if system:
            payload["system"] = system
        if as_json:
            payload["format"] = "json"

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "Calling Ollama (attempt %d/%d) model=%s",
                    attempt,
                    self.max_retries,
                    self.model,
                )
                resp = requests.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                raw_text = data.get("response", "")
                if not raw_text:
                    raise OllamaError("Ollama returned an empty response field.")

                parsed = self._safe_parse_json(raw_text) if as_json else None

                return OllamaResponse(
                    raw_text=raw_text,
                    parsed_json=parsed,
                    model=data.get("model", self.model),
                    eval_count=data.get("eval_count"),
                    total_duration_ns=data.get("total_duration"),
                    metadata={
                        k: v
                        for k, v in data.items()
                        if k not in {"response", "context"}
                    },
                )

            except requests.Timeout as exc:
                last_error = exc
                logger.warning("Ollama request timed out (attempt %d): %s", attempt, exc)
            except requests.ConnectionError as exc:
                last_error = exc
                logger.warning("Ollama connection error (attempt %d): %s", attempt, exc)
            except requests.HTTPError as exc:
                last_error = exc
                logger.warning("Ollama HTTP error (attempt %d): %s", attempt, exc)
            except (ValueError, OllamaError) as exc:
                last_error = exc
                logger.warning("Ollama response error (attempt %d): %s", attempt, exc)

            # Exponential backoff before the next attempt.
            if attempt < self.max_retries:
                sleep_for = self.backoff_base ** attempt
                logger.info("Retrying in %.1fs ...", sleep_for)
                time.sleep(sleep_for)

        raise OllamaError(
            f"Ollama generation failed after {self.max_retries} attempts: {last_error}"
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_parse_json(text: str) -> Optional[Dict[str, Any]]:
        """Attempt to parse JSON from a possibly noisy model response.

        Models sometimes wrap JSON in markdown fences or add stray prose.
        This method tries a few strategies before giving up.
        """
        # 1. Direct parse.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. Strip markdown code fences.
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            # Drop a leading language hint such as "json\n".
            if "\n" in cleaned:
                cleaned = cleaned.split("\n", 1)[1]
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

        # 3. Extract the outermost {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse JSON from model output.")
        return None
