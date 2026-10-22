"""Rasgos locales de una URL; nunca realiza peticiones de red."""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import string
from collections import Counter
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .domains import domain_parts, strip_www


HASH_BINS = 128
NGRAM_RANGE = (2, 3, 4)
SUSPICIOUS_WORDS = (
    "account",
    "actualizar",
    "banco",
    "bank",
    "confirm",
    "cuenta",
    "invoice",
    "login",
    "password",
    "pago",
    "pay",
    "secure",
    "signin",
    "support",
    "update",
    "verify",
    "wallet",
    "webscr",
)

LEXICAL_FEATURE_NAMES = (
    "url_length",
    "hostname_length",
    "path_length",
    "query_length",
    "fragment_length",
    "letter_ratio",
    "digit_ratio",
    "special_ratio",
    "unique_char_ratio",
    "url_entropy",
    "hostname_entropy",
    "dot_count",
    "hyphen_count",
    "underscore_count",
    "slash_count",
    "at_count",
    "percent_count",
    "equals_count",
    "ampersand_count",
    "subdomain_count",
    "path_depth",
    "query_parameter_count",
    "max_hostname_label_length",
    "max_digit_run",
    "alpha_digit_transition_ratio",
    "suspicious_word_count",
    "registrable_domain_length",
    "public_suffix_length",
    "is_https",
    "hostname_is_ip",
    "has_explicit_port",
    "has_userinfo",
    "has_punycode",
    "has_percent_encoding",
    "has_https_token_in_hostname",
)
FEATURE_NAMES = LEXICAL_FEATURE_NAMES + tuple(
    f"char_ngram_hash_{index:03d}" for index in range(HASH_BINS)
)


def _ratio(value: int, total: int) -> float:
    return value / max(total, 1)


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _is_ip(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _max_run(value: str, predicate) -> int:
    longest = current = 0
    for character in value:
        current = current + 1 if predicate(character) else 0
        longest = max(longest, current)
    return longest


def _hashed_character_ngrams(value: str) -> list[float]:
    bins = [0.0] * HASH_BINS
    padded = f"^{value.casefold()}$"
    total = 0
    for size in NGRAM_RANGE:
        for offset in range(max(0, len(padded) - size + 1)):
            ngram = padded[offset : offset + size].encode("utf-8", errors="replace")
            digest = hashlib.blake2b(ngram, digest_size=8).digest()
            index = int.from_bytes(digest, "little") % HASH_BINS
            bins[index] += 1.0
            total += 1
    if total:
        bins = [value / total for value in bins]
    return bins


def extract_features(url: str) -> list[float]:
    """Convierte una URL ya normalizada en rasgos lexicos y n-gramas."""

    parsed = urlsplit(url)
    original_hostname = (parsed.hostname or "").casefold()
    hostname = strip_www(original_hostname)
    subdomain, domain, suffix = domain_parts(hostname)

    # `www` no es evidencia de legitimidad. Se elimina de la representacion
    # aprendida para hacer equivalentes google.com y www.google.com.
    analysis_netloc = parsed.netloc.replace(original_hostname, hostname, 1)
    analysis_url = urlunsplit(
        (parsed.scheme, analysis_netloc, parsed.path, parsed.query, parsed.fragment)
    )
    lowered = analysis_url.casefold()
    length = len(analysis_url)
    letters = sum(character.isalpha() for character in analysis_url)
    digits = sum(character.isdigit() for character in analysis_url)
    specials = sum(character in string.punctuation for character in analysis_url)
    host_labels = [part for part in hostname.split(".") if part]
    transitions = sum(
        left.isalnum()
        and right.isalnum()
        and left.isalpha() != right.isalpha()
        for left, right in zip(analysis_url, analysis_url[1:])
    )
    suspicious_word_count = sum(word in lowered for word in SUSPICIOUS_WORDS)

    try:
        port = parsed.port
    except ValueError:
        port = None

    lexical = [
        float(length),
        float(len(hostname)),
        float(len(parsed.path)),
        float(len(parsed.query)),
        float(len(parsed.fragment)),
        _ratio(letters, length),
        _ratio(digits, length),
        _ratio(specials, length),
        _ratio(len(set(analysis_url)), length),
        _entropy(analysis_url),
        _entropy(hostname),
        float(analysis_url.count(".")),
        float(analysis_url.count("-")),
        float(analysis_url.count("_")),
        float(analysis_url.count("/")),
        float(analysis_url.count("@")),
        float(analysis_url.count("%")),
        float(analysis_url.count("=")),
        float(analysis_url.count("&")),
        float(len([part for part in subdomain.split(".") if part])),
        float(len([part for part in parsed.path.split("/") if part])),
        float(len(parse_qsl(parsed.query, keep_blank_values=True))),
        float(max((len(part) for part in host_labels), default=0)),
        float(_max_run(analysis_url, str.isdigit)),
        _ratio(transitions, max(0, length - 1)),
        float(suspicious_word_count),
        float(len(domain) + (1 + len(suffix) if suffix else 0)),
        float(len(suffix)),
        float(parsed.scheme.casefold() == "https"),
        float(_is_ip(hostname)),
        float(port is not None),
        float(parsed.username is not None),
        float("xn--" in hostname),
        float(bool(re.search(r"%[0-9a-fA-F]{2}", analysis_url))),
        float("https" in hostname),
    ]
    return lexical + _hashed_character_ngrams(analysis_url)
