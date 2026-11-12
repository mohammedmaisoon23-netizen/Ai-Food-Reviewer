"""Turn whatever the user typed (name, "name, area", or a Google Maps link) into shop details.

No paid APIs: Google Maps links are parsed from the URL itself, and coordinates are
reverse-geocoded through OpenStreetMap Nominatim (free, rate-limited, best effort).
"""
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx

from config import HTTP_USER_AGENT

_URL_RE = re.compile(r"https?://\S+", re.I)
_MAPS_HOSTS = ("google.com", "goo.gl", "maps.app.goo.gl", "g.co", "google.co.in")


@dataclass
class ShopInfo:
    name: str
    area: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    source: str = "text"
    notes: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        return f"{self.name}, {self.area}" if self.area else self.name


def parse_shop_input(raw: str) -> ShopInfo:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Please enter a shop name, location or Google Maps link.")

    m = _URL_RE.search(raw)
    if m and any(h in m.group(0).lower() for h in _MAPS_HOSTS):
        return _parse_maps_url(m.group(0).rstrip(".,)"), raw)

    # Plain text: "Shop Name, Area" or just "Shop Name"
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    name = parts[0]
    area = ", ".join(parts[1:])
    return ShopInfo(name=name, area=area, source="text")


def _parse_maps_url(url: str, raw: str) -> ShopInfo:
    final_url = url
    try:
        with httpx.Client(follow_redirects=True, timeout=10, headers={"User-Agent": HTTP_USER_AGENT}) as client:
            resp = client.get(url)
            final_url = str(resp.url)
    except Exception:
        pass  # fall back to whatever is in the original URL

    name = ""
    pm = re.search(r"/maps/place/([^/@?]+)", final_url)
    if pm:
        name = unquote(pm.group(1)).replace("+", " ").strip()
    if not name:
        q = re.search(r"[?&]q=([^&]+)", final_url)
        if q:
            name = unquote(q.group(1)).replace("+", " ").strip()

    lat = lng = None
    cm = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", final_url) or re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final_url)
    if cm:
        lat, lng = float(cm.group(1)), float(cm.group(2))

    # If the user typed extra words next to the link, treat them as the shop name.
    leftover = _URL_RE.sub("", raw).strip(" ,-")
    if not name and leftover:
        name = leftover

    area = _reverse_geocode(lat, lng) if lat is not None else ""
    if not name:
        name = "this local food spot"

    info = ShopInfo(name=name, area=area, lat=lat, lng=lng, source="google_maps")
    if not pm:
        info.notes.append("Could not read the shop name from the link; add it in the box for better results.")
    return info


def _reverse_geocode(lat: Optional[float], lng: Optional[float]) -> str:
    if lat is None or lng is None:
        return ""
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 16},
            headers={"User-Agent": HTTP_USER_AGENT},
            timeout=8,
        )
        addr = r.json().get("address", {})
        bits = [addr.get("suburb") or addr.get("neighbourhood") or addr.get("village"),
                addr.get("city") or addr.get("town") or addr.get("county") or addr.get("state_district")]
        return ", ".join(b for b in bits if b)
    except Exception:
        return ""
