#!/usr/bin/env python3
"""mlrun.py <upstream stage script> [args ...] — run one MagentLess stage, unchanged, under
two run-time substitutions that let it use a local OpenAI-compatible server
(environment: MAGENTLESS_ROOT = the upstream checkout, plus upstream's own variables
OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL / OPENAI_EMBED_URL, SWEBENCH_LANG,
PROJECT_FILE_LOC):

1. Output-token floor (MAGENTLESS_MIN_TOKENS, default 16384). Upstream budgets 300
   (file localization), 1024 (repair) ... output tokens, sized for GPT-4o. On
   llama.cpp / vLLM a reasoning model's thinking counts against max_tokens, so such
   budgets end before any answer. The floor only raises a budget; prompts, sampling
   (temperature, number of samples) and parsing are upstream's.
2. Embeddings (retrieval stage). Upstream hard-codes
   OpenAIEmbedding(model_name="text-embedding-3-large", api_base=OPENAI_EMBED_URL),
   which accepts only OpenAI's model names; it is replaced by a client for any
   OpenAI-compatible /embeddings endpoint serving MAGENTLESS_EMBED_MODEL.
"""
import os
import runpy
import sys

UP = os.environ["MAGENTLESS_ROOT"]
sys.path.insert(0, UP)

import agentless.util.api_requests as _ar  # noqa: E402

_MIN_TOKENS = int(os.environ.get("MAGENTLESS_MIN_TOKENS", "16384"))
_create_chatgpt_config = _ar.create_chatgpt_config


def create_chatgpt_config(*args, **kwargs):
    config = _create_chatgpt_config(*args, **kwargs)
    config["max_tokens"] = max(int(config.get("max_tokens") or 0), _MIN_TOKENS)
    return config


_ar.create_chatgpt_config = create_chatgpt_config  # before agentless.util.model binds it


def _patch_embeddings():
    import openai
    import llama_index.embeddings.openai as lio
    from llama_index.core.embeddings import BaseEmbedding

    model = os.environ.get("MAGENTLESS_EMBED_MODEL", "nomic-embed-text")

    class LocalEmbedding(BaseEmbedding):
        """OpenAI-compatible /embeddings client (any model name)."""

        def _client(self):
            return openai.OpenAI(base_url=os.environ.get("OPENAI_EMBED_URL"),
                                 api_key=os.environ.get("OPENAI_API_KEY", "none"), timeout=600)

        def _embed(self, texts):
            r = self._client().embeddings.create(model=self.model_name, input=list(texts))
            return [d.embedding for d in sorted(r.data, key=lambda d: d.index)]

        def _get_query_embedding(self, query):
            return self._embed([query])[0]

        def _get_text_embedding(self, text):
            return self._embed([text])[0]

        def _get_text_embeddings(self, texts):
            return self._embed(texts)

        async def _aget_query_embedding(self, query):
            return self._get_query_embedding(query)

    def OpenAIEmbedding(model_name=None, api_base=None, **kwargs):  # noqa: N802 (upstream's name)
        from llama_index.core import Settings  # upstream's token counter is registered there
        return LocalEmbedding(model_name=model, embed_batch_size=32, callback_manager=Settings.callback_manager)

    lio.OpenAIEmbedding = OpenAIEmbedding


if __name__ == "__main__":
    script = os.path.abspath(sys.argv[1])
    if script.endswith(os.path.join("fl", "retrieve.py")):
        _patch_embeddings()
    sys.argv = [script] + sys.argv[2:]
    runpy.run_path(script, run_name="__main__")
