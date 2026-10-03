// The PragMattie Sync mark: a navy tile with two teal sync arcs and a gold dot.
// AppLogo draws it inline beside the "Delivery Insights" wordmark; the favicon uses
// the same shapes.
export const logoColors = {
  navy: "#2E3F55",
  teal: "#1B8A94",
  gold: "#E8B35A",
};

export const markArcs = ["M11 22 A9 9 0 0 1 26 13", "M29 18 A9 9 0 0 1 14 27"];

export const markDot = { cx: 29, cy: 11, r: 3 };

export function markSvg() {
  const arcs = markArcs
    .map(
      (d) =>
        `<path d="${d}" fill="none" stroke="${logoColors.teal}" stroke-width="4" ` +
        `stroke-linecap="round"/>`,
    )
    .join("");
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">` +
    `<rect width="40" height="40" rx="8" fill="${logoColors.navy}"/>${arcs}` +
    `<circle cx="${markDot.cx}" cy="${markDot.cy}" r="${markDot.r}" fill="${logoColors.gold}"/>` +
    `</svg>`
  );
}

export function faviconHref() {
  return `data:image/svg+xml,${encodeURIComponent(markSvg())}`;
}

export function setFavicon(doc = document) {
  let link = doc.querySelector("link[rel='icon']");
  if (!link) {
    link = doc.createElement("link");
    link.rel = "icon";
    doc.head.appendChild(link);
  }
  link.type = "image/svg+xml";
  link.href = faviconHref();
}
