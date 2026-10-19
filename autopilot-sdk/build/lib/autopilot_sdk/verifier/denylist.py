"""Denylists for static pre-flight scan (doc §9)."""
BANNED_IMPORT_ROOTS = {
    "openai",
    "anthropic",
    "cohere",
    "mistralai",
    "replicate",
    "groq",
    "ai21",
    "together",
    "huggingface_hub",
    "google.generativeai",
    "vertexai",
    "boto3",  # warning-worthy: general AWS SDK, usable for Bedrock
    "litellm",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_community",
    "ollama",
}

# Imports that are suspicious but not proof of misuse on their own
# (e.g. `import openai` with base_url pointed at the organisers' gateway is legal
# per §10.4 — the host check is the real signal). Callers may surface
# these as warnings.
WARN_ONLY_IMPORT_ROOTS = {
    "openai",  # OpenAI-wire-compatible clients may target the organisers' gateway
    "boto3",
}

# Literal substrings searched anywhere in source (catches base_url= overrides)
BANNED_URL_SUBSTRINGS = [
    "api.openai.com",
    "openai.azure.com",
    "oaiusercontent.com",
    "api.anthropic.com",
    "claude.ai",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.cohere.ai",
    "api.cohere.com",
    "api.mistral.ai",
    "api.together.xyz",
    "api.together.ai",
    "api.groq.com",
    "api.perplexity.ai",
    "api.deepseek.com",
    "openrouter.ai",
    "api.replicate.com",
    "bedrock-runtime",
    ".amazonaws.com",
    "api.x.ai",
    "api.ai21.com",
    "api-inference.huggingface.co",
]

# Heuristics that don't fail the build but flag for manual review
OBFUSCATION_HEURISTICS = [
    r"base64\.b64decode",
    r"\bexec\s*\(",
    r"\beval\s*\(",
    r"codecs\.decode\(.*rot13",
]
