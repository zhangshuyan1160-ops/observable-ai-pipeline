// The animation-v1 graph is fixed, but its cards stretch with the CSS grid.
// Route in actual CSS pixels so arrows stay aligned and retain their shape.
export function setupPipelineEdges(host) {
  let pipeline = null;
  const observer = new ResizeObserver(draw);

  function draw() {
    if (!pipeline?.isConnected) return;
    const bounds = pipeline.getBoundingClientRect();
    const nodes = Object.fromEntries(
      [...pipeline.querySelectorAll("[data-step]")].map((node) => {
        const rect = node.getBoundingClientRect();
        return [
          node.dataset.step,
          {
            left: rect.left - bounds.left,
            right: rect.right - bounds.left,
            y: rect.top - bounds.top + rect.height / 2,
          },
        ];
      }),
    );
    const {
      probe,
      extract,
      stylize,
      thumbnail,
      compose,
      package: delivery,
    } = nodes;
    if (!probe || !extract || !stylize || !thumbnail || !compose || !delivery)
      return;

    const clearance = 4;
    const splitX = (extract.right + stylize.left) / 2;
    const mergeX = (compose.right + delivery.left) / 2;
    const start = (node) => `M${node.right + clearance} ${node.y}`;
    const enter = (node) => `H${node.left - clearance}`;
    // Only destination entries get arrows. The two delivery branches join
    // before a single right-facing arrow, never on the vertical merge line.
    const routes = [
      ["probe-extract", `${start(probe)} ${enter(extract)}`, true],
      ["extract-split", `${start(extract)} H${splitX}`, false],
      [
        "split-stylize",
        `M${splitX} ${extract.y} V${stylize.y} ${enter(stylize)}`,
        true,
      ],
      [
        "split-thumbnail",
        `M${splitX} ${extract.y} V${thumbnail.y} ${enter(thumbnail)}`,
        true,
      ],
      ["stylize-compose", `${start(stylize)} ${enter(compose)}`, true],
      ["compose-merge", `${start(compose)} H${mergeX} V${delivery.y}`, false],
      [
        "thumbnail-merge",
        `${start(thumbnail)} H${mergeX} V${delivery.y}`,
        false,
      ],
      ["merge-package", `M${mergeX} ${delivery.y} ${enter(delivery)}`, true],
    ];
    pipeline.querySelector(".edge-routes").replaceChildren(
      ...routes.map(([key, d, arrow]) => {
        const path = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "path",
        );
        path.dataset.route = key;
        path.setAttribute("d", d);
        if (arrow) path.setAttribute("marker-end", "url(#pipeline-arrow)");
        return path;
      }),
    );
  }

  return function sync() {
    const next = host.querySelector(".pipeline");
    if (next === pipeline) return;
    observer.disconnect();
    pipeline = next;
    if (pipeline) {
      draw();
      observer.observe(pipeline);
    }
  };
}
