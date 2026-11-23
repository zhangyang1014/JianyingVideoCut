"""Check the explicit commercial terms that can be compared without a model."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from ..models.production import BrandFact, StoryboardShot


_HAN_DIGITS = "零〇一二两三四五六七八九"
_DIGITS = dict(zip(_HAN_DIGITS, (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9), strict=True))
_HAN_NUMBER = _HAN_DIGITS + "十百千万亿"
# Bound group sizes before carrying out numeric conversion on arbitrary draft text.
_ARABIC = r"(?:\d{1,3}(?:,\d{3}){1,5}|\d{1,18})(?:\.\d{1,10})?[万亿]?"
_CHINESE = rf"[{_HAN_NUMBER}]{{1,24}}(?:点[{_HAN_DIGITS}]{{1,10}})?"
_NUMBER = rf"(?:(?:[-+负]\s*)?(?:{_ARABIC}|{_CHINESE}))"
_SEPARATOR = r"\s*(?:[、/～~–—-]|和|或|及|到|至)\s*"
_UNITS = {
    "mL": ("毫升", "ml"),
    "L": ("升", "l"),
    "°C": ("摄氏度", "摄氏", "°c", "度"),
    "元": ("人民币", "块钱", "元", "块"),
    "美元": ("美元", "美金", "usd"),
    "港元": ("港元", "港币", "hkd"),
    "%": ("%",),
    "百分点": ("个百分点", "百分点"),
    "折": ("折",),
    "g": ("克", "g"),
    "kg": ("公斤", "千克", "kg"),
    "mg": ("毫克", "mg"),
    "cm": ("厘米", "cm"),
    "mm": ("毫米", "mm"),
    "m": ("米", "m"),
    "W": ("瓦特", "瓦", "w"),
    "kW": ("千瓦", "kw"),
    "V": ("伏特", "伏", "v"),
    "档": ("档位", "档"),
    "层过滤": ("层过滤", "层滤网"),
    "层防护": ("层防护", "层保护"),
    "根滤芯": ("根滤芯", "个滤芯", "支滤芯", "枚滤芯"),
    "秒出水": ("秒出水", "秒即出水", "秒即刻出水"),
    "年质保": ("年保修", "年质保"),
}
_ALIASES = {alias: unit for unit, aliases in _UNITS.items() for alias in aliases}
_UNIT_PATTERN = "|".join(re.escape(alias) for alias in sorted(_ALIASES, key=len, reverse=True))
_AFTER = re.compile(rf"(?<![a-z\d.{_HAN_NUMBER}点])(?P<values>{_NUMBER}(?:{_SEPARATOR}{_NUMBER}){{0,11}})"
                    rf"\s*(?P<unit>{_UNIT_PATTERN})(?![a-z\d])")
_PREFIX_UNITS = {"百分之": "%", "人民币": "元", "¥": "元", "$": "美元"}
_BEFORE = re.compile(rf"(?P<unit>百分之|人民币|[¥$])\s*(?P<value>{_NUMBER})(?![\d.{_HAN_NUMBER}点])")
_CONVERSIONS = {"L": ("mL", 1000), "kg": ("g", 1000), "mg": ("g", Decimal("0.001")),
                "m": ("cm", 100), "mm": ("cm", Decimal("0.1")), "kW": ("W", 1000)}
_NEGATION = r"(?:(?P<negative>不再|不支持|不提供|并非|没有|并不|不|非)\s*)?"
_OFFER = re.compile(_NEGATION + r"(?P<term>终[身生]|免费|包邮|免运费|免息|买一送一|限时|限量)")
_OFFER_ALIASES = {"终生": "终身", "免运费": "包邮"}
_LIFETIME_FREE = re.compile(_NEGATION + r"终[身生]\s*免费")
_TEMPERATURE_CONTEXT = re.compile(r"温度|水温|室温|温水|调到|调温|加热|制冷|冷藏")
_PRICE_CONTEXT = re.compile(r"价格|到手|售价|每台|花了|只要|优惠|便宜")
_LIST_ELEMENT = re.compile(rf"(?:^|{_SEPARATOR})(?P<value>{_NUMBER})")


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("−", "-")


def _chinese_integer(text: str) -> int:
    for character, value in (("亿", 100_000_000), ("万", 10_000)):
        leading, separator, trailing = text.partition(character)
        if separator:
            remainder = _chinese_integer(trailing)
            if trailing in _DIGITS:
                remainder *= value // 10
            return (_chinese_integer(leading) if leading else 1) * value + remainder
    if all(character in _DIGITS for character in text):
        return int("".join(str(_DIGITS[character]) for character in text) or "0")

    total = 0
    digit = 0
    places = {"十": 10, "百": 100, "千": 1000}
    for character in text:
        if character in places:
            total += (digit or 1) * places[character]
            digit = 0
        else:
            digit = _DIGITS[character]
    previous_place = places.get(text[-2:-1], 10)
    return total + digit * (previous_place // 10)


def _number(text: str) -> Decimal:
    token = text.strip()
    unsigned = token.lstrip("+-负").strip().replace(",", "")
    sign = -1 if token.startswith(("-", "负")) else 1
    if unsigned[0].isdecimal():
        multiplier = {"万": 10_000, "亿": 100_000_000}.get(unsigned[-1], 1)
        return sign * Decimal(unsigned if multiplier == 1 else unsigned[:-1]) * multiplier
    integer, _, fraction = unsigned.partition("点")
    tail = "".join(str(_DIGITS[character]) for character in fraction if character in _DIGITS)
    return sign * Decimal(f"{_chinese_integer(integer)}.{tail}")


def _quantities(text: str) -> set[tuple[str, Decimal]]:
    normalized = _normalized(text)
    amounts = {(_PREFIX_UNITS[match["unit"]], _number(match["value"]))
               for match in _BEFORE.finditer(normalized)}
    for match in _AFTER.finditer(normalized):
        alias = match["unit"]
        context = normalized[max(0, match.start() - 8):match.end() + 5]
        following = normalized[match.end():].lstrip()
        if alias == "度" and not _TEMPERATURE_CONTEXT.search(context):
            continue
        if alias == "档" and following.startswith(("节目", "栏目")):
            continue
        if alias == "块" and following and following[0].isalnum() and not _PRICE_CONTEXT.search(context):
            continue
        unit = _ALIASES[alias]
        canonical_unit, factor = _CONVERSIONS.get(unit, (unit, 1))
        amounts.update((canonical_unit, _number(part["value"]) * factor)
                       for part in _LIST_ELEMENT.finditer(match["values"]))
    return amounts


def _quantity_labels(amounts: set[tuple[str, Decimal]]) -> list[str]:
    return [f"{format(value.normalize(), 'f')} {unit}" for unit, value in sorted(amounts)]


def _offer_terms(text: str) -> set[str]:
    return {("不" if match["negative"] else "") + _OFFER_ALIASES.get(match["term"], match["term"])
            for match in _OFFER.finditer(_normalized(text))}


def _mentions_product(text: str, product: str) -> bool:
    name = re.sub(r"\s+", "", _normalized(product))
    if not name:
        return False
    prefix = r"(?<![a-z\d])" if re.match(r"[a-z\d]", name[0]) else ""
    suffix = r"(?![a-z\d])" if re.match(r"[a-z\d]", name[-1]) else ""
    return bool(re.search(prefix + r"\s*".join(re.escape(character) for character in name) + suffix, _normalized(text)))


def locked_copy_changes(original: StoryboardShot, polished_spoken: str, polished_subtitle: str, product: str) -> list[str]:
    """Compare values in each copy field, allowing equivalent unit and number spellings."""
    changes = []
    for label, before, after in (("台词", original.spoken_text, polished_spoken), ("字幕", original.subtitle, polished_subtitle)):
        old_amounts, new_amounts = _quantities(before), _quantities(after)
        old_offers, new_offers = _offer_terms(before), _offer_terms(after)
        for key, values in (("缺少原稿规格", _quantity_labels(old_amounts - new_amounts)),
                            ("新增规格", _quantity_labels(new_amounts - old_amounts)),
                            ("缺少原稿活动条款", sorted(old_offers - new_offers)),
                            ("新增活动条款", sorted(new_offers - old_offers))):
            if values:
                changes.append(f"{label}{key} {'、'.join(values)}")
        if _mentions_product(before, product) and not _mentions_product(after, product):
            changes.append(f"{label}缺少原稿型号 {product}")
    return changes


def unsubstantiated_quantities(shot: StoryboardShot, facts: Sequence[BrandFact]) -> list[str]:
    """Find explicit copy quantities missing from the shot's cited facts."""
    cited = set(shot.fact_ids)
    supported = {quantity for fact in facts if fact.id in cited for quantity in _quantities(fact.text)}
    observed = _quantities(shot.spoken_text) | _quantities(shot.subtitle)
    return _quantity_labels(observed - supported)


def draft_fact_warnings(text: str, facts: Sequence[BrandFact]) -> list[str]:
    """Surface possible evidence gaps in preserved copy without changing it."""
    supported = {quantity for fact in facts for quantity in _quantities(fact.text)}
    warnings = [f"商品资料中未核对到 {value}，请确认型号和适用条件。"
                for value in _quantity_labels(_quantities(text) - supported)]
    has_lifetime_free = any(not match["negative"] for match in _LIFETIME_FREE.finditer(_normalized(text)))
    has_offer_fact = any(not match["negative"] for fact in facts
                         for match in _LIFETIME_FREE.finditer(_normalized(fact.text)))
    if has_lifetime_free and not has_offer_fact:
        warnings.append("口播提到终身免费，请核对购买渠道、适用范围和活动条款。")
    return warnings
