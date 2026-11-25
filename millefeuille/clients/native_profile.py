"""Explicit native SDK selection; no provider/account/agent defaults or auth access."""
import os
import re

def configured_profile():
    value = os.environ.get("MILLEFEUILLE_OPENCLAW_PROFILE")
    if not isinstance(value, str) or re.fullmatch(r"openai:[A-Za-z0-9._+@-]{1,240}", value) is None:
        raise ValueError("native-profile-configuration-required")
    return value
