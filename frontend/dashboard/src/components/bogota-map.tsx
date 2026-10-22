import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import type { FeatureCollection } from "geojson";
import { Expand, Layers, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { approximateLocation, coordinates, date } from "@/lib/reports";
import {
  validLocation,
  type GeoManifest,
  type Localities,
  type LocatedReport,
} from "@/lib/geography";
import "leaflet/dist/leaflet.css";

type Props = {
  localities: Localities;
  manifest: GeoManifest;
  reports: LocatedReport[];
  counts: Record<string, number>;
  selectedId: string | null;
  selectedLocality: string;
  onSelectReport: (id: string) => void;
  onSelectLocality: (code: string) => void;
};

const fill = (count: number, max: number) =>
  count === 0
    ? "#3a2027"
    : count / max > 0.66
      ? "#a83f50"
      : count / max > 0.33
        ? "#773d49"
        : "#592d38";
const urbanCenter: L.LatLngExpression = [4.66, -74.115];

export function BogotaMap(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const polygons = useRef<L.GeoJSON | null>(null);
  const markers = useRef<L.LayerGroup | null>(null);
  const callbacks = useRef(props);
  callbacks.current = props;
  const [blocksMessage, setBlocksMessage] = useState(
    "Acércate para ver las manzanas",
  );
  const [blocksLoading, setBlocksLoading] = useState(false);
  const [blocksFailed, setBlocksFailed] = useState(false);

  useEffect(() => {
    if (!container.current) return;
    const map = L.map(container.current, {
      preferCanvas: true,
      zoomControl: false,
      minZoom: 3,
      maxZoom: 19,
      scrollWheelZoom: true,
      keyboard: true,
    });
    mapRef.current = map;
    map.setView(urbanCenter, 11);
    L.control
      .zoom({
        position: "bottomright",
        zoomInTitle: "Acercar",
        zoomOutTitle: "Alejar",
      })
      .addTo(map);
    L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
    map.attributionControl.setPrefix(
      '<a href="https://leafletjs.com" target="_blank" rel="noreferrer">Leaflet</a>',
    );
    map.attributionControl.addAttribution(
      'Manzanas: MANZ · Localidades: <a href="https://sig.catastrobogota.gov.co/arcgis/rest/services/ordenamientoterritorial/localidad/MapServer/0" target="_blank" rel="noreferrer">SDP / Catastro Bogotá</a>',
    );
    map.createPane("blocks").style.zIndex = "350";
    const blockRenderer = L.canvas({ pane: "blocks", padding: 0.25 });
    const blockGroup = L.layerGroup().addTo(map);
    const cache = new Map<string, FeatureCollection>();
    const inFlight = new Map<string, Promise<FeatureCollection>>();
    const controllers = new Set<AbortController>();
    let generation = 0;
    let disposed = false;

    async function loadBlocks() {
      const current = ++generation;
      blockGroup.clearLayers();
      setBlocksFailed(false);
      if (map.getZoom() < 14) {
        setBlocksLoading(false);
        setBlocksMessage("Acércate para ver las manzanas");
        return;
      }
      const bounds = map.getBounds();
      const visible = props.manifest.layers.filter(
        ({ bbox: [west, south, east, north] }) =>
          bounds.intersects([
            [south, west],
            [north, east],
          ]),
      );
      setBlocksLoading(true);
      const results = await Promise.allSettled(
        visible.map(async ({ code }) => {
          if (cache.has(code)) return cache.get(code)!;
          if (!inFlight.has(code)) {
            const controller = new AbortController();
            controllers.add(controller);
            inFlight.set(
              code,
              fetch(`/geo/manzanas/${code}.geojson`, {
                signal: controller.signal,
              })
                .then(async (response) => {
                  if (!response.ok)
                    throw new Error("No se pudo cargar la capa de manzanas");
                  const data = (await response.json()) as FeatureCollection;
                  cache.set(code, data);
                  return data;
                })
                .finally(() => {
                  inFlight.delete(code);
                  controllers.delete(controller);
                }),
            );
          }
          return inFlight.get(code)!;
        }),
      );
      if (disposed || generation !== current) return;
      let failed = false;
      for (const result of results) {
        if (result.status === "fulfilled") {
          L.geoJSON(result.value, {
            interactive: false,
            style: {
              renderer: blockRenderer,
              weight: 0.6,
              color: "#64748b",
              fillColor: "#475569",
              fillOpacity: 0.65,
              pane: "blocks",
            },
          }).addTo(blockGroup);
        } else {
          failed = true;
        }
      }
      setBlocksLoading(false);
      setBlocksFailed(failed);
      setBlocksMessage(
        failed
          ? "No se cargaron algunas manzanas"
          : visible.length
            ? "Manzanas del GeoJSON adjunto"
            : "Sin manzanas en esta zona",
      );
    }

    polygons.current = L.geoJSON(props.localities, {
      style: { color: "#ffffff", weight: 1.8, fillOpacity: 0.86 },
      onEachFeature: (feature, layer) => {
        layer.on("click", () =>
          callbacks.current.onSelectLocality(feature.properties.code),
        );
      },
    }).addTo(map);
    // Las etiquetas se activan al acercarse para evitar que las localidades pequeñas se superpongan.
    const labels = L.layerGroup();
    props.localities.features.forEach((feature) => {
      const label = document.createElement("span");
      label.textContent = feature.properties.name;
      const marker = L.marker(feature.properties.center, {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          html: label,
          className: "locality-label",
          iconSize: [110, 18],
          iconAnchor: [55, 9],
        }),
      });
      labels.addLayer(marker);
    });
    function updateLabels() {
      if (map.getZoom() >= 12) labels.addTo(map);
      else labels.remove();
    }
    markers.current = L.layerGroup().addTo(map);
    map.on("moveend", loadBlocks);
    map.on("zoomend", updateLabels);
    const observer = new ResizeObserver(() => {
      map.invalidateSize({ pan: false });
      markers.current?.eachLayer((layer) => {
        const popup = (layer as L.Marker).getPopup();
        if (!popup) return;
        popup.options.minWidth = Math.min(220, map.getSize().x - 80);
        popup.options.maxWidth = Math.min(300, map.getSize().x - 80);
        if (popup.isOpen()) popup.update();
      });
    });
    observer.observe(container.current);
    return () => {
      disposed = true;
      generation += 1;
      controllers.forEach((controller) => controller.abort());
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      polygons.current = null;
      markers.current = null;
    };
  }, [props.localities, props.manifest]);

  useEffect(() => {
    const max = Math.max(1, ...Object.values(props.counts));
    polygons.current?.eachLayer((layer) => {
      const polygon = layer as L.Polygon & {
        feature: Localities["features"][number];
      };
      const { code, name } = polygon.feature.properties;
      const count = props.counts[code] ?? 0;
      polygon.setStyle({
        fillColor: fill(count, max),
        color: "#ffffff",
        weight: props.selectedLocality === code ? 3 : 1.8,
        fillOpacity: 0.86,
      });
      const label = document.createElement("div");
      label.textContent = `${name} · ${count} ${count === 1 ? "reporte" : "reportes"}`;
      polygon.unbindTooltip().bindTooltip(label, { sticky: true });
    });
  }, [props.counts, props.selectedLocality, props.localities]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const feature = props.localities.features.find(
      (feature) => feature.properties.code === props.selectedLocality,
    );
    if (feature)
      map.fitBounds(L.geoJSON(feature).getBounds(), {
        padding: [25, 25],
        maxZoom: 14,
      });
    else map.setView(urbanCenter, 11);
  }, [props.selectedLocality, props.localities]);

  useEffect(() => {
    const map = mapRef.current;
    const group = markers.current;
    if (!map || !group) return;
    group.clearLayers();
    const byPosition = new Map<string, LocatedReport[]>();
    for (const report of props.reports.filter(validLocation)) {
      const location = approximateLocation(report.geolocation);
      const key = `${location.latitude},${location.longitude}`;
      byPosition.set(key, [...(byPosition.get(key) ?? []), report]);
    }
    for (const reports of byPosition.values()) {
      const selected = reports.some((report) => report.id === props.selectedId);
      const icon = document.createElement("span");
      icon.className = `report-pin${selected ? " selected" : ""}`;
      icon.textContent = reports.length > 1 ? String(reports.length) : "";
      const first = reports[0];
      const location = approximateLocation(first.geolocation);
      const marker = L.marker(
        [location.latitude, location.longitude],
        {
          title:
            reports.length > 1
              ? `${reports.length} reportes en ${first.localityName}`
              : `Reporte: ${first.title}`,
          icon: L.divIcon({
            html: icon,
            className: "report-marker",
            iconSize: [28, 28],
            iconAnchor: [14, 14],
          }),
          zIndexOffset: selected ? 1000 : 0,
        },
      );
      const popup = document.createElement("div");
      popup.className = "report-popup";
      const locationLabel = document.createElement("p");
      locationLabel.className = "popup-location";
      locationLabel.textContent = `${first.localityName} · Ubicación aproximada: ${coordinates(location)}`;
      popup.append(locationLabel);
      for (const report of reports) {
        const item = document.createElement("div");
        item.className = "popup-report";
        const title = document.createElement("button");
        title.textContent = report.title;
        title.type = "button";
        title.onclick = () => callbacks.current.onSelectReport(report.id);
        const time = document.createElement("p");
        time.textContent = date(report.created_at);
        const hash = document.createElement("code");
        hash.textContent = report.hash;
        item.append(title, time, hash);
        popup.append(item);
      }
      marker
        .bindPopup(popup, {
          minWidth: Math.min(220, map.getSize().x - 80),
          maxWidth: Math.min(300, map.getSize().x - 80),
          maxHeight: 260,
          keepInView: true,
          autoPanPaddingTopLeft: L.point(16, 70),
          autoPanPaddingBottomRight: L.point(16, 20),
        })
        .addTo(group);
      marker.getElement()?.setAttribute("aria-label", marker.options.title!);
      if (reports.length === 1)
        marker.on("click", () => callbacks.current.onSelectReport(first.id));
      if (selected) {
        marker.openPopup();
      }
    }
  }, [props.reports, props.selectedId, props.localities]);

  return (
    <div className="relative isolate">
      <div
        ref={container}
        className="h-[450px] w-full sm:h-[560px]"
        role="region"
        aria-label="Mapa de reportes por localidades de Bogotá"
      />
      <Button
        variant="outline"
        size="sm"
        className="absolute right-3 top-3 z-[500] bg-card shadow-sm"
        onClick={() => {
          if (polygons.current)
            mapRef.current?.fitBounds(polygons.current.getBounds(), {
              padding: [20, 20],
            });
        }}
      >
        <Expand className="size-3.5" />
        Ver todo Bogotá
      </Button>
      <div className="absolute left-3 top-3 z-[500] max-w-[calc(100%-180px)] rounded-lg border bg-card/95 px-3 py-2 text-[11px] text-muted-foreground shadow-sm">
        <span className="flex items-center gap-2">
          {blocksLoading ? (
            <LoaderCircle className="size-3.5 shrink-0 animate-spin" />
          ) : (
            <Layers className="size-3.5 shrink-0" />
          )}
          {blocksLoading ? "Cargando manzanas…" : blocksMessage}
        </span>
        {blocksFailed && (
          <button
            className="mt-1 text-primary underline"
            onClick={() => mapRef.current?.fire("moveend")}
          >
            Reintentar capa
          </button>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-card px-4 py-3 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-2">
          <span className="size-2.5 rounded-full bg-primary ring-2 ring-red-950" />
          Reportes anónimos
        </span>
        <span className="flex items-center gap-2">
          Menos
          <span className="flex gap-0.5">
            {["#3a2027", "#592d38", "#773d49", "#a83f50"].map((color) => (
              <span
                key={color}
                className="h-2.5 w-5 rounded-sm"
                style={{ backgroundColor: color }}
              />
            ))}
          </span>
          Más reportes
        </span>
      </div>
    </div>
  );
}
