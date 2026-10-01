import { Chart, Legend } from "chart.js";
import { describe, expect, it, vi } from "vitest";
import {
  barValueLabels,
  GRID_COLOR,
  LABEL_COLOR,
  moneyAxis,
  moneyTooltip,
} from "../charts/setup";

function fakeChart({ indexAxis, data }) {
  const ctx = {
    save: vi.fn(),
    restore: vi.fn(),
    fillText: vi.fn(),
  };
  return {
    ctx,
    options: { indexAxis },
    data: { datasets: [{ data }] },
    getDatasetMeta: () => ({ data: data.map((_, i) => ({ x: 100 * (i + 1), y: 20 * i })) }),
  };
}

describe("chart setup", () => {
  it("uses Roboto with grey labels and light grid lines", () => {
    expect(Chart.defaults.font.family).toContain("Roboto");
    expect(Chart.defaults.color).toBe("#5B6573");
    expect(LABEL_COLOR).toBe("#5B6573");
    expect(Chart.defaults.borderColor).toBe("#E6E8EB");
    expect(GRID_COLOR).toBe("#E6E8EB");
  });

  it("registers only what the charts use, with no legend plugin", () => {
    expect(Chart.registry.getElement("bar")).toBeTruthy();
    expect(Chart.registry.getScale("category")).toBeTruthy();
    expect(Chart.registry.getScale("linear")).toBeTruthy();
    expect(Chart.registry.plugins.get("tooltip")).toBeTruthy();
    expect(Chart.registry.plugins.get(Legend.id)).toBeUndefined();
    expect(() => Chart.registry.getElement("line")).toThrow();
  });

  it("has a money axis whose ticks are compact dollars", () => {
    const axis = moneyAxis({ stacked: true });

    expect(axis.ticks.callback(1250000)).toBe("$1.3M");
    expect(axis.ticks.callback(0)).toBe("$0");
    expect(axis.beginAtZero).toBe(true);
    expect(axis.stacked).toBe(true);
  });

  it("has a dark tooltip that shows full dollars on either axis", () => {
    const label = moneyTooltip.callbacks.label;

    expect(moneyTooltip.backgroundColor).toBe("#1F2933");
    expect(
      label({
        parsed: { x: 0, y: 1234500 },
        chart: { options: {} },
        dataset: { label: "Closed won" },
      }),
    ).toBe("Closed won: $1,234,500");
    expect(
      label({ parsed: { x: 900000, y: 0 }, chart: { options: { indexAxis: "y" } }, dataset: {} }),
    ).toBe("$900,000");
  });

  it("writes each horizontal bar's value just past its end", () => {
    const chart = fakeChart({ indexAxis: "y", data: [900000, 12500] });

    barValueLabels.afterDatasetsDraw(chart);

    expect(chart.ctx.fillText.mock.calls).toEqual([
      ["$900K", 106, 0],
      ["$12.5K", 206, 20],
    ]);
    expect(chart.ctx.textAlign).toBe("left");
  });

  it("leaves vertical bars alone", () => {
    const chart = fakeChart({ indexAxis: "x", data: [900000] });

    barValueLabels.afterDatasetsDraw(chart);

    expect(chart.ctx.fillText).not.toHaveBeenCalled();
  });
});
