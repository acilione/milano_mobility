import type { Feature, FeatureCollection, Polygon } from "geojson";
import { Map as MapLibreMap, Marker, NavigationControl, GeoJSONSource } from "maplibre-gl";
import { MAP_STYLE } from "./map";

interface Place { stop_id: string; stop_name: string; stop_lat: number; stop_lon: number }
interface Leg { mode: string; route?: string; from: string; to: string; departure: number; arrival: number }
interface Reachable extends Place { minutes: number; departure: number; legs: Leg[] }
interface Result {
  stops: Reachable[]; date: string; deadline: number; minutes: number; walk: number;
  destination: [number, number]; snapshot_date: string; connections: number;
}
interface Saved { point: [number, number]; name: string; time: string; minutes: string; walk: string }
const colors = ["#c8ff63", "#48e3b5", "#68b5ff", "#bf9bff"];
const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
const escape = (value: unknown): string => String(value ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const time = (seconds: number): string => {
  const day = Math.floor(seconds / 86400);
  const value = ((seconds % 86400) + 86400) % 86400;
  return `${String(Math.floor(value / 3600)).padStart(2, "0")}:${String(Math.floor(value % 3600 / 60)).padStart(2, "0")}${day < 0 ? " (previous day)" : ""}`;
};

function walkingAreas(result: Result): Feature<Polygon>[] {
  // A single time band per 100 m cell avoids thousands of overlapping opaque circles.
  // Include a cell only if its furthest corner fits the estimated walking radius.
  const size = 100, latitudeScale = 111_320;
  const longitudeScale = latitudeScale * Math.cos(result.destination[1] * Math.PI / 180);
  const rows = new Map<number, Map<number, number>>();
  const add = (point: [number, number], seconds: number, band: number): void => {
    const radius = Math.max(0, seconds) * 1.25 / 1.3;
    const x = (point[0] - result.destination[0]) * longitudeScale;
    const y = (point[1] - result.destination[1]) * latitudeScale;
    for (let iy = Math.floor((y-radius)/size); iy <= Math.floor((y+radius)/size); iy++) {
      for (let ix = Math.floor((x-radius)/size); ix <= Math.floor((x+radius)/size); ix++) {
        const dx = Math.abs((ix + .5) * size - x) + size / 2;
        const dy = Math.abs((iy + .5) * size - y) + size / 2;
        if (dx*dx + dy*dy > radius*radius) continue;
        const row = rows.get(iy) ?? new Map<number, number>();
        row.set(ix, Math.min(band, row.get(ix) ?? band)); rows.set(iy, row);
      }
    }
  };
  for (const band of [15, 30, 45, 60].filter(n => n <= result.minutes)) {
    add(result.destination, Math.min(band, result.walk) * 60, band);
    for (const stop of result.stops) {
      if (!stop.legs.some(leg => leg.mode === "transit")) continue;
      const remaining = band * 60 - (result.deadline - stop.departure);
      if (remaining > 0) add([Number(stop.stop_lon), Number(stop.stop_lat)], Math.min(remaining, result.walk * 60), band);
    }
  }
  const features: Feature<Polygon>[] = [];
  for (const [y, row] of rows) {
    const cells = [...row.keys()].sort((a,b) => a-b);
    for (let i = 0; i < cells.length; i++) {
      const start = cells[i]!, band = row.get(start)!;
      let end = start;
      while (cells[i+1] === end+1 && row.get(end+1) === band) { i++; end++; }
      const west = result.destination[0] + start*size/longitudeScale;
      const east = result.destination[0] + (end+1)*size/longitudeScale;
      const south = result.destination[1] + y*size/latitudeScale;
      const north = result.destination[1] + (y+1)*size/latitudeScale;
      features.push({ type:"Feature", properties:{minutes:band}, geometry:{type:"Polygon",
        coordinates:[[[west,south],[east,south],[east,north],[west,north],[west,south]]] } });
    }
  }
  return features;
}

export function createCommuteMap(
  root: HTMLElement, stops: Place[], serviceDays: { service_date: string }[],
): { destroy(): void } {
  let saved: Saved | null = null;
  try {
    const value = JSON.parse(localStorage.getItem("milano-commute") ?? "null") as Saved | null;
    if (value && Array.isArray(value.point) && value.point.length === 2 &&
      value.point.every(Number.isFinite) && value.point[0] >= 8.3 && value.point[0] <= 10.2 &&
      value.point[1] >= 44.8 && value.point[1] <= 46.2) saved = value;
  } catch { /* Storage is optional. */ }
  let point: [number, number] = saved?.point ?? [9.19, 45.4642];
  let placeName = saved?.name ?? "Duomo · example destination";
  let request: AbortController | null = null;
  let result: Result | null = null;
  let loaded = false;
  let generation = 0;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Rome", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const dates = [...new Set(serviceDays.map(d => d.service_date))].sort();
  const selectedDate = dates.includes(today) ? today : dates.find(d => d > today) ?? dates.at(-1) ?? "";
  root.innerHTML = `
    <div class="commute-heading"><div><div class="eyebrow">Your everyday journey</div><h2>Where could you live?</h2>
      <p>Choose where you work or study. Explore the places you could commute from.</p></div><span class="tag">Arrive by · Milan time</span></div>
    <div class="commute-layout"><form class="commute-controls">
      <label for="commute-search">Work or study destination</label>
      <input id="commute-search" type="search" list="commute-places" placeholder="Find a stop or station" autocomplete="off">
      <datalist id="commute-places">${stops.map(s => `<option value="${escape(s.stop_name)} [${escape(s.stop_id)}]"></option>`).join("")}</datalist>
      <p class="field-help">Select a stop, or click the map to place your destination precisely.</p>
      <div class="destination-card"><span class="destination-dot"></span><div><strong id="commute-destination"></strong><small id="commute-coordinates"></small></div></div>
      <div class="commute-fields"><div><label for="commute-date">Travel date</label><select id="commute-date" required>${dates.map(d => `<option value="${d}" ${d === selectedDate ? "selected" : ""}>${new Date(`${d}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" })}</option>`).join("")}</select></div>
      <div><label for="commute-time">Arrive by</label><input id="commute-time" type="time" value="09:00" required></div></div>
      <label for="commute-budget">Maximum commute</label><select id="commute-budget"><option value="15">15 minutes</option><option value="30" selected>30 minutes</option><option value="45">45 minutes</option><option value="60">60 minutes</option></select>
      <label for="commute-walk">Maximum walk per leg</label><select id="commute-walk"><option value="5">5 minutes</option><option value="10" selected>10 minutes</option><option value="15">15 minutes</option></select>
      <p class="field-help">Includes walking, waiting and up to 2 transfers, with 2 minutes allowed for each transfer.</p>
      <button class="commute-submit" type="submit">Find my commute area <span aria-hidden="true">↗</span></button>
      <button class="commute-save" type="button">Save these preferences</button>
      <p id="commute-status" role="status" aria-live="polite">Preparing the map…</p>
    </form><div class="commute-map-wrap"><div id="commute-map" role="region" aria-label="Commute areas: click to choose a destination"></div>
      <div class="commute-legend" aria-label="Estimated commute time">${[15,30,45,60].map((n,i) => `<span data-band="${n}"><i style="background:${colors[i]}"></i>≤ ${n} min</span>`).join("")}</div>
      <div class="commute-map-note">Click an area to move your destination · click a stop to inspect a journey</div></div></div>
    <div class="commute-assumptions"><strong>Scheduled travel, approximate walking.</strong> Shaded areas estimate walks at 4.5 km/h with a 30% distance allowance, displayed in 100 m cells. Streets, barriers, station entrances and accessibility are not modeled. Live delays and cancellations are not included. Walking-only areas use the same per-leg limit.</div>
    <div class="commute-results"><div><h3 id="commute-count">Explore your commute</h3><p id="commute-summary">Select a destination and calculate an area.</p><div id="commute-stop-list" class="commute-stop-list"></div></div>
      <aside id="commute-journey" aria-live="polite"><h3>A closer look</h3><p>Select a reachable stop to see its scheduled journey to your destination. The shaded area around it adds a walk to that stop.</p></aside></div>`;
  const get = <T extends HTMLElement>(selector: string): T => root.querySelector<T>(selector)!;
  const status = get<HTMLElement>("#commute-status");
  const date = get<HTMLSelectElement>("#commute-date");
  const clock = get<HTMLInputElement>("#commute-time");
  const budget = get<HTMLSelectElement>("#commute-budget");
  const walk = get<HTMLSelectElement>("#commute-walk");
  const search = get<HTMLInputElement>("#commute-search");
  const submit = get<HTMLButtonElement>(".commute-submit");
  if (saved) {
    if (/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(saved.time)) clock.value = saved.time;
    if (["15", "30", "45", "60"].includes(saved.minutes)) budget.value = saved.minutes;
    if (["5", "10", "15"].includes(saved.walk)) walk.value = saved.walk;
  }
  const map = new MapLibreMap({ container: get("#commute-map"), style: MAP_STYLE,
    center: point, zoom: 11.5, maxZoom: 18 });
  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  const marker = new Marker({ color: "#ff7a66" }).setLngLat(point).addTo(map);
  const observer = new ResizeObserver(() => map.resize());
  observer.observe(get("#commute-map"));
  const showDestination = (): void => {
    get("#commute-destination").textContent = placeName;
    get("#commute-coordinates").textContent = `${point[1].toFixed(4)}, ${point[0].toFixed(4)}`;
    marker.setLngLat(point);
  };
  showDestination();
  const source = (name: string): GeoJSONSource | undefined => map.getSource(name) as GeoJSONSource | undefined;
  const clear = (): void => {
    generation++;
    request?.abort(); result = null;
    source("commute-areas")?.setData(empty); source("commute-stops")?.setData(empty);
    get("#commute-stop-list").replaceChildren();
    get("#commute-count").textContent = "Ready to explore";
    get("#commute-summary").textContent = "Calculate again to see this selection.";
    get("#commute-journey").innerHTML = "<h3>A closer look</h3><p>Select a reachable stop after calculating your commute.</p>";
    status.textContent = "Preferences changed. Calculate to update the map.";
    submit.disabled = false;
  };
  const choose = (coordinates: [number, number], name: string): void => {
    clear(); point = coordinates; placeName = name; showDestination();
  };
  const inspect = (index: number): void => {
    const stop = result?.stops[index];
    if (!stop || !result) return;
    map.easeTo({ center: [Number(stop.stop_lon), Number(stop.stop_lat)], zoom: Math.max(13, map.getZoom()) });
    get("#commute-journey").innerHTML = `<h3>${escape(stop.stop_name)}</h3><p>${stop.minutes} min including arrival margin · leave by ${time(stop.departure)}</p>
      <ol class="journey-legs">${stop.legs.map(leg => `<li><span>${time(leg.departure)} – ${time(leg.arrival)}</span><strong>${leg.mode === "walk" ? "Walk" : `Take ${escape(leg.route)}`}</strong><p>${escape(leg.from)} → ${escape(leg.to)}</p></li>`).join("")}</ol><p>Arrive by ${time(result.deadline)} on ${escape(result.date)}. Transfer gaps include walking, waiting and the transfer allowance.</p>`;
  };
  const calculate = async (): Promise<void> => {
    if (!loaded || !date.value) return;
    clear();
    const token = generation;
    request = new AbortController(); submit.disabled = true;
    status.textContent = "Finding scheduled journeys and walking connections…";
    const query = new URLSearchParams({ lat: String(point[1]), lon: String(point[0]), date: date.value,
      time: clock.value, minutes: budget.value, walk: walk.value });
    try {
      const response = await fetch(`/api/commute?${query}`, { signal: request.signal });
      const payload = await response.json() as Result & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "Could not calculate this commute.");
      if (token !== generation) return;
      result = payload;
      result.stops.sort((a, b) => a.minutes - b.minutes || a.stop_name.localeCompare(b.stop_name));
      const areas = walkingAreas(result);
      source("commute-areas")?.setData({ type: "FeatureCollection", features: areas });
      source("commute-stops")?.setData({ type: "FeatureCollection", features: result.stops.map((s, index) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [Number(s.stop_lon), Number(s.stop_lat)] },
        properties: { index, minutes: s.minutes },
      })) });
      root.querySelectorAll<HTMLElement>("[data-band]").forEach(el => { el.hidden = Number(el.dataset.band) > payload.minutes; });
      get("#commute-count").textContent = `${result.stops.length.toLocaleString()} reachable stops`;
      get("#commute-summary").textContent = `Within ${payload.minutes} minutes of ${placeName} · arrive by ${clock.value}, ${payload.date}. Timetable published ${payload.snapshot_date}.`;
      get("#commute-stop-list").innerHTML = result.stops.map((s, i) => `<button type="button" data-stop="${i}"><span>${escape(s.stop_name)}</span><strong>${s.minutes} min <span aria-hidden="true">↗</span></strong></button>`).join("");
      status.textContent = `${payload.date !== today ? `Showing ${payload.date}. ` : ""}${result.stops.some(s => s.legs.some(l => l.mode === "transit")) ? "Your commute area is ready." : "No transit journey reaches this destination in the selected window. Showing walking access."}`;
    } catch (error) {
      if (token === generation && !(error instanceof DOMException && error.name === "AbortError")) {
        status.textContent = error instanceof Error ? error.message : "Could not calculate. Please retry.";
      }
    } finally { if (token === generation) submit.disabled = false; }
  };
  root.querySelector("form")!.addEventListener("submit", event => { event.preventDefault(); void calculate(); });
  for (const input of [date, clock, budget, walk]) input.addEventListener("change", clear);
  search.addEventListener("change", () => {
    const stop = stops.find(s => `${s.stop_name} [${s.stop_id}]` === search.value);
    if (stop) {
      choose([Number(stop.stop_lon), Number(stop.stop_lat)], stop.stop_name);
      map.easeTo({ center: point, zoom: 13 });
    } else if (search.value) { clear(); status.textContent = "Choose a stop from the suggestions, or click your destination on the map."; }
  });
  get("#commute-stop-list").addEventListener("click", event => {
    const button = (event.target as HTMLElement).closest<HTMLElement>("[data-stop]");
    if (button) inspect(Number(button.dataset.stop));
  });
  get(".commute-save").addEventListener("click", () => {
    try {
      localStorage.setItem("milano-commute", JSON.stringify({ point, name: placeName, time: clock.value, minutes: budget.value, walk: walk.value }));
      status.textContent = "Destination and preferences saved in this browser.";
    } catch { status.textContent = "Your browser could not save preferences. You can still use the map."; }
  });
  map.once("load", () => {
    loaded = true;
    map.addSource("commute-areas", { type: "geojson", data: empty });
    map.addLayer({ id: "commute-areas", type: "fill", source: "commute-areas", paint: {
      "fill-color": ["match", ["get", "minutes"], 15, colors[0]!, 30, colors[1]!, 45, colors[2]!, colors[3]!], "fill-opacity": 0.34, "fill-antialias": false,
    } });
    map.addSource("commute-stops", { type: "geojson", data: empty });
    map.addLayer({ id: "commute-stops", type: "circle", source: "commute-stops", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.5, 13, 2, 16, 5],
      "circle-color": ["step", ["get", "minutes"], colors[0]!, 16, colors[1]!, 31, colors[2]!, 46, colors[3]!],
      "circle-stroke-color": "#07110f", "circle-stroke-width": 1,
    } });
    map.on("click", event => {
      const feature = map.queryRenderedFeatures(event.point, { layers: ["commute-stops"] })[0];
      if (feature) inspect(Number(feature.properties.index));
      else { search.value = ""; choose([event.lngLat.lng, event.lngLat.lat], "Pinned destination"); }
    });
    map.on("mouseenter", "commute-stops", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "commute-stops", () => { map.getCanvas().style.cursor = ""; });
    void calculate();
  });
  if (!dates.length) { status.textContent = "No service dates are available in this timetable."; submit.disabled = true; }
  return { destroy(): void { generation++; request?.abort(); observer.disconnect(); marker.remove(); map.remove(); } };
}
