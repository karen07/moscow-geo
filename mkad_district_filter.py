#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

DEFAULT_OUT = "districts_in_mkad.geojson"
DEFAULT_MIN_RATIO = 0.1
DEFAULT_SIMPLIFY_M = 60.0
DEFAULT_BUFFER_M = 0.0


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Filter Moscow district polygons by the MKAD boundary."
    )
    p.add_argument("districts", help="input districts GeoJSON")
    p.add_argument("mkad", help="input MKAD GeoJSON")
    p.add_argument(
        "out",
        nargs="?",
        default=DEFAULT_OUT,
        help=f"output GeoJSON, default: {DEFAULT_OUT}",
    )
    p.add_argument(
        "--no-clip",
        action="store_true",
        help="keep original district geometry instead of clipping it by MKAD",
    )
    p.add_argument(
        "--keep-properties",
        action="store_true",
        help="keep input feature properties in the output",
    )
    p.add_argument(
        "--min-ratio",
        type=float,
        default=DEFAULT_MIN_RATIO,
        help=(
            "keep a district if at least this share of its area is inside MKAD; "
            f"default: {DEFAULT_MIN_RATIO}"
        ),
    )
    p.add_argument(
        "--simplify-m",
        type=float,
        default=DEFAULT_SIMPLIFY_M,
        help=(
            "simplify output geometry in meters in EPSG:3857; "
            f"default: {DEFAULT_SIMPLIFY_M}, use 0 to disable"
        ),
    )
    p.add_argument(
        "--buffer-m",
        type=float,
        default=DEFAULT_BUFFER_M,
        help=(
            "buffer MKAD geometry in meters before filtering; "
            f"default: {DEFAULT_BUFFER_M}"
        ),
    )
    return p.parse_args(argv[1:])


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_geom_any(path):
    obj = load_json(path)
    t = obj.get("type")

    if t == "FeatureCollection":
        feats = obj.get("features") or []
        if not feats:
            die(f"{path}: empty FeatureCollection")
        return shape(feats[0]["geometry"])

    if t == "Feature":
        return shape(obj["geometry"])

    return shape(obj)


def project_4326_to_3857(geom):
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    return transform(tr.transform, geom)


def project_3857_to_4326(geom):
    tr = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    return transform(tr.transform, geom)


def feature_properties(feature, keep_properties):
    if keep_properties:
        return dict(feature.get("properties") or {})
    return {}


def output_geometry(geom_used_p):
    if geom_used_p.is_empty:
        return None

    return mapping(project_3857_to_4326(geom_used_p))


def main(argv):
    args = parse_args(argv)

    mkad_wgs84 = load_geom_any(args.mkad)
    mkad = project_4326_to_3857(mkad_wgs84)

    if args.buffer_m != 0.0:
        mkad = mkad.buffer(args.buffer_m)

    data = load_json(args.districts)
    if data.get("type") != "FeatureCollection":
        die(f"{args.districts}: expected FeatureCollection")

    feats = data.get("features") or []
    kept = []

    for feature in feats:
        geom_wgs84 = shape(feature["geometry"])
        geom_p = project_4326_to_3857(geom_wgs84)

        if geom_p.is_empty or geom_p.area <= 0:
            continue

        inter_p = geom_p.intersection(mkad)
        ratio = inter_p.area / geom_p.area if geom_p.area else 0.0
        rep_inside = mkad.contains(geom_p.representative_point())

        if ratio < args.min_ratio and not rep_inside:
            continue

        geom_used_p = geom_p if args.no_clip else inter_p

        if args.simplify_m > 0.0:
            geom_used_p = geom_used_p.simplify(
                args.simplify_m,
                preserve_topology=True,
            )

        out_geom = output_geometry(geom_used_p)
        if out_geom is None:
            continue

        kept.append(
            {
                "type": "Feature",
                "properties": feature_properties(feature, args.keep_properties),
                "geometry": out_geom,
            }
        )

    out = {
        "type": "FeatureCollection",
        "features": kept,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Input : {len(feats)}")
    print(f"Kept  : {len(kept)}")
    print(f"Saved : {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
