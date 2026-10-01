// @vitest-environment node
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("../..", import.meta.url));

// Whole words only: minified framework code has identifiers like "onVnodeMounted".
const forbidden = /\b(demo|simulated|fictional|PragMattie Growth Partners)\b/gi;

describe("built app", () => {
  it("carries no demo, simulated or fictional wording", async () => {
    const result = await build({ root, logLevel: "silent", build: { write: false } });
    const outputs = [result].flat().flatMap((bundle) => bundle.output);
    const texts = outputs
      .filter((file) => /\.(html|js)$/.test(file.fileName))
      .map((file) => (file.type === "chunk" ? file.code : String(file.source)));

    expect(texts.length).toBeGreaterThan(1);
    expect(texts.join("\n").match(forbidden)).toBeNull();
  }, 60_000);
});
