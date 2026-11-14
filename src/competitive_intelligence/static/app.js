(function () {
  "use strict";

  const form = document.getElementById("audit-form");
  const statusEl = document.getElementById("status");
  const reportEl = document.getElementById("report");
  const buttons = form.querySelectorAll("button");
  const examplePreviewButton = document.getElementById("preview-show-example");
  const modeOptions = form.querySelectorAll('input[name="audit-mode"]');
  const modeHelp = document.getElementById("analysis-mode-help");
  const runSummary = document.getElementById("run-summary");

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
    // Ukázka je mimo formulář, ale během běžící analýzy má být také nedostupná.
    if (examplePreviewButton) examplePreviewButton.disabled = busy;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("busy", busy);
  }

  function selected() {
    return Array.from(form.querySelectorAll("input[name=supplier]:checked")).map((i) => i.value);
  }

  function selectedMode() {
    const checked = form.querySelector('input[name="audit-mode"]:checked');
    return checked && checked.value === "snapshot" ? "snapshot" : "live";
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
        body: JSON.stringify({
          supplier_ids: ids,
          mode: mode,
          perspective: document.getElementById("perspective").value || undefined,
          code: codeInput ? codeInput.value : undefined,
        }),
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
    if (src.effective_from) meta.append(" · účinnost od " + src.effective_from);
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

  const czNumber = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 0 });
  const czMoney = new Intl.NumberFormat("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const POSITION_TEXT = { below: "pod pásmem", within: "v pásmu", above: "nad pásmem", unknown: "nelze určit" };

  function comparisonPanel(r) {
    if (!r.comparison || !r.comparison.length) return null;
    const sec = el("section", "panel");
    sec.append(el("h2", null, "Srovnání fixovaných nabídek"));
    sec.append(el("p", "meta", "Sazba " + r.profile.tariff + (r.profile.area ? ", distribuční území " + r.profile.area : "") +
      ". Roční náklad je obchodní část ceny s DPH (12 × stálý plat + spotřeba × cena), bez regulovaných složek. " +
      "Seřazeno od nejnižšího nákladu. Pásmo ERÚ: pod pásmem = nejlevnější čtvrtina nabídek na trhu."));
    r.comparison.forEach((g) => {
      sec.append(el("h3", null, "Fixace " + g.fixation_months + " měsíců"));
      const wrap = el("div", "matrix-wrap");
      const table = el("table", "cmp");
      const head = el("tr");
      ["Dodavatel a produkt", "Kč/MWh bez DPH", "Stálý plat Kč/měs."]
        .concat(g.annual_mwh.map((m) => "Ročně při " + m + " MWh"), ["Pásmo ERÚ", "Ověření"])
        .forEach((t) => head.append(el("th", null, t)));
      table.append(head);
      g.rows.forEach((row) => {
        const tr = el("tr");
        const first = el("td", "cmp-name");
        first.append(el("strong", null, row.supplier), el("span", null, " " + row.product));
        if (row.price_list_url) {
          first.append(" ");
          first.append(link(row.price_list_url, "ceník"));
        }
        tr.append(first);
        tr.append(el("td", null, row.price_mwh_excl_vat == null ? "–" : czNumber.format(row.price_mwh_excl_vat)));
        tr.append(el("td", null, row.monthly_fee_incl_vat == null ? "–" : czMoney.format(row.monthly_fee_incl_vat)));
        row.annual_cost.forEach((c) => tr.append(el("td", null, c == null ? "–" : czNumber.format(c) + " Kč")));
        const pos = el("td");
        pos.append(el("span", "badge pos-" + row.position, POSITION_TEXT[row.position] || row.position));
        tr.append(pos);
        const ver = el("td");
        const badge = el("span", "badge " + (row.confirmed_live ? "origin-live" : "origin-snapshot"),
          row.confirmed_live ? "živě" : "z přípravy");
        badge.title = row.confirmation;
        ver.append(badge);
        tr.append(ver);
        table.append(tr);
      });
      wrap.append(table);
      sec.append(wrap);
    });
    sec.append(el("p", "meta", "Ověření „živě“ znamená, že při tomto běhu byl stažen ceník nebo stránka dodavatele " +
      "a cena v nich stále je. „Z přípravy“ jsou údaje z ceníku, který nelze stáhnout automaticky."));
    return sec;
  }

  function pctText(v) {
    return (v > 0 ? "+" : "") + String(v.toFixed(1)).replace(".", ",") + " %";
  }

  function changeCell(change) {
    const td = el("td");
    if (!change) { td.textContent = "–"; return td; }
    td.append(el("strong", change.abs < 0 ? "neg" : "pos", pctText(change.pct)));
    td.append(el("span", "sub", " (" + (change.abs > 0 ? "+" : "") + czNumber.format(change.abs) + ")"));
    return td;
  }

  function bulletList(items, cls) {
    const ul = el("ul", cls || "plain");
    items.forEach((node) => { const li = el("li"); li.append(node); ul.append(li); });
    return ul;
  }

  function perspectivePanel(v) {
    const sec = el("section", "panel perspective");
    sec.append(el("h2", null, "Pohled pro firmu " + v.supplier));

    sec.append(el("h3", null, "1. Kde stojíme cenou"));
    if (v.price.length) {
      const wrap = el("div", "matrix-wrap");
      const table = el("table", "cmp");
      const head = el("tr");
      ["Fixace a náš produkt", "Ročně při " + v.price[0].annual_mwh[0] + " MWh", "Pořadí ve vzorku", "Pásmo ERÚ", "Kdo je levnější"]
        .forEach((t) => head.append(el("th", null, t)));
      table.append(head);
      v.price.forEach((p) => {
        const tr = el("tr", "plain-row");
        const first = el("td", "cmp-name");
        first.append(el("strong", null, p.fixation_months + " měs."), el("span", null, " " + p.product));
        tr.append(first);
        tr.append(el("td", null, czNumber.format(p.annual_cost[0]) + " Kč"));
        tr.append(el("td", null, p.rank + ". z " + p.of));
        const pos = el("td");
        pos.append(el("span", "badge pos-" + p.position, POSITION_TEXT[p.position] || p.position));
        tr.append(pos);
        const who = el("td", "cmp-name");
        who.textContent = p.cheaper.length
          ? p.cheaper.map((c) => c.supplier + " o " + czNumber.format(c.saving[0]) + " Kč").join(", ")
          : "nikdo ve vzorku";
        tr.append(who);
        table.append(tr);
      });
      wrap.append(table);
      sec.append(wrap);
    } else {
      sec.append(el("p", "meta", "Pro tuto firmu nemáme ceník fixovaného produktu, cenu proto nelze srovnat."));
    }

    sec.append(el("h3", null, "2. Kde je naše vlastní reklama napadnutelná"));
    if (v.risky.length) {
      sec.append(bulletList(v.risky.map((c) => {
        const box = el("div");
        box.append(el("strong", null, "„" + c.quote + "“ "), el("span", "badge", c.verdict_label));
        box.append(el("div", null, c.finding));
        return box;
      })));
      sec.append(el("p", "meta", "Stejná data může použít konkurent. Zvážit přeformulování, nebo si připravit doložení."));
    } else {
      sec.append(el("p", "meta", "Žádné z připravených tvrzení není podle dostupných dat napadnutelné."));
    }
    if (v.cautions.length) sec.append(bulletList(v.cautions.map((t) => el("span", null, t)), "plain warn"));
    if (v.solid.length) {
      sec.append(el("p", "meta", "Obstojí: " + v.solid.map((c) => "„" + c.quote + "“ (" + c.verdict_label + ")").join("; ")));
    }

    sec.append(el("h3", null, "3. Co můžeme tvrdit a doložit"));
    if (v.provable.length) {
      sec.append(bulletList(v.provable.map((c) => {
        const box = el("div");
        box.append(el("div", null, c.text));
        box.append(el("div", "meta", "Zdroj: " + c.source + ". " + c.caveat));
        return box;
      })));
    } else {
      sec.append(el("p", "meta", "Z dostupných dat nevychází žádné tvrzení, které by šlo doložit."));
    }

    sec.append(el("h3", null, "4. Co dělá konkurence a týká se nás"));
    const KIND = { price: "Cena", claim: "Tvrzení konkurenta", loyalty: "Noví a stávající zákazníci", growth: "Růst", regulator: "Regulátor" };
    if (v.watch.length) {
      sec.append(bulletList(v.watch.map((w) => {
        const box = el("div");
        box.append(el("span", "badge auto", KIND[w.kind] || w.kind), " ");
        if (w.kind === "claim") {
          box.append(el("strong", null, w.supplier + ": „" + w.quote + "“ "), el("span", "badge", w.verdict_label));
          box.append(el("div", null, w.text));
        } else {
          box.append(el("span", null, w.text));
        }
        return box;
      })));
    } else {
      sec.append(el("p", "meta", "V datech není nic, co by se této firmy přímo týkalo."));
    }
    sec.append(el("p", "meta", v.note));
    return sec;
  }

  function marketPanel(m, ownId) {
    const sec = el("section", "panel");
    sec.append(el("h2", null, "Cena a růst: kdo získává odběrná místa"));
    if (m.observations.length) sec.append(bulletList(m.observations.map((t) => el("strong", null, t)), "plain"));
    const wrap = el("div", "matrix-wrap");
    const table = el("table", "cmp");
    const head = el("tr");
    ["Dodavatel", "Elektřina: změna OPM", "Plyn: změna OPM",
      "Nejlevnější nabídka" + (m.annual_mwh ? " (ročně při " + m.annual_mwh + " MWh)" : ""),
      "Pásmo ERÚ", "Přestupky podle ERÚ"].forEach((t) => head.append(el("th", null, t)));
    table.append(head);
    m.rows.forEach((row) => {
      const tr = el("tr", "plain-row" + (row.id === ownId ? " own" : ""));
      tr.append(el("td", "cmp-name", row.name + (row.id === ownId ? " (my)" : "")));
      tr.append(changeCell(row.electricity), changeCell(row.gas));
      tr.append(el("td", null, row.price
        ? row.price.fixation_months + " měs.: " + row.price.rank + ". z " + row.price.of + " · " + czNumber.format(row.price.annual_cost) + " Kč"
        : "bez ceníku"));
      const pos = el("td");
      if (row.price) pos.append(el("span", "badge pos-" + row.price.position, POSITION_TEXT[row.price.position] || row.price.position));
      else pos.textContent = "–";
      tr.append(pos);
      const dec = el("td");
      if (!row.decisions) dec.textContent = "–";
      else if (!row.decisions.offences) dec.textContent = "0";
      else {
        dec.append(el("strong", null, String(row.decisions.offences)));
        if (row.decisions.practices) dec.append(el("span", "sub neg", " · " + row.decisions.practices + " o praktikách"));
      }
      tr.append(dec);
      table.append(tr);
    });
    wrap.append(table);
    sec.append(wrap);
    const period = m.period ? "Změna počtu odběrných míst od " + m.period.from + " do " + m.period.to + " podle OTE" : "Změna počtu odběrných míst podle OTE";
    const total = m.market_electricity_pct == null ? "" : "; trh elektřiny jako celek " + pctText(m.market_electricity_pct);
    sec.append(el("p", "meta", period + total + ". " + m.caveat));

    if (m.loyalty_gaps && m.loyalty_gaps.length) {
      sec.append(el("h3", null, "Cena pro nové a pro stávající zákazníky"));
      const wrap2 = el("div", "matrix-wrap");
      const t2 = el("table", "cmp");
      const h2 = el("tr");
      ["Dodavatel a fixace", "Nový zákazník Kč/MWh", "Stávající Kč/MWh", "Rozdíl", "Ročně navíc", "Ověření"]
        .forEach((t) => h2.append(el("th", null, t)));
      t2.append(h2);
      m.loyalty_gaps.forEach((g) => {
        const tr = el("tr", "plain-row");
        const first = el("td", "cmp-name");
        first.append(el("strong", null, g.supplier), el("span", null, " " + g.fixation_months + " měs."));
        first.append(el("div", "sub", g.new_product + " → " + g.existing_product));
        tr.append(first);
        tr.append(el("td", null, czNumber.format(g.new_price)));
        tr.append(el("td", null, czNumber.format(g.existing_price)));
        const diff = el("td");
        diff.append(el("strong", "neg", "+" + String(g.pct).replace(".", ",") + " %"));
        tr.append(diff);
        const annual = g.annual_diff.filter((x) => x != null).map((x) => czNumber.format(x) + " Kč");
        tr.append(el("td", null, annual.length ? annual.join(" až ") : "–"));
        const ver = el("td");
        ver.append(el("span", "badge " + (g.confirmed_live ? "origin-live" : "origin-snapshot"),
          g.confirmed_live ? "ceníky živě" : "z přípravy"));
        if (g.inferred) ver.append(" ", el("span", "badge", "odvozeno"));
        tr.append(ver);
        t2.append(tr);
      });
      wrap2.append(t2);
      sec.append(wrap2);
      const notes = Array.from(new Set(m.loyalty_gaps.map((g) => g.source_note).filter(Boolean)));
      sec.append(el("p", "meta", m.loyalty_caveat + " " + notes.join(" ")));
    }
    return sec;
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
        const label = el("div", "evidence-label");
        if (e.url) label.append(link(e.url, e.label)); else label.textContent = e.label;
        row.append(label, el("div", "evidence-value", e.value));
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

    if (r.perspective) reportEl.append(perspectivePanel(r.perspective));

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
    if (r.market) reportEl.append(marketPanel(r.market, r.perspective ? r.perspective.id : null));

    const srcPanel = el("section", "panel");
    srcPanel.append(el("h2", null, "Zdroje použité v tomto běhu"));
    const ul = el("ul", "sources");
    ul.append(sourceRow(r.sources.eru), sourceRow(r.sources.ote));
    if (r.sources.ote_gas) ul.append(sourceRow(r.sources.ote_gas));
    if (r.sources.eru_decisions) ul.append(sourceRow(r.sources.eru_decisions));
    r.suppliers.forEach((s) => { if (s.page) ul.append(sourceRow(s.page)); });
    srcPanel.append(ul);
    if (r.price_lists && r.price_lists.length) {
      const liveCount = r.price_lists.filter((p) => p.origin === "live").length;
      const det = el("details", "pricelists");
      det.append(el("summary", null, "Ceníky dodavatelů ve formátu PDF (" + r.price_lists.length +
        ", z toho živě staženo " + liveCount + ")"));
      const list = el("ul", "sources");
      r.price_lists.forEach((p) => list.append(sourceRow(Object.assign({}, p, { name: p.supplier + " – " + p.name }))));
      det.append(list);
      srcPanel.append(det);
    }
    srcPanel.append(el("p", "meta", "Připraveno předem: seznam dodavatelů, citáty s datem zachycení, ceny a stálé platy z ceníků. " +
      "Staženo při běhu: stránky dodavatelů a jejich ceníky (kontrola, že citát a cena stále platí), " +
      "pásma ERÚ a počty OPM elektřiny i plynu z OTE."));
    const cmp = comparisonPanel(r);
    if (cmp) reportEl.append(cmp);
    reportEl.append(srcPanel);

    r.suppliers.forEach((s) => {
      const sec = el("section", "panel supplier");
      sec.append(el("h2", null, s.name));
      s.claims.forEach((c) => sec.append(claimCard(c)));
      if (s.decisions && s.decisions.length) {
        const DKIND = { practice: "obchodní praktiky", offence: "jiný přestupek", dispute: "spor", discontinued: "řízení zastaveno", other: "jiné" };
        const det = el("details", "candidates");
        const practices = s.decisions.filter((d) => d.kind === "practice").length;
        det.append(el("summary", null, "Pravomocná rozhodnutí ERÚ (" + s.decisions.length +
          (practices ? ", z toho " + practices + " o obchodních praktikách" : "") + ")"));
        const list = el("ul");
        s.decisions.forEach((d) => {
          const li = el("li");
          li.append(d.date + " · ", link(d.url, d.title), " ", el("span", "badge" + (d.kind === "practice" ? " risk" : ""), DKIND[d.kind] || d.kind));
          list.append(li);
        });
        det.append(list);
        det.append(el("p", "meta", "Zdroj: seznam pravomocných rozhodnutí ERÚ, datum je datum zveřejnění. Obsah rozhodnutí aplikace nečte."));
        sec.append(det);
      }
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

  // Vzhledové prvky titulní stránky. Nezasahují do datového workflow ani do výpočtů.
  const summary = document.getElementById("selected-summary");
  function refreshSelected() {
    const n = selected().length;
    if (summary) summary.textContent = n === 1 ? "Vybrán 1 dodavatel" : "Vybráno " + n + " dodavatelů";
    const live = selectedMode() === "live";
    if (modeHelp) modeHelp.textContent = live
      ? "Načtou se dostupné veřejné zdroje. Pokud zdroj selže, případné použití zálohy bude označeno."
      : "Analýza proběhne nad uloženými daty. Výsledek nebude označen jako živé ověření.";
    if (runSummary) runSummary.textContent = (n === 1 ? "1 dodavatel" : n + " dodavatelů") +
      " · " + (live ? "živý sběr" : "uložená data") +
      " · " + (document.getElementById("perspective").selectedOptions[0]?.textContent || "neutrální srovnání");
  }
  form.querySelectorAll('input[name="supplier"]').forEach((input) =>
    input.addEventListener("change", refreshSelected));
  modeOptions.forEach((input) => input.addEventListener("change", refreshSelected));
  document.getElementById("perspective").addEventListener("change", refreshSelected);
  refreshSelected();

  async function loadSavedPreview() {
    const quoteEl = document.getElementById("preview-quote");
    const verdictEl = document.getElementById("preview-verdict");
    const supplierEl = document.getElementById("preview-supplier");
    try {
      const resp = await fetch("/api/v1/example");
      if (!resp.ok) throw new Error("Uložená ukázka není dostupná.");
      const data = await resp.json();
      const supplier = (data.suppliers || []).find((s) => (s.claims || []).length);
      if (!supplier) throw new Error("Ukázka neobsahuje tvrzení.");
      const claim = supplier.claims[0];
      quoteEl.textContent = "„" + claim.quote + "“";
      verdictEl.textContent = claim.verdict_label || "Vyhodnocené tvrzení";
      supplierEl.textContent = supplier.name || "";
    } catch (_err) {
      quoteEl.textContent = "Prohlédněte si analýzu konkurence podloženou dohledatelnými zdroji.";
      verdictEl.textContent = "Uložená ukázka nyní není k dispozici";
      supplierEl.textContent = "";
    }
  }
  if (examplePreviewButton) examplePreviewButton.addEventListener("click", showExample);
  loadSavedPreview();

  // Jediné spuštění analýzy; režim určuje uživatelsky zvolený přepínač.
  form.addEventListener("submit", (e) => { e.preventDefault(); run(selectedMode()); });
})();
