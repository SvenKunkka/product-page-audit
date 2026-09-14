() => {
  const styleOf = element => window.getComputedStyle(element);
  const isVisible = element => {
    const box = element.getBoundingClientRect();
    if (!element.getClientRects().length || box.width <= 0 || box.height <= 0) return false;
    for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
      const style = styleOf(ancestor);
      if (style.display === "none" || ["hidden", "collapse"].includes(style.visibility) || Number(style.opacity) === 0) return false;
      if (ancestor !== element && ancestor !== document.body && ancestor !== document.documentElement) {
        const clip = ancestor.getBoundingClientRect();
        if (["hidden", "clip", "scroll", "auto"].includes(style.overflowX) && (box.right <= clip.left || box.left >= clip.right)) return false;
        if (["hidden", "clip", "scroll", "auto"].includes(style.overflowY) && (box.bottom <= clip.top || box.top >= clip.bottom)) return false;
      }
    }
    return true;
  };
  const label = element => `${element.tagName.toLowerCase()}${element.id ? "#" + element.id : ""}`;
  const images = [...document.images].map(image => ({
    src: image.currentSrc || image.src,
    alt: image.getAttribute("alt"),
    visible: isVisible(image),
    loaded: image.complete && image.naturalWidth > 0,
    natural_width: image.naturalWidth,
    natural_height: image.naturalHeight,
  }));
  const specWords = /specifications?|technical\s+(?:data|details|specs)|tech\s*specs|规格|参数/i;
  const specCandidates = new Set([...document.querySelectorAll("[id], [class], table")].filter(element =>
    /specification|tech-spec|technical-spec|product-spec|specs/i.test(`${element.id} ${typeof element.className === "string" ? element.className : ""}`) ||
    (element.tagName === "TABLE" && specWords.test(element.innerText || element.textContent || ""))
  ));
  for (const heading of document.querySelectorAll("h1,h2,h3,h4,h5,h6,summary,caption")) {
    if (specWords.test(heading.textContent || "")) {
      // A visible section heading does not prove its following table is visible.
      const section = heading.closest("section,details") || heading.parentElement;
      const tables = section ? [...section.querySelectorAll("table,dl")] : [];
      if (tables.length) tables.forEach(table => specCandidates.add(table));
      else if (heading.nextElementSibling) specCandidates.add(heading.nextElementSibling);
      else specCandidates.add(heading);
    }
  }
  const specContent = new Set();
  for (const candidate of specCandidates) {
    const tables = [...candidate.querySelectorAll("table,dl")];
    if (tables.length) tables.forEach(table => specContent.add(table));
    else specContent.add(candidate);
  }
  const specs = [...specContent].map(element => ({element: label(element), visible: isVisible(element)}));
  const fonts = new Map();
  for (const element of document.querySelectorAll("p,li,td,th,h1,h2,h3,button")) {
    if (!isVisible(element) || !(element.textContent || "").trim()) continue;
    const style = styleOf(element);
    const key = [style.fontFamily, style.fontSize, style.fontWeight, style.lineHeight].join("|");
    if (fonts.has(key)) fonts.get(key).count += 1;
    else fonts.set(key, {font: style.fontFamily, size: style.fontSize, weight: style.fontWeight, line_height: style.lineHeight, count: 1});
  }
  const documentWidth = document.scrollingElement?.scrollWidth || document.documentElement.scrollWidth;
  // Mobile Chromium can expand innerWidth to fit overflowing content. The
  // layout viewport's clientWidth still reveals the intended device width.
  const viewportWidth = document.documentElement.clientWidth || innerWidth;
  const overflowElements = documentWidth > viewportWidth + 2 ? [...document.body.querySelectorAll("*")]
    .filter(element => {
      if (!isVisible(element)) return false;
      const box = element.getBoundingClientRect();
      return box.right > viewportWidth + 2 && box.left < documentWidth;
    }).slice(0, 12).map(label) : [];
  return {
    extraction_complete: true,
    title: document.title,
    horizontal_overflow: documentWidth > viewportWidth + 2 && overflowElements.length > 0,
    viewport_width: viewportWidth,
    document_width: documentWidth,
    overflow_elements: overflowElements,
    images,
    specs_present: specs.length > 0,
    specs_visible: specs.some(spec => spec.visible),
    spec_sections: specs,
    typography: [...fonts.values()].sort((a, b) => b.count - a.count).slice(0, 25),
    viewport_height: innerHeight,
    document_height: document.documentElement.scrollHeight,
    visible_text: document.body.innerText.slice(0, 150000),
  };
}
