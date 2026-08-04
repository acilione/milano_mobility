import type { Feature, FeatureCollection, LineString, Point } from "geojson";
import {
  AttributionControl,
  GeoJSONSource,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  Popup,
  type StyleSpecification,
} from "maplibre-gl";

interface Route {
  route_sk: string;
  route_name: string;
  route_long_name: string | null;
  route_color: string | null;
  route_type: number;
}

interface Stop {
  stop_id: string;
  stop_name: string;
  stop_lat: number;
  stop_lon: number;
  stop_events: number | string;
  routes: Route[];
}

interface RouteShape {
  route_sk: string;
  shape_id: string;
  points: [number, number][];
}

interface RouteShapeResponse {
  routes: RouteShape[];
}

export interface MobilityMap {
  destroy(): void;
  selectStop(index: number): void;
}

const EMPTY_LINES: FeatureCollection<LineString> = { type: "FeatureCollection", features: [] };
const MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    openStreetMap: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [
    {
      id: "base-map",
      type: "raster",
      source: "openStreetMap",
      paint: { "raster-opacity": 0.42, "raster-saturation": -0.55, "raster-brightness-max": 0.55 },
    },
  ],
};

function routeColor(route: Route): string {
  if (/^[0-9a-f]{6}$/i.test(route.route_color ?? "")) return `#${route.route_color}`;
  if (route.route_type === 0) return "#ffc45c";
  if (route.route_type === 1) return "#c8ff63";
  return "#68b5ff";
}

function stopFeatures(stops: Stop[]): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: stops.map((stop, index) => ({
      type: "Feature",
      id: index,
      geometry: { type: "Point", coordinates: [Number(stop.stop_lon), Number(stop.stop_lat)] },
      properties: { index, name: stop.stop_name, calls: Number(stop.stop_events) },
    })),
  };
}

function routeFeatures(routes: Route[], cache: Map<string, RouteShape[]>): FeatureCollection<LineString> {
  const features: Feature<LineString>[] = [];
  for (const route of routes) {
    for (const shape of cache.get(route.route_sk) ?? []) {
      if (shape.points.length < 2) continue;
      features.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: shape.points },
        properties: {
          routeKey: route.route_sk,
          routeName: route.route_name,
          color: routeColor(route),
        },
      });
    }
  }
  return { type: "FeatureCollection", features };
}

export function createMap(
  container: string | HTMLElement,
  stops: Stop[],
  onSelect: (index: number) => void,
  initialStopIndex: number | null = null,
): MobilityMap {
  const map = new MapLibreMap({
    container,
    style: MAP_STYLE,
    center: [9.19, 45.4642],
    zoom: 10,
    attributionControl: false,
    maxZoom: 19,
  });
  const shapeCache = new Map<string, RouteShape[]>();
  const stopPopup = new Popup({
    className: "stop-popup",
    closeButton: false,
    closeOnClick: false,
    offset: 9,
  });
  const resizeObserver = new ResizeObserver(() => map.resize());
  let selectedIndex: number | null = null;
  let selectionToken = 0;
  let shapeRequest: AbortController | null = null;

  const updateRouteSource = (routes: Route[]): void => {
    (map.getSource("selected-routes") as GeoJSONSource | undefined)?.setData(
      routeFeatures(routes, shapeCache),
    );
  };

  const loadRoutes = async (routes: Route[]): Promise<void> => {
    const token = ++selectionToken;
    shapeRequest?.abort();
    shapeRequest = null;
    updateRouteSource(routes);
    const missing = routes.filter((route) => !shapeCache.has(route.route_sk));
    if (missing.length === 0) return;
    const parameters = new URLSearchParams();
    missing.forEach((route) => parameters.append("route_sk", route.route_sk));
    const controller = new AbortController();
    shapeRequest = controller;
    try {
      const response = await fetch(`/api/route-shapes?${parameters}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Route geometry returned HTTP ${response.status}`);
      const payload = (await response.json()) as RouteShapeResponse;
      missing.forEach((route) => shapeCache.set(route.route_sk, []));
      for (const shape of payload.routes) {
        const cached = shapeCache.get(shape.route_sk) ?? [];
        cached.push(shape);
        shapeCache.set(shape.route_sk, cached);
      }
      if (token === selectionToken) updateRouteSource(routes);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        console.warn("Route geometry is unavailable", error);
      }
    }
  };

  const selectStop = (index: number, notify = true): void => {
    const stop = stops[index];
    if (!stop || !map.getSource("stops")) return;
    if (selectedIndex !== null) {
      map.setFeatureState({ source: "stops", id: selectedIndex }, { selected: false });
    }
    selectedIndex = index;
    map.setFeatureState({ source: "stops", id: index }, { selected: true });
    void loadRoutes(stop.routes ?? []);
    if (notify) onSelect(index);
  };

  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new AttributionControl({ compact: true }), "bottom-right");
  resizeObserver.observe(typeof container === "string" ? document.getElementById(container)! : container);

  map.once("load", () => {
    map.addSource("selected-routes", { type: "geojson", data: EMPTY_LINES });
    map.addLayer({
      id: "selected-routes",
      type: "line",
      source: "selected-routes",
      paint: {
        "line-color": ["get", "color"],
        "line-opacity": 0.92,
        "line-width": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 16, 5],
      },
      layout: { "line-cap": "round", "line-join": "round" },
    });
    map.addSource("stops", {
      type: "geojson",
      data: stopFeatures(stops),
      cluster: true,
      clusterMaxZoom: 14,
      clusterRadius: 22,
    });
    map.addLayer({
      id: "stop-clusters",
      type: "circle",
      source: "stops",
      filter: ["has", "point_count"],
      paint: {
        "circle-color": "#123c34",
        "circle-opacity": 0.86,
        "circle-radius": ["step", ["get", "point_count"], 12, 25, 16, 100, 21],
        "circle-stroke-color": "#48e3b5",
        "circle-stroke-width": 1,
      },
    });
    map.addLayer({
      id: "stop-cluster-count",
      type: "symbol",
      source: "stops",
      filter: ["has", "point_count"],
      layout: {
        "text-field": ["get", "point_count_abbreviated"],
        "text-size": 11,
        "text-allow-overlap": true,
      },
      paint: { "text-color": "#e8f4ef" },
    });
    map.addLayer({
      id: "stops",
      type: "circle",
      source: "stops",
      filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-color": ["case", ["boolean", ["feature-state", "selected"], false], "#c8ff63", "#48e3b5"],
        "circle-opacity": 0.9,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 17, 4],
        "circle-stroke-color": "#07110f",
        "circle-stroke-width": 0.75,
      },
      layout: { "circle-sort-key": ["get", "calls"] },
    });

    const bounds = new LngLatBounds();
    stops.forEach((stop) => bounds.extend([Number(stop.stop_lon), Number(stop.stop_lat)]));
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 36, duration: 0, maxZoom: 13 });

    map.on("click", "stop-clusters", async (event) => {
      const feature = event.features?.[0];
      if (!feature || feature.geometry.type !== "Point") return;
      const source = map.getSource("stops") as GeoJSONSource;
      const zoom = await source.getClusterExpansionZoom(Number(feature.properties?.cluster_id));
      map.easeTo({ center: feature.geometry.coordinates as [number, number], zoom });
    });
    map.on("click", "stops", (event) => {
      const index = Number(event.features?.[0]?.properties?.index);
      if (Number.isInteger(index)) selectStop(index);
    });
    map.on("mouseenter", "stops", (event) => {
      const index = Number(event.features?.[0]?.properties?.index);
      const stop = stops[index];
      if (!stop) return;
      const content = document.createElement("div");
      const name = document.createElement("strong");
      const calls = document.createElement("small");
      name.textContent = stop.stop_name;
      calls.textContent = `${Number(stop.stop_events).toLocaleString()} scheduled calls`;
      content.append(name, calls);
      stopPopup
        .setLngLat([Number(stop.stop_lon), Number(stop.stop_lat)])
        .setDOMContent(content)
        .addTo(map);
    });
    map.on("mouseleave", "stops", () => stopPopup.remove());
    for (const layer of ["stop-clusters", "stops"]) {
      map.on("mouseenter", layer, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layer, () => {
        map.getCanvas().style.cursor = "";
      });
    }
    if (initialStopIndex !== null) selectStop(initialStopIndex, false);
  });

  return {
    destroy(): void {
      shapeRequest?.abort();
      stopPopup.remove();
      resizeObserver.disconnect();
      map.remove();
    },
    selectStop(index: number): void {
      selectStop(index);
    },
  };
}
