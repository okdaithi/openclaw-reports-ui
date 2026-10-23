#!/usr/bin/env python3
"""Build static/geo/world.json, the Geographic page's map, from world-atlas.

Input: countries-110m.json from the world-atlas package (Natural Earth
1:110m country outlines, public domain), e.g.
    curl -O https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json
    python3 tools/build_world_map.py countries-110m.json

The outlines are projected once here (Equal Earth) into SVG path data, so
the page needs no mapping library. Antarctica is left out.
"""
import json
import math
import sys
from pathlib import Path

WIDTH = 1000
OUT = Path(__file__).resolve().parent.parent / 'static' / 'geo' / 'world.json'
SKIP = {'010'}  # Antarctica

# ISO 3166 numeric -> the alpha-3 codes the database uses (the countries the
# agent reports on; others keep their numeric id)
ALPHA3 = {
    '784': 'ARE', '036': 'AUS', '076': 'BRA', '156': 'CHN', '276': 'DEU', '826': 'GBR',
    '360': 'IDN', '356': 'IND', '372': 'IRL', '392': 'JPN', '410': 'KOR', '643': 'RUS',
    '682': 'SAU', '792': 'TUR', '840': 'USA', '710': 'ZAF',
}

A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
M = math.sqrt(3) / 2


def equal_earth(lon, lat):
    lam, phi = math.radians(lon), math.radians(lat)
    theta = math.asin(M * math.sin(phi))
    t2 = theta * theta
    t6 = t2 * t2 * t2
    x = lam * math.cos(theta) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)))
    y = theta * (A1 + A2 * t2 + t6 * (A3 + A4 * t2))
    return x, y


def decode_arcs(topology):
    sx, sy = topology['transform']['scale']
    tx, ty = topology['transform']['translate']
    arcs = []
    for arc in topology['arcs']:
        x = y = 0
        points = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)
    return arcs


def ring(indices, arcs):
    points = []
    for index in indices:
        arc = arcs[index] if index >= 0 else arcs[~index][::-1]
        points.extend(arc if not points else arc[1:])
    return points


def unwrap(points):
    """Keep a ring that crosses the 180th meridian in one piece: carry its
    longitudes past +-180 instead of jumping to the far side of the map
    (the part beyond the edge is clipped), centred on the map"""
    out, shift = [], 0.0
    for i, (lon, lat) in enumerate(points):
        if i:
            step = lon + shift - out[-1][0]
            if step > 180:
                shift -= 360
            elif step < -180:
                shift += 360
        out.append((lon + shift, lat))
    mean = sum(lon for lon, _ in out) / len(out)
    if mean > 180:
        out = [(lon - 360, lat) for lon, lat in out]
    elif mean < -180:
        out = [(lon + 360, lat) for lon, lat in out]
    return out


def polygons(geometry, arcs):
    if geometry['type'] == 'Polygon':
        return [[ring(r, arcs) for r in geometry['arcs']]]
    if geometry['type'] == 'MultiPolygon':
        return [[ring(r, arcs) for r in polygon] for polygon in geometry['arcs']]
    return []


def area_centroid(points):
    """Signed area and centroid of a projected ring"""
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if not area:
        return 0.0, points[0]
    return area / 2, (cx / (3 * area), cy / (3 * area))


def main(source):
    topology = json.loads(Path(source).read_text())
    arcs = decode_arcs(topology)
    xmax, ymax = equal_earth(180, 0)[0], equal_earth(0, 90)[1]
    scale = WIDTH / (2 * xmax)
    height = round(2 * ymax * scale)

    def project(lon, lat):
        x, y = equal_earth(lon, lat)
        return round((x + xmax) * scale, 1), round((ymax - y) * scale, 1)

    countries = []
    for geometry in topology['objects']['countries']['geometries']:
        number = geometry.get('id')
        if number in SKIP or number is None:
            continue
        parts, largest = [], (0.0, None)
        for polygon in polygons(geometry, arcs):
            projected = [[project(lon, lat) for lon, lat in unwrap(r)] for r in polygon]
            for points in projected:
                # Drop points that round onto the one before
                kept = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
                if len(kept) < 3:
                    continue
                parts.append('M' + 'L'.join(f'{x:g},{y:g}' for x, y in kept) + 'Z')
            area, centre = area_centroid(projected[0])
            if abs(area) > largest[0]:
                largest = (abs(area), centre)
        if not parts:
            continue
        cx, cy = largest[1]
        countries.append({
            'id': ALPHA3.get(number, number),
            'name': geometry['properties']['name'],
            'd': ''.join(parts),
            'c': [round(cx, 1), round(cy, 1)],
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        'width': WIDTH,
        'height': height,
        'source': 'Natural Earth 1:110m (public domain) via world-atlas 2.0.2; Equal Earth projection',
        'countries': countries,
    }, separators=(',', ':')))
    print(f'{OUT}: {len(countries)} countries, {WIDTH}x{height}, {OUT.stat().st_size // 1024} KB')


if __name__ == '__main__':
    main(sys.argv[1])
