"use strict";
async function getJSON(path) {
  const res = await fetch(path);
  return res.json();
}
async function refreshOverview() {
  try {
    const o = await getJSON("/api/overview");
    document.getElementById("view").textContent =
      `jarvis ${o.version || ""} — ${o.dataDir || ""}`;
  } catch (e) {
    document.getElementById("view").textContent = `error: ${e}`;
  }
}
refreshOverview();
