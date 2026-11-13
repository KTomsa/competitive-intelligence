// Fact or Watt front end. Everything that comes from the API (and so, in live
// mode, from supplier websites or an LLM) is written with textContent. There is
// no innerHTML anywhere in this file.

const VERDICTS = {
  supported: "Podloženo",
  partially_supported: "Částečně podloženo",
  contradicted: "V rozporu s daty",
  unverifiable: "Nelze ověřit",
};
const VERDICT_ORDER = ["supported", "partially_supported", "contradicted", "unverifiable"];
const CLAIM_TYPES = { price: "Cena", green: "Ekologie", customer: "Zákazníci", other: "Ostatní" };
const STATUSES = {
  queued: "Ve frontě",
  collecting: "Načítám weby dodavatelů",
  extracting: "Hledám tvrzení",
  verifying: "Ověřuji proti datům",
  judging: "Kontroluji shodu důkazů",
  reporting: "Sestavuji briefing",
  completed: "Dokončeno",
  failed: "Selhalo",
};
const HTTP_MESSAGES = {
  401: "Přístupový kód chybí nebo nesouhlasí.",
  403: "K této akci nemáte oprávnění.",
  429: "Příliš mnoho pokusů. Zkuste to za chvíli.",
  503: "Spouštění auditů není na serveru nastaveno.",
};

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attrs || {})) node.setAttribute(name, value);
  for (const child of children) if (child) node.append(child);
  return node;
}

function formatDateTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso) : date.toLocaleString("cs-CZ");
}

function formatDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso) : date.toLocaleDateString("cs-CZ");
}

function safeLink(url, label) {
  // Only https links from the backend source registry become clickable.
  if (typeof url === "string" && url.startsWith("https://")) {
    return el("a", { text: label || url, attrs: { href: url, rel: "noopener noreferrer", target: "_blank" } });
  }
  return el("span", { text: label || String(url) });
}

function originLabel(source) {
  if (source.origin === "live") return `Z webu načteno: ${formatDateTime(source.captured_at)}`;
  if (source.origin === "baseline") {
    const approved = source.baseline_approved_at ? formatDate(source.baseline_approved_at) : "neznámé datum";
    return `Web ověřen ${formatDateTime(source.checked_at)}, beze změny – tvrzení převzata ze schválené baseline ze dne ${approved}`;
  }
  const reason = source.fallback_reason ? ` (${source.fallback_reason})` : "";
  return `Web se nepodařilo načíst${reason} – použit uložený snapshot ze dne ${formatDate(source.captured_at)}`;
}

function badge(text, kind) {
  return el("span", { className: `badge badge-${kind}`, text });
}

function renderMatrix(matrix) {
  const types = Object.keys(CLAIM_TYPES).filter((type) => matrix[type]);
  if (!types.length) return null;
  const head = el("tr", {}, [
    el("th", { text: "Typ tvrzení", attrs: { scope: "col" } }),
    ...VERDICT_ORDER.map((v) => el("th", { text: VERDICTS[v], attrs: { scope: "col" } })),
  ]);
  const rows = types.map((type) =>
    el("tr", {}, [
      el("th", { text: CLAIM_TYPES[type], attrs: { scope: "row" } }),
      ...VERDICT_ORDER.map((v) => el("td", { text: String(matrix[type][v] || 0) })),
    ]),
  );
  return el("div", { className: "table-scroll" }, [
    el("table", { className: "matrix" }, [
      el("caption", { text: "Počty verdiktů podle typu tvrzení (ne hodnocení dodavatelů)" }),
      el("thead", {}, [head]),
      el("tbody", {}, rows),
    ]),
  ]);
}

function renderFact(fact) {
  const value = fact.value !== null && fact.value !== undefined ? `: ${fact.value} ${fact.unit || ""}`.trimEnd() : "";
  const item = el("li", {}, [
    el("span", { text: `${fact.statement}${value}` }),
    el("div", { className: "fact-meta" }, [
      safeLink(fact.source_url, "zdroj"),
      el("span", { text: ` · ${fact.source_ref} · platí k ${formatDate(fact.valid_as_of)} · získáno ${formatDateTime(fact.captured_at)} · rozsah: ${fact.scope}` }),
    ]),
  ]);
  for (const limitation of fact.limitations || []) {
    item.append(el("div", { className: "limitation", text: `Omezení: ${limitation}` }));
  }
  return item;
}

function renderClaim(claim, verdict, evidence) {
  const card = el("article", { className: `claim claim-${verdict.verdict}` });
  card.append(
    el("blockquote", { text: `„${claim.quote}“` }),
    el("div", { className: "badges" }, [
      badge(VERDICTS[verdict.verdict] || verdict.verdict, verdict.verdict),
      badge(`pravidlo: ${claim.rule.id}`, "neutral"),
      badge(claim.human_reviewed ? "tvrzení zkontrolováno člověkem" : "tvrzení neověřeno člověkem", "neutral"),
      verdict.judge_changed ? badge("verdikt snížen kontrolou shody", "warning") : null,
      verdict.possible_regulatory_risk ? badge("možné regulatorní riziko", "warning") : null,
      verdict.review_required ? badge("nutná kontrola člověkem", "warning") : null,
    ]),
    el("p", { text: verdict.reasoning }),
  );
  if (verdict.judge_changed) {
    card.append(
      el("p", { className: "limitation", text: `Původní výsledek pravidla: ${VERDICTS[verdict.rule_verdict]}. Důvod změny: ${verdict.judge_reason}` }),
    );
  }
  if (verdict.possible_regulatory_risk) {
    card.append(el("p", { className: "limitation", text: `Možné riziko (není právní závěr): ${verdict.risk_reason}` }));
  }
  for (const limitation of verdict.limitations || []) {
    card.append(el("p", { className: "limitation", text: `Omezení: ${limitation}` }));
  }
  const facts = evidence ? evidence.facts : [];
  const details = el("details", {}, [
    el("summary", { text: facts.length ? `Důkazy (${facts.length})` : "Žádné použitelné důkazy" }),
  ]);
  if (facts.length) details.append(el("ul", { className: "facts" }, facts.map(renderFact)));
  details.append(
    el("p", { className: "fact-meta" }, [
      el("span", { text: "Tvrzení nalezeno na: " }),
      safeLink(claim.source_url),
      el("span", { text: ` · ${claim.source_ref}` }),
    ]),
  );
  card.append(details);
  return card;
}

function renderResult(root, result) {
  root.replaceChildren();
  if (result.mode === "fixture") {
    root.append(el("p", { className: "notice warning", text: `Ukázkový režim. ${result.scope_note}` }));
  } else {
    root.append(el("p", { className: "notice warning", text: result.scope_note }));
  }
  const meta = [`Dokončeno: ${formatDateTime(result.completed_at)}`];
  if (result.published_at) meta.push(`Publikováno: ${formatDateTime(result.published_at)}`);
  meta.push(`Profily: ${result.profile_ids.join(", ")}`);
  root.append(el("p", { className: "muted", text: meta.join(" · ") }));

  const briefing = el("section", { className: "panel" }, [
    el("h3", { text: "Briefing pro manažera" }),
    el("p", { text: result.briefing.summary }),
  ]);
  if (result.briefing.items.length) {
    briefing.append(
      el("ul", {}, result.briefing.items.map((item) =>
        el("li", {}, [el("span", { text: item.text }), el("span", { className: "muted", text: ` (${item.claim_id})` })]),
      )),
    );
  }
  const matrix = renderMatrix(result.briefing.matrix);
  if (matrix) briefing.append(matrix);
  root.append(briefing);

  for (const warning of result.warnings || []) {
    root.append(el("p", { className: "notice warning", text: warning }));
  }

  const verdicts = new Map(result.verdicts.map((v) => [v.claim_id, v]));
  const evidence = new Map(result.evidence.map((e) => [e.claim_id, e]));
  for (const supplier of result.suppliers) {
    const section = el("section", { className: "supplier" }, [el("h3", { text: supplier.name })]);
    for (const source of result.sources.filter((s) => s.supplier_id === supplier.supplier_id)) {
      section.append(
        el("p", { className: `origin origin-${source.origin}` }, [
          el("span", { text: `${originLabel(source)} · ` }),
          safeLink(source.source_url),
        ]),
      );
    }
    const claims = result.claims.filter((c) => c.supplier_id === supplier.supplier_id);
    if (!claims.length) section.append(el("p", { className: "muted", text: "Žádná ověřitelná tvrzení." }));
    for (const claim of claims) {
      const verdict = verdicts.get(claim.id);
      if (verdict) section.append(renderClaim(claim, verdict, evidence.get(claim.id)));
    }
    root.append(section);
  }
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { response, body };
}

function errorText(response, body) {
  return HTTP_MESSAGES[response.status] || body?.error?.message || `Chyba serveru (${response.status}).`;
}

async function initIndex() {
  const latest = document.getElementById("latest");
  try {
    const { response, body } = await fetchJson("/api/v1/audits/latest");
    if (response.ok) renderResult(latest, body);
    else latest.replaceChildren(el("p", { className: "muted", text: response.status === 404 ? "Zatím nebyl publikován žádný audit." : errorText(response, body) }));
  } catch {
    latest.replaceChildren(el("p", { className: "notice error", text: "Výsledky se nepodařilo načíst." }));
  }

  const form = document.getElementById("launch-form");
  const errorBox = document.getElementById("launch-error");
  const currentMode = () => form.querySelector('input[name="mode"]:checked').value;
  const syncMode = () => {
    for (const group of form.querySelectorAll("fieldset[data-mode]")) group.hidden = group.dataset.mode !== currentMode();
  };
  for (const radio of form.querySelectorAll('input[name="mode"]')) radio.addEventListener("change", syncMode);
  syncMode();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    const mode = currentMode();
    const scope = form.querySelector(`fieldset[data-mode="${mode}"]`);
    const checked = (root, name) => [...root.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);
    const codeInput = document.getElementById("access-code");
    const button = form.querySelector('button[type="submit"]');
    const payload = { supplier_ids: checked(scope, "supplier_ids"), profile_ids: checked(form, "profile_ids"), commodity: "electricity", mode };
    if (!payload.supplier_ids.length || !payload.profile_ids.length) {
      errorBox.textContent = "Vyberte alespoň jednoho dodavatele a jeden profil.";
      errorBox.hidden = false;
      return;
    }
    button.disabled = true;
    button.textContent = mode === "live" ? "Načítám weby dodavatelů…" : "Spouštím…";
    try {
      // The code travels only in a request header: never in the URL, never in storage.
      const { response, body } = await fetchJson("/api/v1/audits", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Audit-Access-Code": codeInput.value },
        body: JSON.stringify(payload),
      });
      codeInput.value = "";
      if (response.status === 202) {
        window.location.assign(`/audits/${encodeURIComponent(body.audit_id)}`);
        return;
      }
      errorBox.textContent = errorText(response, body);
    } catch {
      errorBox.textContent = "Server není dostupný.";
    }
    button.disabled = false;
    button.textContent = "Spustit audit";
    errorBox.hidden = false;
  });
}

async function initAudit() {
  const auditId = document.body.dataset.auditId;
  const base = `/api/v1/audits/${encodeURIComponent(auditId)}`;
  const events = document.getElementById("events");
  const statusLine = document.getElementById("status-line");
  const bar = document.getElementById("progress-bar");
  const progress = document.getElementById("progress");
  const errorBox = document.getElementById("audit-error");
  let lastEventId = 0;

  const showError = (text) => {
    errorBox.textContent = text;
    errorBox.hidden = false;
  };

  async function tick() {
    let status;
    try {
      const { response, body } = await fetchJson(base);
      if (!response.ok) {
        statusLine.textContent = "";
        showError(response.status === 404 ? "Audit s tímto ID neexistuje. Ukázkové audity se po restartu serveru neuchovávají." : errorText(response, body));
        return;
      }
      status = body;
    } catch {
      window.setTimeout(tick, 3000);
      return;
    }
    for (const event of status.events) {
      if (event.event_id <= lastEventId) continue;
      lastEventId = event.event_id;
      events.append(
        el("li", { className: `event event-${event.level}` }, [
          el("time", { text: formatDateTime(event.timestamp), attrs: { datetime: event.timestamp } }),
          el("span", { text: ` ${event.stage}: ${event.message}` }),
        ]),
      );
    }
    bar.style.width = `${status.progress_percent}%`;
    progress.setAttribute("aria-valuenow", String(status.progress_percent));
    statusLine.textContent = STATUSES[status.status] || status.status;

    if (status.status === "completed") {
      const { response, body } = await fetchJson(`${base}/results`);
      if (response.ok) renderResult(document.getElementById("results"), body);
      else showError(errorText(response, body));
      return;
    }
    if (status.status === "failed") {
      showError(status.error ? status.error.message : "Audit selhal.");
      return;
    }
    window.setTimeout(tick, 1500);
  }
  tick();
}

const page = document.body.dataset.page;
if (page === "index") initIndex();
if (page === "audit") initAudit();
