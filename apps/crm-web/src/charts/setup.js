// Shared Chart.js setup: registers only the parts the CRM's charts use, and
// holds the look every chart shares. Legends, where needed, are plain HTML.
import { BarElement, CategoryScale, Chart, LinearScale, Tooltip } from "chart.js";
import { money, moneyFull } from "../format";

Chart.register(BarElement, CategoryScale, LinearScale, Tooltip);

export const LABEL_COLOR = "#5B6573";
export const GRID_COLOR = "#E6E8EB";

Chart.defaults.font.family = "Roboto, sans-serif";
Chart.defaults.color = LABEL_COLOR;
Chart.defaults.borderColor = GRID_COLOR;

// A dark tooltip that shows full dollars.
export const moneyTooltip = {
  backgroundColor: "#1F2933",
  titleColor: "#FFFFFF",
  bodyColor: "#FFFFFF",
  padding: 10,
  cornerRadius: 4,
  callbacks: {
    label(context) {
      const value = context.parsed[context.chart.options.indexAxis === "y" ? "x" : "y"];
      const name = context.dataset.label;
      return name ? `${name}: ${moneyFull(value)}` : moneyFull(value);
    },
  },
};

// A value axis whose ticks are compact dollars.
export function moneyAxis(extra = {}) {
  return {
    beginAtZero: true,
    grid: { color: GRID_COLOR },
    border: { display: false },
    ticks: { color: LABEL_COLOR, callback: (value) => money(value) },
    ...extra,
  };
}

// Writes each horizontal bar's value just past its end.
export const barValueLabels = {
  id: "barValueLabels",
  afterDatasetsDraw(chart) {
    if (chart.options.indexAxis !== "y") return;
    const { ctx } = chart;
    ctx.save();
    ctx.font = `500 12px ${Chart.defaults.font.family}`;
    ctx.fillStyle = LABEL_COLOR;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      chart.getDatasetMeta(datasetIndex).data.forEach((bar, index) => {
        ctx.fillText(money(dataset.data[index]), bar.x + 6, bar.y);
      });
    });
    ctx.restore();
  },
};
