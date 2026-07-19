"use client";

import {
  Activity,
  AlertTriangle,
  ArrowDownUp,
  Building2,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Database,
  ExternalLink,
  FileText,
  Gauge,
  Globe2,
  Loader2,
  MapPin,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const api = {
  async getMeta() {
    const response = await fetch("/api/backend/api/meta", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load runtime metadata");
    return response.json();
  },
  async listOpportunities() {
    const response = await fetch("/api/backend/api/opportunities", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load opportunities");
    return response.json();
  },
  async getOpportunity(id) {
    const response = await fetch(`/api/backend/api/opportunities/${id}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load opportunity detail");
    return response.json();
  },
  async getAudit() {
    const response = await fetch("/api/backend/api/data/audit", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load data audit");
    return response.json();
  },
  async listScans() {
    const response = await fetch("/api/backend/api/scans", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load scans");
    return response.json();
  },
  async searchLocations(query, country) {
    const params = new URLSearchParams({ q: query, country, limit: "8" });
    const response = await fetch(`/api/backend/api/locations/search?${params}`, {
      cache: "no-store"
    });
    if (!response.ok) throw new Error("Could not search locations");
    return response.json();
  },
  async runScan(payload) {
    const response = await fetch("/api/backend/api/scans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!response.ok) {
      let message = "Scan failed";
      try {
        const error = await response.json();
        message =
          typeof error.detail === "string"
            ? error.detail
            : error.detail?.message || error.message || message;
      } catch {
        // Keep the generic fallback when the backend returns non-JSON.
      }
      throw new Error(message);
    }
    return response.json();
  },
  async cancelScan(id) {
    const response = await fetch(`/api/backend/api/scans/${id}/cancel`, { method: "POST" });
    if (!response.ok) throw new Error("Could not cancel scan");
    return response.json();
  },
  async retryScan(id) {
    const response = await fetch(`/api/backend/api/scans/${id}/retry`, { method: "POST" });
    if (!response.ok) {
      let message = "Could not retry scan";
      try {
        const error = await response.json();
        message = error.detail || message;
      } catch {
        // Keep generic fallback.
      }
      throw new Error(message);
    }
    return response.json();
  },
  async rescoreOpportunity(id) {
    const response = await fetch(`/api/backend/api/opportunities/${id}/rescore`, {
      method: "POST"
    });
    if (!response.ok) throw new Error("Could not rescore opportunity");
    return response.json();
  }
};

function number(value, fallback = "n/a") {
  if (value === null || value === undefined) return fallback;
  return Number(value).toFixed(1);
}

function money(value) {
  if (value === null || value === undefined) return "$0.000";
  return `$${Number(value).toFixed(3)}`;
}

function artifactByKind(artifacts, kind) {
  return artifacts.find((artifact) => artifact.kind === kind)?.payload;
}

export default function Dashboard() {
  const [opportunities, setOpportunities] = useState([]);
  const [scans, setScans] = useState([]);
  const [audit, setAudit] = useState(null);
  const [meta, setMeta] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [scanLoading, setScanLoading] = useState(false);
  const [notice, setNotice] = useState(null);
  const [lastPlan, setLastPlan] = useState(null);
  const [locationOptions, setLocationOptions] = useState([]);
  const [locationLoading, setLocationLoading] = useState(false);
  const [locationOpen, setLocationOpen] = useState(false);
  const [selectedLocation, setSelectedLocation] = useState(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("score");
  const [form, setForm] = useState({
    service_text: "water heater repair",
    location_text: "",
    country: "US",
    dry_run: true,
    async_run: true,
    confirm_live_cost: false
  });

  async function refresh(selectFirst = false) {
    setLoading(true);
    try {
      const [metaData, data, auditData, scanData] = await Promise.all([
        api.getMeta(),
        api.listOpportunities(),
        api.getAudit(),
        api.listScans()
      ]);
      setMeta(metaData);
      setOpportunities(data.opportunities);
      setAudit(auditData);
      setScans(scanData.scans || []);
      if (selectFirst && data.opportunities[0]) setSelectedId(data.opportunities[0].id);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh(true);
  }, []);

  useEffect(() => {
    const active = scans.some((scanRow) => ["queued", "running"].includes(scanRow.status));
    if (!active) return undefined;
    const timer = setInterval(() => refresh(false), 4000);
    return () => clearInterval(timer);
  }, [scans]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    api
      .getOpportunity(selectedId)
      .then(setDetail)
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  useEffect(() => {
    const text = form.location_text.trim();
    if (selectedLocation && selectedLocation.label === text) return undefined;
    if (text.length < 2) {
      setLocationOptions([]);
      setLocationOpen(false);
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      setLocationLoading(true);
      api
        .searchLocations(text, form.country || "US")
        .then((result) => {
          if (cancelled) return;
          setLocationOptions(result.locations || []);
          setLocationOpen(true);
        })
        .catch(() => {
          if (cancelled) return;
          setLocationOptions([]);
          setLocationOpen(false);
        })
        .finally(() => {
          if (!cancelled) setLocationLoading(false);
        });
    }, 220);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [form.location_text, form.country, selectedLocation]);

  function updateLocationText(value) {
    setSelectedLocation(null);
    setLocationOpen(true);
    setForm({ ...form, location_text: value });
  }

  function chooseLocation(option) {
    setSelectedLocation(option);
    setLocationOpen(false);
    setLocationOptions([]);
    setForm({
      ...form,
      location_text: option.label,
      country: option.country || form.country
    });
  }

  async function submitScan(event) {
    event.preventDefault();
    setScanLoading(true);
    setNotice(null);
    try {
      const result = await api.runScan({
        ...form,
        selected_location: selectedLocation
      });
      setLastPlan(result.scan_plan || null);
      setNotice({ type: result.dry_run ? "dry-run" : "success", message: result.message });
      await refresh(false);
      if (result.opportunity_id) setSelectedId(result.opportunity_id);
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setScanLoading(false);
    }
  }

  async function cancelScan(id) {
    try {
      const result = await api.cancelScan(id);
      setNotice({ type: "success", message: result.message });
      await refresh(false);
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    }
  }

  async function retryScan(id) {
    try {
      const result = await api.retryScan(id);
      setNotice({ type: "success", message: result.message });
      await refresh(false);
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    }
  }

  async function rescoreSelected() {
    if (!selectedId) return;
    setDetailLoading(true);
    try {
      const result = await api.rescoreOpportunity(selectedId);
      setNotice({ type: "success", message: `Rescored opportunity ${result.opportunity.id}` });
      await refresh(false);
      setDetail(await api.getOpportunity(selectedId));
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setDetailLoading(false);
    }
  }

  const filtered = useMemo(() => {
    const text = query.trim().toLowerCase();
    const rows = opportunities.filter((item) => {
      if (!text) return true;
      return `${item.service} ${item.market} ${item.status}`.toLowerCase().includes(text);
    });
    return rows.sort((a, b) => {
      if (sort === "score") return (b.score || 0) - (a.score || 0);
      if (sort === "updated") return String(b.updated_at).localeCompare(String(a.updated_at));
      return a.service.localeCompare(b.service);
    });
  }, [opportunities, query, sort]);

  const artifacts = detail?.artifacts || [];
  const scan = artifactByKind(artifacts, "scan_result") || artifactByKind(artifacts, "preliminary_assessment");
  const report = artifactByKind(artifacts, "discovery_report") || scan?.discovery_report || null;
  const components = scan?.score?.component_scores || {};
  const demandEvidence = report?.demand || scan?.demand_evidence || null;
  const providers = scan?.providers || [];
  const competitors = scan?.competitors || [];
  const keywords = scan?.metrics || [];
  const keywordDecisions = detail?.keyword_decisions || scan?.keyword_decisions || [];
  const keywordClusters = detail?.keyword_clusters || scan?.keyword_clusters || [];
  const activeScanCount = scans.filter((item) => ["queued", "running"].includes(item.status)).length;

  return (
    <main className="shell">
      <section className="topbar">
        <div>
          <p className="eyebrow">Local-first research engine</p>
          <h1>Digital Real Estate Engine</h1>
        </div>
        <div className="topActions">
          <button className="ghostButton" onClick={() => refresh(false)}>
            <RefreshCw size={16} />
            Refresh
          </button>
          <a className="ghostButton" href="/api/backend/healthz" target="_blank">
            <Activity size={16} />
            Backend
            <ExternalLink size={14} />
          </a>
        </div>
      </section>

      {meta?.synthetic_fixture_data && (
        <section className="fixtureBanner">
          <AlertTriangle size={18} />
          <strong>Synthetic fixture data</strong>
          <span>
            Fixture mode uses deterministic test data for scores, SERPs, providers, domains, and
            outreach. Do not treat it as live market evidence.
          </span>
        </section>
      )}
      {meta?.dataforseo_sandbox && meta?.data_mode === "live" && (
        <section className="fixtureBanner sandbox">
          <AlertTriangle size={18} />
          <strong>DataForSEO sandbox</strong>
          <span>
            Live mode is using free sandbox responses. API plumbing is real, but returned market
            data may be mock or unrelated.
          </span>
        </section>
      )}
      {!meta?.dataforseo_sandbox && meta?.data_mode === "live" && meta?.live_api_calls_allowed && (
        <section className="fixtureBanner production">
          <AlertTriangle size={18} />
          <strong>Production API spend enabled</strong>
          <span>Real DataForSEO production calls may consume credits when scans are confirmed.</span>
        </section>
      )}

      <section className="scanBand">
        <form onSubmit={submitScan} className="scanForm">
          <label>
            <span>Service</span>
            <input
              value={form.service_text}
              onChange={(event) => setForm({ ...form, service_text: event.target.value })}
            />
          </label>
          <label>
            <span>Location</span>
            <div className="locationPicker">
              <div className="locationInputWrap">
                <input
                  value={form.location_text}
                  onFocus={() => {
                    setLocationOpen(true);
                  }}
                  onChange={(event) => updateLocationText(event.target.value)}
                  placeholder="City, state, ZIP, or country"
                />
                <button
                  type="button"
                  className="locationToggle"
                  onClick={() => setLocationOpen(!locationOpen)}
                  aria-label="Show location options"
                >
                  <ChevronDown size={16} />
                </button>
                {locationLoading && <Loader2 className="locationSpinner spin" size={15} />}
              </div>
              {selectedLocation && (
                <div className="selectedLocation">
                  <MapPin size={14} />
                  <span>{selectedLocation.source}</span>
                  <span>{Math.round((selectedLocation.confidence || 0) * 100)}%</span>
                </div>
              )}
              {locationOpen && (
                <div className="locationMenu">
                  {locationOptions.length === 0 ? (
                    <div className="locationEmpty">
                      {locationLoading
                        ? "Searching locations..."
                        : "No local matches yet. Try City, ST or a ZIP code."}
                    </div>
                  ) : (
                    locationOptions.map((option) => (
                      <button
                        type="button"
                        key={`${option.source}-${option.id}`}
                        className="locationOption"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => chooseLocation(option)}
                      >
                        <span>
                          <strong>{option.label}</strong>
                          <small>
                            {option.type} · {option.source} · {option.match_reason}
                          </small>
                        </span>
                        <span>{Math.round((option.confidence || 0) * 100)}%</span>
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          </label>
          <label className="countryField">
            <span>Country</span>
            <input
              value={form.country}
              onChange={(event) => {
                setSelectedLocation(null);
                setForm({ ...form, country: event.target.value.toUpperCase() });
              }}
            />
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={form.dry_run}
              onChange={(event) => setForm({ ...form, dry_run: event.target.checked })}
            />
            <span>Dry run</span>
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={form.async_run}
              disabled={form.dry_run}
              onChange={(event) => setForm({ ...form, async_run: event.target.checked })}
            />
            <span>Background</span>
          </label>
          {meta?.data_mode === "live" && !form.dry_run && (
            <label className="toggle costToggle">
              <input
                type="checkbox"
                checked={form.confirm_live_cost}
                onChange={(event) =>
                  setForm({ ...form, confirm_live_cost: event.target.checked })
                }
              />
              <span>Confirm cost</span>
            </label>
          )}
          <button className="primaryButton" disabled={scanLoading}>
            {scanLoading ? <Loader2 className="spin" size={17} /> : <Play size={17} />}
            {form.dry_run ? "Dry Run" : form.async_run ? "Queue Scan" : "Run Scan"}
          </button>
        </form>
        {lastPlan && (
          <>
            <div className="planStrip">
              <span>{lastPlan.planned_calls?.length || 0} calls</span>
              <span>{money(lastPlan.estimated_uncached_cost_usd)} uncached</span>
              <span>{money(lastPlan.cached_cost_usd)} cached</span>
              <span>{lastPlan.blocked ? "blocked" : lastPlan.scan_profile}</span>
            </div>
            <div className="plannedCalls">
              {(lastPlan.planned_calls || []).map((call) => (
                <span key={`${call.stage}-${call.endpoint}`}>
                  <strong>{call.stage}</strong>
                  <small>{call.cache_hit ? "cache hit" : call.request_known ? "uncached" : "estimated"}</small>
                  <small>{money(call.estimated_cost_usd)}</small>
                </span>
              ))}
            </div>
          </>
        )}
        {scans.length > 0 && (
          <div className="scanTimeline">
            {scans.slice(0, 4).map((item) => (
              <span key={item.id} className={`scanChip ${item.status}`}>
                #{item.id} {item.status} {money(item.actual_cost_usd || item.estimated_cost_usd)}
                {["queued", "running"].includes(item.status) && (
                  <button type="button" onClick={() => cancelScan(item.id)}>
                    Cancel
                  </button>
                )}
                {["failed", "cancelled"].includes(item.status) && (
                  <button type="button" onClick={() => retryScan(item.id)}>
                    Retry
                  </button>
                )}
              </span>
            ))}
          </div>
        )}
        {notice && (
          <div className={`notice ${notice.type}`}>
            {notice.type === "error" ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}
            {notice.message}
          </div>
        )}
      </section>

      <section className="statsGrid">
        <Metric icon={Database} label="Opportunities" value={opportunities.length} />
        <Metric icon={Activity} label="Active scans" value={activeScanCount} />
        <Metric icon={ShieldCheck} label="Data mode" value={meta?.data_mode || "fixture"} />
        <Metric
          icon={Gauge}
          label="Best score"
          value={number(Math.max(0, ...opportunities.map((item) => item.score || 0)))}
        />
        <Metric
          icon={ShieldCheck}
          label="Scan depth"
          value={meta?.live_scan_depth || "testing"}
        />
        <Metric icon={Database} label="Raw responses" value={audit?.raw_response_count || 0} />
      </section>

      <section className="workspace">
        <aside className="opportunityPane">
          <div className="paneHeader">
            <div>
              <h2>Opportunities</h2>
              <p>{filtered.length} visible</p>
            </div>
            <ArrowDownUp size={18} />
          </div>
          <div className="listControls">
            <label className="searchBox">
              <Search size={16} />
              <input
                placeholder="Filter service, market, status"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <select value={sort} onChange={(event) => setSort(event.target.value)}>
              <option value="score">Score</option>
              <option value="updated">Updated</option>
              <option value="service">Service</option>
            </select>
          </div>
          <div className="opportunityList">
            {loading ? (
              <div className="emptyState">Loading opportunities...</div>
            ) : filtered.length === 0 ? (
              <div className="emptyState">Run a scan to create your first opportunity.</div>
            ) : (
              filtered.map((item) => (
                <button
                  key={item.id}
                  className={`opportunityRow ${selectedId === item.id ? "active" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                >
                  <span>
                    <strong>{item.service}</strong>
                    <small>
                      <MapPin size={13} />
                      {item.market}
                    </small>
                  </span>
                  <ScorePill score={item.score} confidence={item.confidence} />
                </button>
              ))
            )}
          </div>
        </aside>

        <section className="detailPane">
          {!detail && !detailLoading ? (
            <div className="blankDetail">
              <Sparkles size={28} />
              <h2>Select an opportunity</h2>
              <p>Scores, demand, SERP composition, providers, and competitor evidence will appear here.</p>
            </div>
          ) : detailLoading ? (
            <div className="blankDetail">
              <Loader2 className="spin" size={28} />
              <h2>Loading detail</h2>
            </div>
          ) : (
            <>
              <div className="detailHero">
                <div>
                  <p className="eyebrow">Opportunity #{detail.opportunity.id}</p>
                  <h2>
                    {detail.opportunity.service} in {detail.opportunity.market}
                  </h2>
                  <p>{scan?.score?.explanation || "No score explanation has been saved yet."}</p>
                  {detail.data_mode === "fixture" && (
                    <p className="syntheticNote">
                      Synthetic fixture data. Do not use as live market evidence.
                    </p>
                  )}
                  {scan?.assessment_type === "preliminary" && (
                    <p className="syntheticNote">
                      Preliminary testing assessment. It is not comparable to a full opportunity score.
                    </p>
                  )}
                  {demandEvidence?.warning && (
                    <p className="syntheticNote">{demandEvidence.warning}</p>
                  )}
                </div>
                <div className="heroActions">
                  <ScoreDial score={detail.opportunity.score} confidence={detail.opportunity.confidence} />
                  <button className="ghostButton" onClick={rescoreSelected}>
                    <RefreshCw size={16} />
                    Rescore
                  </button>
                </div>
              </div>

              <div className="componentGrid">
                <ComponentScore label="Demand Evidence" value={components.demand_evidence} max={24} />
                <ComponentScore label="Commercial Value" value={components.commercial_value} max={16} />
                <ComponentScore label="Competitor Weakness" value={components.competitor_weakness} max={22} />
                <ComponentScore label="Click Availability" value={components.organic_click_availability} max={16} />
                <ComponentScore label="Provider Fit" value={components.provider_suitability} max={14} />
                <ComponentScore label="Completeness" value={components.data_completeness} max={8} />
              </div>

              <div className="detailGrid">
                <Panel title="Market Interpretation" icon={MapPin}>
                  <Table
                    columns={["Field", "Value"]}
                    rows={[
                      ["Market", report?.market_interpretation?.input_market || detail.opportunity.market],
                      ["Type", report?.market_interpretation?.market_type || "unknown"],
                      ["Provider location", report?.market_interpretation?.provider_location_name || "n/a"],
                      ["Data mode", detail.latest_scan?.data_mode || detail.data_mode || "fixture"],
                      ["Actual API cost", money(detail.latest_scan?.actual_cost_usd || 0)]
                    ]}
                  />
                </Panel>
                <Panel title="Keyword Demand" icon={Search}>
                  <Table
                    columns={["Keyword", "Intent", "Volume", "Granularity", "CPC"]}
                    rows={keywords.slice(0, 8).map((item) => [
                      item.keyword,
                      item.intent,
                      item.search_volume,
                      item.market_granularity || "unknown",
                      `$${item.cpc}`
                    ])}
                  />
                  {demandEvidence && (
                    <p className="muted">
                      Raw granularity: {demandEvidence.raw_volume_granularity || demandEvidence.provider_reported_metric_granularity}.
                      Local estimate: {demandEvidence.market_estimation_method}.
                    </p>
                  )}
                </Panel>
                <Panel title="Keyword Decisions" icon={ShieldCheck}>
                  <Table
                    columns={["Keyword", "Decision", "Rank", "Reason"]}
                    rows={keywordDecisions.slice(0, 10).map((item) => [
                      item.keyword,
                      item.representative ? "SERP representative" : item.decision,
                      item.rank || "n/a",
                      item.ranking_score ? `${item.reason || "n/a"} (${item.ranking_score})` : item.reason || "n/a"
                    ])}
                  />
                  {keywordClusters.length > 0 && (
                    <p className="muted">
                      {keywordClusters.length} close-variant clusters saved. Cluster demand uses
                      the representative volume rather than blindly summing variants.
                    </p>
                  )}
                </Panel>
                <Panel title="SERP Composition" icon={Globe2}>
                  <Table
                    columns={["Type", "Count"]}
                    rows={Object.entries(report?.serp_composition?.classification_counts || {}).map(
                      ([label, value]) => [label, value]
                    )}
                  />
                </Panel>
                <Panel title="Provider Suitability" icon={Building2}>
                  <Table
                    columns={["Provider", "Score", "Contact"]}
                    rows={providers.map((item) => [
                      item.name,
                      item.suitability_score || "n/a",
                      item.website ? "website" : item.phone ? "phone" : "unknown"
                    ])}
                  />
                </Panel>
                <Panel title="Top Competitors" icon={CircleDollarSign}>
                  <Table
                    columns={["Domain", "Ref domains", "Type"]}
                    rows={competitors.slice(0, 6).map((item) => [
                      item.domain,
                      item.referring_domains,
                      item.page_type
                    ])}
                  />
                </Panel>
                <Panel title="Score Evidence" icon={FileText} wide>
                  <Table
                    columns={["Component", "Explanation"]}
                    rows={Object.entries(scan?.score?.component_explanations || {}).map(
                      ([label, value]) => [label, value]
                    )}
                  />
                </Panel>
              </div>
            </>
          )}
        </section>
      </section>
    </main>
  );
}

function Metric({ icon: Icon, label, value }) {
  return (
    <article className="metric">
      <Icon size={20} />
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function ScorePill({ score, confidence }) {
  return (
    <span className="scorePill">
      <strong>{number(score)}</strong>
      <small>{confidence || "n/a"}</small>
    </span>
  );
}

function ScoreDial({ score, confidence }) {
  const value = Math.max(0, Math.min(100, score || 0));
  return (
    <div className="scoreDial" style={{ "--score": `${value * 3.6}deg` }}>
      <div>
        <strong>{number(score)}</strong>
        <span>{confidence || "n/a"}</span>
      </div>
    </div>
  );
}

function ComponentScore({ label, value, max }) {
  const pct = Math.max(0, Math.min(100, ((value || 0) / max) * 100));
  return (
    <article className="component">
      <span>
        {label}
        <strong>
          {number(value)} / {max}
        </strong>
      </span>
      <div className="bar">
        <i style={{ width: `${pct}%` }} />
      </div>
    </article>
  );
}

function Panel({ title, icon: Icon, children, wide = false }) {
  return (
    <section className={`panel ${wide ? "wide" : ""}`}>
      <header>
        <Icon size={18} />
        <h3>{title}</h3>
      </header>
      {children}
    </section>
  );
}

function Table({ columns, rows }) {
  if (!rows.length) return <p className="muted">No data saved yet.</p>;
  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
