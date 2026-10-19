"""Domain tables for runtime network guard (doc §10.3).

Literal hostnames shipped with the SDK, updated via SDK releases —
NOT participant-editable. Maintaining this list is an ongoing
operational task (see §17).
"""
ALLOWED_HOSTS = {
    "api.autopilot.gdg.dev",  # our own Backend API
}

BLOCKED_HOST_SUFFIXES = {
    # OpenAI
    "api.openai.com",
    "openai.com",
    "oaiusercontent.com",
    "openai.azure.com",
    # Anthropic
    "api.anthropic.com",
    "claude.ai",
    # Google
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "aistudio.google.com",
    # Cohere / Mistral / AI21
    "api.cohere.ai",
    "api.cohere.com",
    "api.mistral.ai",
    "api.ai21.com",
    # Aggregators / multi-model gateways
    "openrouter.ai",
    "api.together.xyz",
    "api.together.ai",
    "api.groq.com",
    "api.replicate.com",
    "api.perplexity.ai",
    "api.deepseek.com",
    "api.x.ai",
    # AWS Bedrock (any region)
    "bedrock-runtime.amazonaws.com",
    # HuggingFace hosted inference (hub itself NOT blocked)
    "api-inference.huggingface.co",
}

# Connecting to localhost on these ports strongly suggests a local LLM
# server (Ollama / LM Studio / vLLM / text-generation-webui).
LOCAL_LLM_PORTS = {11434, 1234, 5000, 5001, 7860, 8000, 8080, 8888}
