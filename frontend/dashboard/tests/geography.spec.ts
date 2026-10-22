import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import {
  countLocalities,
  locateReport,
  type Localities,
} from "../src/lib/geography";
import {
  approximateLocation,
  type Report,
} from "../src/lib/reports";

function report(latitude: number, longitude: number): Report {
  return {
    schema_version: 3,
    id: "test",
    title: "Prueba",
    text: "Evidencia",
    source: "sms",
    source_ref: "",
    geolocation: { latitude, longitude, accuracy_m: 20 },
    created_at: "2026-01-01T00:00:00+00:00",
    analysis: null,
    fraud_positive: null,
    hash: "a".repeat(64),
  };
}

test("aproxima las coordenadas en una cuadrícula estable sin mutar el reporte", () => {
  const input = report(4.65123, -74.06234);
  const original = JSON.stringify(input);
  const approximate = approximateLocation(input.geolocation);

  expect(approximate.latitude).toBeCloseTo(4.65, 10);
  expect(approximate.longitude).toBeCloseTo(-74.07, 10);
  expect(JSON.stringify(input)).toBe(original);
  expect(approximate).not.toEqual(input.geolocation);
});

test("asigna puntos interiores a las 20 localidades sin cambiar el registro", () => {
  const data = JSON.parse(
    readFileSync(
      new URL("../public/geo/localidades.geojson", import.meta.url),
      "utf8",
    ),
  ) as Localities;
  const located = data.features.map(({ properties }) => {
    const input = report(...properties.center);
    const original = JSON.stringify(input);
    const result = locateReport(input, data);
    expect(result.localityCode).toBe(properties.code);
    expect(JSON.stringify(input)).toBe(original);
    expect(result.hash).toBe(input.hash);
    return result;
  });
  expect(Object.keys(countLocalities(located))).toHaveLength(20);
  expect(locateReport(report(6.244, -75.581), data).localityCode).toBe(
    "outside",
  );
  expect(locateReport(report(Number.NaN, -74.1), data).localityCode).toBe(
    "missing",
  );
  expect(locateReport(report(91, -74.1), data).localityCode).toBe("missing");
});

test("respeta huecos, multipolígonos y límites en la clasificación", () => {
  const data: Localities = {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { code: "01", name: "Prueba", center: [0.5, 0.5] },
        geometry: {
          type: "MultiPolygon",
          coordinates: [
            [
              [
                [0, 0],
                [4, 0],
                [4, 4],
                [0, 4],
                [0, 0],
              ],
              [
                [1, 1],
                [1, 2],
                [2, 2],
                [2, 1],
                [1, 1],
              ],
            ],
            [
              [
                [6, 6],
                [7, 6],
                [7, 7],
                [6, 7],
                [6, 6],
              ],
            ],
          ],
        },
      },
    ],
  };
  expect(locateReport(report(0.5, 0.5), data).localityCode).toBe("01");
  expect(locateReport(report(1.5, 1.5), data).localityCode).toBe("outside");
  expect(locateReport(report(6.5, 6.5), data).localityCode).toBe("01");
  expect(locateReport(report(0, 0), data).localityCode).toBe("01");
});
