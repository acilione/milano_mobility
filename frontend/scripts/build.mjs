import { mkdir, copyFile } from "node:fs/promises";
import { build } from "esbuild";

await mkdir("dist", { recursive: true });
await build({
  entryPoints: ["src/map.ts"],
  bundle: true,
  minify: true,
  format: "esm",
  target: "es2022",
  outfile: "dist/map.js",
  plugins: [
    {
      name: "external-maplibre",
      setup(context) {
        context.onResolve({ filter: /^maplibre-gl$/ }, () => ({
          path: "./maplibre-gl.mjs",
          external: true,
        }));
      },
    },
  ],
});
await copyFile("node_modules/maplibre-gl/dist/maplibre-gl.css", "dist/maplibre-gl.css");
for (const filename of [
  "maplibre-gl.mjs",
  "maplibre-gl-shared.mjs",
  "maplibre-gl-worker.mjs",
]) {
  await copyFile(`node_modules/maplibre-gl/dist/${filename}`, `dist/${filename}`);
}
