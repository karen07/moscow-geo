#!/usr/bin/env python3

import argparse
import json
import math
import sys
from html import escape
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

DEFAULT_WIDTH = 1000
DEFAULT_PADDING = 20
DEFAULT_FILL = "#777cb8"
DEFAULT_STROKE = "#30366d"
DEFAULT_FILL_OPACITY = 0.3
DEFAULT_STROKE_WIDTH = 1.0


def die(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Convert polygon GeoJSON to a standalone, transparent SVG."
    )
    parser.add_argument(
        "geojson", help="input GeoJSON geometry, Feature or FeatureCollection"
    )
    parser.add_argument("svg", help="output SVG file")
    parser.add_argument(
        "--bounds",
        help="GeoJSON used to define the viewport (use the same bounds for SVG overlays)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"SVG width in pixels; default: {DEFAULT_WIDTH}",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=DEFAULT_PADDING,
        help=f"viewport padding in pixels; default: {DEFAULT_PADDING}",
    )
    parser.add_argument(
        "--simplify-m",
        type=float,
        default=0.0,
        help="optional SVG-only topology-preserving simplification in EPSG:3857 meters",
    )
    parser.add_argument(
        "--fill",
        default=DEFAULT_FILL,
        help=f"polygon fill color; default: {DEFAULT_FILL} (use none for no fill)",
    )
    parser.add_argument(
        "--stroke",
        default=DEFAULT_STROKE,
        help=f"polygon stroke color; default: {DEFAULT_STROKE}",
    )
    parser.add_argument(
        "--fill-opacity",
        type=float,
        default=DEFAULT_FILL_OPACITY,
        help=f"polygon fill opacity from 0 to 1; default: {DEFAULT_FILL_OPACITY}",
    )
    parser.add_argument(
        "--stroke-width",
        type=float,
        default=DEFAULT_STROKE_WIDTH,
        help=f"stroke width in SVG pixels; default: {DEFAULT_STROKE_WIDTH}",
    )
    args = parser.parse_args(argv[1:])

    if args.width <= 0 or not math.isfinite(args.padding):
        parser.error("width must be positive and padding must be finite")
    if args.padding < 0 or 2 * args.padding >= args.width:
        parser.error("padding must be nonnegative and smaller than half of width")
    if not math.isfinite(args.fill_opacity) or not 0 <= args.fill_opacity <= 1:
        parser.error("fill-opacity must be between 0 and 1")
    if not math.isfinite(args.stroke_width) or args.stroke_width < 0:
        parser.error("stroke-width must be nonnegative and finite")
    if not math.isfinite(args.simplify_m) or args.simplify_m < 0:
        parser.error("simplify-m must be nonnegative and finite")

    output = Path(args.svg).resolve()
    inputs = [Path(args.geojson).resolve()]
    if args.bounds:
        inputs.append(Path(args.bounds).resolve())
    if output in inputs:
        parser.error("output SVG must not overwrite an input GeoJSON")
    return args


def read_features(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("type") == "FeatureCollection":
            features = data.get("features", [])
        elif data.get("type") == "Feature":
            features = [data]
        else:
            features = [{"geometry": data, "properties": {}}]

        result = []
        skipped = 0
        for feature in features:
            if feature.get("geometry") is None:
                continue
            geometry = shape(feature["geometry"])
            if geometry.is_empty:
                continue
            if geometry.geom_type not in ("Polygon", "MultiPolygon"):
                skipped += 1
                continue
            properties = feature.get("properties") or {}
            result.append((geometry, str(properties.get("name") or "")))
        if not result:
            die(f"{path}: no nonempty polygons found")
        return result, skipped
    except (OSError, ValueError, KeyError, TypeError) as exc:
        die(f"{path}: {exc}")


def project_features(features, transformer):
    return [(transform(transformer.transform, geom), name) for geom, name in features]


def feature_bounds(features):
    min_x = min(geom.bounds[0] for geom, _ in features)
    min_y = min(geom.bounds[1] for geom, _ in features)
    max_x = max(geom.bounds[2] for geom, _ in features)
    max_y = max(geom.bounds[3] for geom, _ in features)
    if not all(map(math.isfinite, (min_x, min_y, max_x, max_y))):
        die("non-finite projected bounds")
    if min_x >= max_x or min_y >= max_y:
        die("the selected bounds have zero width or height")
    return min_x, min_y, max_x, max_y


def number(value):
    if abs(value) < 0.0005:
        return "0"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def ring_path(ring, min_x, max_y, scale, padding):
    coords = ring.coords
    if len(coords) < 4:
        return ""
    points = []
    for x, y, *_ in list(coords)[:-1]:
        screen_x = (x - min_x) * scale + padding
        screen_y = (max_y - y) * scale + padding
        points.append(f"{number(screen_x)} {number(screen_y)}")
    return "M " + " L ".join(points) + " Z"


def geometry_path(geom, min_x, max_y, scale, padding):
    polygons = [geom] if geom.geom_type == "Polygon" else geom.geoms
    parts = []
    for polygon in polygons:
        parts.append(ring_path(polygon.exterior, min_x, max_y, scale, padding))
        for ring in polygon.interiors:
            parts.append(ring_path(ring, min_x, max_y, scale, padding))
    return " ".join(part for part in parts if part)


def main(argv):
    args = parse_args(argv)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    source_features, skipped = read_features(args.geojson)
    features = project_features(source_features, transformer)
    if args.simplify_m > 0:
        features = [
            (geom.simplify(args.simplify_m, preserve_topology=True), name)
            for geom, name in features
        ]

    if args.bounds and Path(args.bounds).resolve() != Path(args.geojson).resolve():
        bounds_features, _ = read_features(args.bounds)
        viewport_features = project_features(bounds_features, transformer)
    else:
        viewport_features = features

    min_x, min_y, max_x, max_y = feature_bounds(viewport_features)
    scale = (args.width - 2 * args.padding) / (max_x - min_x)
    height = math.ceil((max_y - min_y) * scale + 2 * args.padding)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{args.width}" height="{height}" '
            f'viewBox="0 0 {args.width} {height}">'
        ),
        "  <title>" + escape(Path(args.geojson).stem) + "</title>",
        "  <g "
        + f'fill="{escape(args.fill, quote=True)}" '
        + f'fill-opacity="{number(args.fill_opacity)}" '
        + f'stroke="{escape(args.stroke, quote=True)}" '
        + f'stroke-width="{number(args.stroke_width)}" '
        + 'fill-rule="evenodd" stroke-linejoin="round">',
    ]
    for geom, name in features:
        path = geometry_path(geom, min_x, max_y, scale, args.padding)
        if not path:
            continue
        if name:
            lines.append(f'    <path d="{path}"><title>{escape(name)}</title></path>')
        else:
            lines.append(f'    <path d="{path}"/>')

    lines += ["  </g>", "</svg>", ""]
    try:
        Path(args.svg).write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        die(f"{args.svg}: {exc}")

    print(f"Input : {len(features)} polygons")
    if skipped:
        print(f"Skipped: {skipped} non-polygon features")
    print(f"Size  : {args.width} x {height}")
    print(f"Saved : {args.svg}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
