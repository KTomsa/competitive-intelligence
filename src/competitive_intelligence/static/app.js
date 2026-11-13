(function () {
  "use strict";

  const form = document.getElementById("audit-form");
  const statusEl = document.getElementById("status");
  const reportEl = document.getElementById("report");
  const buttons = form.querySelectorAll("button");

  const STATUS_TEXT = {
    live: "nalezeno na živé stránce",
    changed: "na živé stránce už není",
    snapshot: "z uloženého snapshotu",
    prepared_only: "jen z přípravy",
  };
  const ORIGIN_TEXT = { live: "živě", snapshot: "snapshot" };
  const MODE_TEXT = {
    live: "Živý běh: všechny zdroje byly staženy teď.",
    mixed: "Smíšený běh: část zdrojů pochází z uloženého snapshotu.",
    snapshot: "Běh z uložených dat: nejde o živé stažení.",
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString("cs-CZ", { dateStyle: "medium", timeStyle: "short" });
  }

  function link(url, text) {
    const a = el("a", null, text || url);
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    return a;
  }

  function setBusy(busy, message) {
    buttons.forEach((b) => (b.disabled = busy));
    statusEl.textContent = message || "";
    statusEl.classList.toggle("busy", busy);
  }

  function selected() {
    return Array.from(form.querySelectorAll("input[name=supplier]:checked")).map((i) => i.value);
  }

  async function run(mode) {
    const ids = selected();
    if (!ids.length) {
      setBusy(false, "Vyberte alespoň jednoho dodavatele.");
      return;
    }
    setBusy(true, mode === "live" ? "Stahuji stránky dodavatelů a data ERÚ a OTE…" : "Načítám uložená data…");
    const codeInput = document.getElementById("demo-code");
    try {
      const resp = await fetch("/api/v1/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ supplier_ids: ids, mode: mode, code: codeInput ? codeInput.value : undefined }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Audit selhal.");
      render(data);
      setBusy(false, "");
    } catch (err) {
      setBusy(false, "Chyba: " + err.message);
    }
  }

  async function showExample() {
    setBusy(true, "Načítám uloženou ukázku…");
    try {
      const resp = await fetch("/api/v1/example");
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Ukázka není k dispozici.");
      render(data);
      setBusy(false, "");
    } catch (err) {
      setBusy(false, "Chyba: " + err.message);
    }
  }

  function sourceRow(src) {
    const row = el("li", "source");
    const head = el("div", "source-head");
    head.append(el("strong", null, src.name), el("span", "badge origin-" + src.origin, ORIGIN_TEXT[src.origin] || src.origin));
    row.append(head);
    const meta = el("div", "meta");
    meta.append("Získáno " + fmtTime(src.fetched_at));
    if (src.data_period) meta.append(" · období dat " + src.data_period);
    if (src.unit) meta.append(" · " + src.unit);
    row.append(meta);
    row.append(link(src.page_url || src.source_url, src.page_url || src.source_url));
    return row;
  }

  function bandChart(chart) {
    const lo = Math.min(chart.low, chart.price), hi = Math.max(chart.high, chart.price);
    const pad = (hi - lo) * 0.25 || 100;
    const min = lo - pad, max = hi + pad;
    const pct = (v) => ((v - min) / (max - min)) * 100;
    const box = el("div", "chart");
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", "Cena nabídky " + Math.round(chart.price) + " Kč/MWh, pásmo ERÚ " +
      Math.round(chart.low) + " až " + Math.round(chart.high) + " Kč/MWh");
    const track = el("div", "chart-track");
    const band = el("div", "chart-band");
    band.style.left = pct(chart.low) + "%";
    band.style.width = pct(chart.high) - pct(chart.low) + "%";
    const marker = el("div", "chart-marker pos-" + chart.position);
    marker.style.left = pct(chart.price) + "%";
    track.append(band, marker);
    const labels = el("div", "chart-labels");
    const bandLabel = el("span", "chart-label", "pásmo ERÚ");
    bandLabel.style.left = (pct(chart.low) + pct(chart.high)) / 2 + "%";
    const priceLabel = el("span", "chart-label price", "nabídka");
    priceLabel.style.left = pct(chart.price) + "%";
    labels.append(bandLabel, priceLabel);
    box.append(track, labels);
    return box;
  }

  function claimCard(c) {
    const card = el("article", "claim verdict-" + c.verdict);
    const head = el("div", "claim-head");
    head.append(el("span", "badge verdict", c.verdict_label), el("span", "rule", c.rule_label));
    if (c.possible_regulatory_risk) head.append(el("span", "badge risk", "možné regulatorní riziko – k posouzení"));
    if (c.discovered) head.append(el("span", "badge auto", "nalezeno automaticky"));
    card.append(head);

    const quote = el("blockquote", null, "„" + c.quote + "“");
    card.append(quote);
    const src = el("p", "meta");
    src.append(link(c.source_url, new URL(c.source_url).hostname), " · zachyceno " + c.captured_at + " · " +
      (STATUS_TEXT[c.source_status] || c.source_status));
    card.append(src);

    const dl = el("dl");
    const rows = [["Nález", c.finding], ["Omezení", c.limitation], ["Doporučený krok", c.action]];
    if (c.risk_reason) rows.push(["Možné riziko", c.risk_reason]);
    rows.forEach(([k, v]) => {
      dl.append(el("dt", null, k), el("dd", null, v));
    });
    card.append(dl);

    if (c.subclaims && c.subclaims.length) {
      const ul = el("ul", "subclaims");
      c.subclaims.forEach((s) => {
        const li = el("li");
        li.append(el("span", null, s.text + " — "), el("em", null, s.status));
        ul.append(li);
      });
      card.append(ul);
    }
    if (c.evidence && c.evidence.length) {
      const details = el("details");
      details.open = c.verdict !== "unverifiable" || c.opportunity;
      details.append(el("summary", null, "Podklady a výpočet (" + c.evidence.length + ")"));
      c.evidence.forEach((e) => {
        const row = el("div", "evidence");
        row.append(el("div", "evidence-label", e.label), el("div", "evidence-value", e.value));
        if (e.chart) row.append(bandChart(e.chart));
        details.append(row);
      });
      card.append(details);
    }
    return card;
  }

  function render(r) {
    reportEl.replaceChildren();
    reportEl.hidden = false;

    const SAVED_TEXT = {
      live: "Zdroje byly tehdy staženy živě; nejde o aktuální běh.",
      mixed: "Část zdrojů tehdy pocházela ze snapshotu; nejde o aktuální běh.",
      snapshot: "Vznikla z uložených dat; nejde o aktuální běh.",
    };
    const modeText = r.is_saved_example
      ? "Uložená ukázka z " + fmtTime(r.generated_at) + ". " + SAVED_TEXT[r.mode]
      : MODE_TEXT[r.mode];
    const mode = el("p", "mode mode-" + (r.is_saved_example ? "snapshot" : r.mode), modeText);
    reportEl.append(mode);
    if (r.warnings && r.warnings.length) {
      const warn = el("ul", "warnings");
      r.warnings.forEach((w) => warn.append(el("li", null, w)));
      reportEl.append(warn);
    }

    const brief = el("section", "panel briefing");
    brief.append(el("h2", null, "Briefing pro vedení"), el("p", "headline", r.briefing.headline), el("p", null, r.briefing.summary));
    if (r.briefing.priorities.length) {
      const ol = el("ol", "priorities");
      r.briefing.priorities.forEach((p) => {
        const li = el("li");
        li.append(el("div", "prio-quote", p.supplier + ": „" + p.quote + "“"), el("div", null, p.why), el("div", "prio-step", p.next_step));
        ol.append(li);
      });
      brief.append(ol);
    }
    if (r.briefing.matrix && Object.keys(r.briefing.matrix).length) {
      const order = [["supported", "podloženo"], ["partially_supported", "částečně"],
        ["contradicted", "v rozporu"], ["unverifiable", "nelze ověřit"]];
      const table = el("table", "matrix");
      const head = el("tr");
      head.append(el("th", null, "Typ tvrzení"));
      order.forEach(([, label]) => head.append(el("th", null, label)));
      table.append(head);
      Object.entries(r.briefing.matrix).forEach(([rule, counts]) => {
        const tr = el("tr");
        tr.append(el("th", null, rule));
        order.forEach(([key]) => tr.append(el("td", counts[key] ? "has" : null, String(counts[key] || 0))));
        table.append(tr);
      });
      const box = el("div", "matrix-wrap");
      box.append(table);
      brief.append(box);
    }
    brief.append(el("p", "meta", "Způsob sestavení: " + r.briefing.method));
    reportEl.append(brief);

    const srcPanel = el("section", "panel");
    srcPanel.append(el("h2", null, "Zdroje použité v tomto běhu"));
    const ul = el("ul", "sources");
    ul.append(sourceRow(r.sources.eru), sourceRow(r.sources.ote));
    r.suppliers.forEach((s) => { if (s.page) ul.append(sourceRow(s.page)); });
    srcPanel.append(ul);
    srcPanel.append(el("p", "meta", "Připraveno předem: seznam dodavatelů, citáty s datem zachycení a ceny nabídek. " +
      "Staženo při běhu: stránky dodavatelů (kontrola, že citát a cena stále platí), pásma ERÚ a počty OPM z OTE."));
    reportEl.append(srcPanel);

    r.suppliers.forEach((s) => {
      const sec = el("section", "panel supplier");
      sec.append(el("h2", null, s.name));
      s.claims.forEach((c) => sec.append(claimCard(c)));
      if (s.candidates && s.candidates.length) {
        const det = el("details", "candidates");
        det.append(el("summary", null, "Další tvrzení nalezená na stránce (" + s.candidates.length + ", bez verdiktu)"));
        det.append(el("p", "meta", "Nalezeno podle klíčových slov a nezkontrolováno člověkem, proto se nevyhodnocují."));
        const list = el("ul");
        s.candidates.forEach((c) => list.append(el("li", null, "„" + c.quote + "“ (" + c.rule_label + ")")));
        det.append(list);
        sec.append(det);
      }
      reportEl.append(sec);
    });
    reportEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); run("live"); });
  document.getElementById("run-snapshot").addEventListener("click", () => run("snapshot"));
  document.getElementById("show-example").addEventListener("click", showExample);
})();
