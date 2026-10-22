"""Genera capas locales para Leaflet sin enviar el GeoJSON del usuario a terceros.

Requiere Shapely solo para preparar datos; hasher.py no depende de esta librería.
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from shapely import STRtree, make_valid, set_precision
from shapely.geometry import mapping, shape


LOCALITIES_URL = ("https://sig.catastrobogota.gov.co/arcgis/rest/services/"
                  "ordenamientoterritorial/localidad/MapServer/0")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if path.exists() and path.read_text(encoding="utf-8") == encoded:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description="Preparar manzanas y localidades de Bogotá para Leaflet")
    parser.add_argument("--source", type=Path, required=True, help="MANZ.geojson original")
    parser.add_argument("--localities", type=Path, required=True, help="GeoJSON oficial de localidades en EPSG:4326")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "frontend/dashboard/public/geo")
    args = parser.parse_args()
    original = args.source.read_bytes()
    blocks = json.loads(original.decode("utf-8-sig"))
    localities = json.loads(args.localities.read_text(encoding="utf-8-sig"))
    if len(localities.get("features", [])) != 20:
        raise ValueError("La capa oficial debe contener las 20 localidades")
    localities["features"].sort(key=lambda feature: feature["properties"]["LOCCODIGO"])
    official_shapes = [make_valid(shape(feature["geometry"])) for feature in localities["features"]]
    tree = STRtree(official_shapes)
    names = {"01": "Usaquén", "03": "Santa Fe", "04": "San Cristóbal", "09": "Fontibón",
             "10": "Engativá", "14": "Los Mártires", "15": "Antonio Nariño", "17": "La Candelaria",
             "19": "Ciudad Bolívar"}
    features = []
    for feature, geometry in zip(localities["features"], official_shapes):
        code = feature["properties"]["LOCCODIGO"].zfill(2)
        center = geometry.representative_point()
        features.append({"type": "Feature", "id": code,
                         "properties": {"code": code, "name": names.get(code, feature["properties"]["LOCNOMBRE"].title()),
                                        "center": [center.y, center.x]},
                         "bbox": list(geometry.bounds), "geometry": mapping(geometry)})
    save(args.output / "localidades.geojson", {"type": "FeatureCollection", "features": features})

    # Un archivo por localidad: Leaflet los solicita solo al acercarse al mapa.
    groups = defaultdict(list)
    counts = Counter()
    bounds = {}
    for feature in blocks["features"]:
        geometry = shape(feature["geometry"])
        if geometry.is_empty:
            raise ValueError(f"Manzana sin geometría: {feature['properties'].get('MANCODIGO')}")
        if not geometry.is_valid:
            geometry = make_valid(geometry)
        point = geometry.representative_point()
        candidates = sorted(tree.query(point).tolist())
        matches = [index for index in candidates if official_shapes[index].covers(point)]
        code = features[matches[0]]["properties"]["code"] if matches else "outside"
        simplified = geometry.simplify(0.00001, preserve_topology=True)
        reduced = set_precision(simplified, grid_size=0.000001)
        if reduced.is_empty:
            reduced = simplified  # No perder manzanas menores a la rejilla.
        if not reduced.is_valid:
            raise ValueError(f"Geometría inválida tras simplificar: {feature['properties']['MANCODIGO']}")
        mapped = mapping(reduced)
        groups[code].append({"type": "Feature", "properties": {
            "code": feature["properties"]["MANCODIGO"], "sector": feature["properties"]["SECCODIGO"]},
            "geometry": mapped})
        counts[code] += 1
        west, south, east, north = geometry.bounds
        previous = bounds.get(code, (west, south, east, north))
        bounds[code] = [min(west, previous[0]), min(south, previous[1]),
                        max(east, previous[2]), max(north, previous[3])]
    for code, group in groups.items():
        save(args.output / "manzanas" / f"{code}.geojson", {"type": "FeatureCollection", "features": group})
    # Archivos vacíos explícitos para localidades sin manzanas en el adjunto.
    for feature in features:
        code = feature["properties"]["code"]
        if code not in groups:
            save(args.output / "manzanas" / f"{code}.geojson", {"type": "FeatureCollection", "features": []})
    manifest = {"source": args.source.name, "source_sha256": hashlib.sha256(original).hexdigest(),
                "source_bytes": len(original), "source_features": len(blocks["features"]),
                "unique_block_codes": len({feature["properties"]["MANCODIGO"] for feature in blocks["features"]}),
                "localities_source": LOCALITIES_URL, "localities_attribution": "Secretaría Distrital de Planeación / Catastro Bogotá",
                "crs": "EPSG:4326", "simplification_degrees": 0.00001, "coordinate_decimals": 6,
                "assignment": "representative point within official locality; unmatched blocks retained as outside",
                "counts": dict(sorted(counts.items())),
                "layers": [{"code": code, "bbox": bbox} for code, bbox in sorted(bounds.items())]}
    save(args.output / "manifest.json", manifest)
    output_bytes = sum(path.stat().st_size for path in args.output.rglob("*.geojson"))
    print(json.dumps({"source_features": len(blocks["features"]), "generated_features": sum(counts.values()),
                      "unassigned_blocks": counts["outside"], "output_bytes": output_bytes,
                      "localities": len(features)}, indent=2))


if __name__ == "__main__":
    main()
