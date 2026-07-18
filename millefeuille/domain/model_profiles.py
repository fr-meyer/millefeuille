"""Bundled offline model-profile previews for Millefeuille."""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL_PROFILE_BUNDLE: dict[str, Any] = {
    "schema_version": "millefeuille-model-profile/v0.1",
    "default_profile": "research-default",
    "profiles": {
        "research-default": {
            "extract_ocr": {
                "backend": "mistral",
                "model": "mistral-ocr-latest",
                "provider": "mistral",
                "record_usage": True,
            },
            "structure": {
                "backend": "pageindex-tree",
                "model": "pageindex-tree-default",
                "provider": "pageindex",
                "record_usage": True,
            },
            "summarize_page": {
                "backend": "chat",
                "model": "openai/gpt-5.6-sol",
                "provider": "openai",
                "reasoning_effort": "xhigh",
                "fast_mode": "off",
                "prompt_version": "summary-page-v1",
                "record_usage": True,
            },
            "summarize_section": {
                "backend": "chat",
                "model": "openai/gpt-5.6-sol",
                "provider": "openai",
                "reasoning_effort": "xhigh",
                "fast_mode": "off",
                "prompt_version": "summary-section-v1",
                "record_usage": True,
            },
            "summarize_full_paper": {
                "backend": "chat",
                "model": "openai/gpt-5.6-sol",
                "provider": "openai",
                "reasoning_effort": "xhigh",
                "fast_mode": "off",
                "prompt_version": "summary-full-paper-v1",
                "record_usage": True,
            },
            "paper_card": {
                "backend": "chat",
                "model": "gpt-5",
                "provider": "openai",
                "temperature": 0.1,
                "prompt_version": "paper-card-v1",
                "record_usage": True,
            },
            "classify": {
                "backend": "chat",
                "model": "gpt-5",
                "provider": "openai",
                "temperature": 0.0,
                "prompt_version": "classify-v1",
                "require_taxonomy_version": True,
                "record_usage": True,
            },
        },
        "offline-preview": {
            "summarize_page": {
                "backend": "fixture",
                "model": "offline-preview",
                "provider": "none",
                "record_usage": False,
            },
            "summarize_section": {
                "backend": "fixture",
                "model": "offline-preview",
                "provider": "none",
                "record_usage": False,
            },
            "summarize_full_paper": {
                "backend": "fixture",
                "model": "offline-preview",
                "provider": "none",
                "record_usage": False,
            },
            "paper_card": {
                "backend": "fixture",
                "model": "offline-preview",
                "provider": "none",
                "record_usage": False,
            },
            "classify": {
                "backend": "fixture",
                "model": "offline-preview",
                "provider": "none",
                "require_taxonomy_version": True,
                "record_usage": False,
            },
        },
    },
}
