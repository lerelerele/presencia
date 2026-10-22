"""Geocercas sin dependencias nativas: polígonos GeoJSON (con huecos) y
distancia al borde en metros con proyección local equirectangular
(error despreciable a escala de barrio)."""
import json
import math

Ring = list[tuple[float, float]]          # (lon, lat)
Polygon = list[Ring]                      # [exterior, hueco, hueco…]


def parse_zones(geojson: str | dict) -> list[Polygon]:
    data = json.loads(geojson) if isinstance(geojson, str) else geojson
    polys: list[Polygon] = []

    def add_geom(g: dict) -> None:
        t = g.get("type")
        if t == "Polygon":
            polys.append([[(float(x), float(y)) for x, y, *_ in ring] for ring in g["coordinates"]])
        elif t == "MultiPolygon":
            for p in g["coordinates"]:
                polys.append([[(float(x), float(y)) for x, y, *_ in ring] for ring in p])
        elif t == "GeometryCollection":
            for sub in g["geometries"]:
                add_geom(sub)

    t = data.get("type")
    if t == "FeatureCollection":
        for f in data["features"]:
            add_geom(f["geometry"])
    elif t == "Feature":
        add_geom(data["geometry"])
    else:
        add_geom(data)
    if not polys:
        raise ValueError("El GeoJSON no contiene ningún polígono")
    for p in polys:
        if len(p[0]) < 4:
            raise ValueError("Polígono con menos de 3 vértices")
    return polys


def _in_ring(lon: float, lat: float, ring: Ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def in_polygon(lon: float, lat: float, poly: Polygon) -> bool:
    if not _in_ring(lon, lat, poly[0]):
        return False
    return not any(_in_ring(lon, lat, hole) for hole in poly[1:])


def _ring_distance_m(lon: float, lat: float, ring: Ring) -> float:
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0
    best = math.inf
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        ax, ay = (x1 - lon) * kx, (y1 - lat) * ky
        bx, by = (x2 - lon) * kx, (y2 - lat) * ky
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        px, py = ax + t * dx, ay + t * dy
        best = min(best, math.hypot(px, py))
    return best


def distance_to_zones_m(lon: float, lat: float, polys: list[Polygon]) -> float:
    """0 si está dentro; si no, distancia al borde más cercano."""
    if any(in_polygon(lon, lat, p) for p in polys):
        return 0.0
    return min(_ring_distance_m(lon, lat, ring) for p in polys for ring in p)


def inside_with_tolerance(lon: float, lat: float, polys: list[Polygon], tolerance_m: float) -> bool:
    return distance_to_zones_m(lon, lat, polys) <= tolerance_m
