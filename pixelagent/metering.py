"""Validated local cost estimates shared by Responses, Images, and the drawing agent.

Prices are USD / million tokens. They are estimates, not an API spending reservation.
The app uses Standard processing and 30-minute cache writes for GPT-6.2 requests.
"""
import math
import re


class PricingError(ValueError):
    pass


class UsageUnpriced(RuntimeError):
    pass


def is_gpt62(model):
    return isinstance(model, str) and re.fullmatch(r"gpt-6\.2-(?:sol|astra)(?:-\d{4}-\d{2}-\d{2})?", model) is not None


def _builtin_text(model):
    return isinstance(model, str) and re.fullmatch(
        r"gpt-(?:6\.2-(?:sol|astra)|6\.1-(?:sol|terra)|6-(?:astra|luna))(?:-\d{4}-\d{2}-\d{2})?", model) is not None


def price_profile(cfg, model, kind):
    """Refuse missing, partial, negative, or non-finite prices before a paid request."""
    p = cfg.get("pricing", {}).get(model)
    if not isinstance(p, dict):
        raise PricingError(f"No pricing configured for {model}. Add its prices before running a paid request.")
    required = {"text_input", "image_input", "output"} if kind == "image" else {"input", "output"}
    if kind == "llm" and is_gpt62(model):
        required |= {"cached_input", "cache_write_30m", "cache_write_12h", "long_context_threshold",
                     "long_input_multiplier", "long_output_multiplier"}
    fields = required | {"cached_input", "cache_write_30m", "cache_write_12h", "long_context_threshold",
                         "long_input_multiplier", "long_output_multiplier"}
    result = {}
    for key in fields:
        if key not in p:
            if key in required:
                raise PricingError(f"Pricing for {model} is missing {key}.")
            continue
        value = p[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise PricingError(f"Pricing for {model}: {key} must be a finite, nonnegative number.")
        if key in ("long_context_threshold", "long_input_multiplier", "long_output_multiplier") and value <= 0:
            raise PricingError(f"Pricing for {model}: {key} must be above zero.")
        result[key] = value
    long_keys = {"long_context_threshold", "long_input_multiplier", "long_output_multiplier"}
    if long_keys & result.keys() and not long_keys <= result.keys():
        raise PricingError(f"Pricing for {model} has an incomplete long-context profile.")
    return result


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _tokens(value, label, optional=False):
    if value is None and optional:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
        raise UsageUnpriced(f"Missing or invalid {label} in provider usage")
    return int(value)


def usage_fields(usage):
    details = _get(usage, "input_tokens_details")
    out = {"input_tokens": _get(usage, "input_tokens"), "output_tokens": _get(usage, "output_tokens"),
           "cached_tokens": _get(details, "cached_tokens"), "cache_write_tokens": _get(details, "cache_write_tokens")}
    # Keep the split used for image costing; totals alone cannot later reconstruct it.
    out["input_tokens_details"] = {k: _get(details, k) for k in
                                   ("text_tokens", "image_tokens", "cached_tokens", "cache_write_tokens")}
    write_details = _get(details, "cache_write_tokens_details")
    out["input_tokens_details"]["cache_write_tokens_details"] = {
        k: _get(write_details, k) for k in ("ttl_30m_tokens", "ttl_12h_tokens")}
    return out


def usage_price(p, usage, kind, cache_ttl="30m"):
    inputs = _tokens(_get(usage, "input_tokens"), "input_tokens")
    outputs = _tokens(_get(usage, "output_tokens"), "output_tokens")
    details = _get(usage, "input_tokens_details")
    if kind == "image":
        # Both modalities affect the price. Missing nonzero-input detail cannot safely mean "all text".
        image = _tokens(_get(details, "image_tokens"), "image_tokens", optional=inputs == 0)
        text = _tokens(_get(details, "text_tokens"), "text_tokens", optional=inputs == 0)
        if image + text != inputs:
            raise UsageUnpriced("Image token split does not match total input tokens")
        # Without a reliable cached text/image split, estimate all image-request input at full rates.
        # The raw reported cache total remains in the log; no guessed discount is applied.
        return (text * p["text_input"] + image * p["image_input"] + outputs * p["output"]) / 1e6
    cached = _tokens(_get(details, "cached_tokens"), "cached_tokens", optional=True)
    written = _tokens(_get(details, "cache_write_tokens"), "cache_write_tokens", optional=True)
    ordinary = inputs - cached - written
    if ordinary < 0:
        raise UsageUnpriced("Cached and written tokens exceed total input tokens")
    write_details = _get(details, "cache_write_tokens_details")
    if write_details is not None:
        writes = {ttl: _tokens(_get(write_details, f"ttl_{ttl}_tokens"), f"ttl_{ttl}_tokens") for ttl in ("30m", "12h")}
        if sum(writes.values()) != written:
            raise UsageUnpriced("Cache-write duration split does not match total written tokens")
    else:
        # The pinned SDK exposes total writes; newer API responses may also expose the TTL breakdown.
        writes = {cache_ttl: written}
    # Legacy profiles without a cached rate keep their prior conservative input-rate estimate.
    input_cost = ordinary * p["input"] + cached * p.get("cached_input", p["input"])
    for ttl, count in writes.items():
        key = f"cache_write_{ttl}"
        if count and key not in p:
            raise UsageUnpriced("Cache-write pricing is missing")
        input_cost += count * p.get(key, 0)
    long = "long_context_threshold" in p and inputs > p["long_context_threshold"]
    return (input_cost * (p["long_input_multiplier"] if long else 1)
            + outputs * p["output"] * (p["long_output_multiplier"] if long else 1)) / 1e6


def check_usage_guard(guard):
    """Stop this job after unpriceable usage; a deliberate new job gets a new guard."""
    if guard.get("blocked"):
        raise UsageUnpriced("This job stopped because a successful request returned missing or invalid usage. "
                            "Its cost is unknown; check provider billing before deliberately starting another job.")


def record_usage(project, asset_id, model, kind, role, usage, profile, guard=None, **extra):
    fields = usage_fields(usage)
    base = {"asset": asset_id, "model": model, "kind": kind, "role": role, **fields,
            "price_profile": profile, **extra}
    try:
        if kind == "llm" and extra.get("service_tier") not in (None, "default"):
            raise UsageUnpriced("Provider returned a non-Standard processing tier")
        usd = usage_price(profile, usage, kind)
    except UsageUnpriced:
        if guard is not None:
            guard["blocked"] = True
        project.log_cost({**base, "usd": None, "pricing_status": "unknown"})
        raise
    project.log_cost({**base, "usd": round(usd, 8), "pricing_status": "estimated"})
    return usd


def response_settings(model, effort):
    """Pin Standard processing for built-ins; custom model request shapes stay compatible."""
    result = {}
    if _builtin_text(model):
        result["service_tier"] = "default"
    if is_gpt62(model):
        effort = "low" if effort in ("none", "minimal") else effort
        result["prompt_cache_options"] = {"ttl": "30m"}
    if effort:
        result["reasoning"] = {"effort": effort}
    return result


def request_error(error):
    """Provider exception messages may include credential fragments; persist only type/status."""
    status = getattr(error, "status_code", None)
    suffix = f", HTTP {status}" if isinstance(status, int) else ""
    return RuntimeError(f"OpenAI request failed ({type(error).__name__}{suffix}).")
