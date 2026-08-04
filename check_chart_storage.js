// Paste into the Deriv console WITH YOUR LINES DRAWN and visible on the chart.
(async () => {
  console.log("=== 1. the small localStorage keys, in full ===");
  ["chart-layout-trade", "contract_trade.chart_style", "current_chart_lang"]
    .forEach(k => console.log(k, "=", localStorage.getItem(k)));

  console.log("=== 2. EVERY localStorage key (drawings may be named oddly) ===");
  Object.keys(localStorage).forEach(k =>
    console.log(k, "| len:", (localStorage.getItem(k) || "").length));

  console.log("=== 3. sessionStorage ===");
  Object.keys(sessionStorage).forEach(k =>
    console.log(k, "| len:", (sessionStorage.getItem(k) || "").length));

  console.log("=== 4. IndexedDB ===");
  const dbs = (await indexedDB.databases?.()) || [];
  console.log("databases:", dbs.map(d => d.name));
  for (const {name} of dbs) {
    await new Promise(res => {
      const req = indexedDB.open(name);
      req.onsuccess = e => {
        const db = e.target.result;
        console.log(name, "stores:", [...db.objectStoreNames]);
        for (const s of db.objectStoreNames) {
          try {
            db.transaction(s, "readonly").objectStore(s).getAll().onsuccess = ev => {
              const txt = JSON.stringify(ev.target.result).slice(0, 500);
              if (/horizontal|drawing|103\.|104\.|price/i.test(txt))
                console.log("  HIT in", name + "/" + s, txt);
            };
          } catch (err) {}
        }
        setTimeout(() => { db.close(); res(); }, 400);
      };
      req.onerror = () => res();
    });
  }

  console.log("=== 5. anything holding your level numbers in memory ===");
  // Your visible levels are ~103.x - search the page state for them.
  const hunt = (o, path = "win", depth = 0, seen = new Set()) => {
    if (depth > 4 || o === null || typeof o !== "object" || seen.has(o)) return;
    seen.add(o);
    for (const k of Object.keys(o)) {
      let v; try { v = o[k]; } catch { continue; }
      if (typeof v === "number" && v > 100 && v < 110 &&
          /price|level|value|y$/i.test(k))
        console.log("  candidate:", path + "." + k, "=", v);
      if (typeof v === "object") hunt(v, path + "." + k, depth + 1, seen);
    }
  };
  try { hunt(window); } catch (e) { console.log("scan stopped:", e.message); }
  console.log("=== done ===");
})();
