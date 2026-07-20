"use client";

import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowDownUp,
  BarChart3,
  Building2,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Database,
  ExternalLink,
  FileText,
  Gauge,
  GitCompareArrows,
  Globe2,
  History,
  Info,
  Layers3,
  Loader2,
  MapPin,
  Play,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  X
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

const COMPONENT_MAXIMUMS = {
  demand_evidence: 24,
  commercial_value: 16,
  competitor_weakness: 22,
  organic_click_availability: 16,
  provider_suitability: 14,
  data_completeness: 8
};

async function request(path, options) {
  const response = await fetch(`/api/backend${path}`, {
    cache: "no-store",
    ...options,
    headers: options?.body
      ? { "Content-Type": "application/json", ...options?.headers }
      : options?.headers
  });
  if (response.ok) return response.json();
  let message = `Request failed (${response.status})`;
  try {
    const payload = await response.json();
    message =
      typeof payload.detail === "string"
        ? payload.detail
        : payload.detail?.message || payload.message || message;
  } catch {
    // Keep the HTTP fallback for non-JSON responses.
  }
  throw new Error(message);
}

const api = {
  getMeta: () => request("/api/meta"),
  getServices: () => request("/api/services"),
  listOpportunities: () => request("/api/opportunities"),
  getOpportunity: (id) => request(`/api/opportunities/${id}`),
  getAudit: () => request("/api/data/audit"),
  listScans: () => request("/api/scans"),
  searchLocations(query, country) {
    const params = new URLSearchParams({ q: query, country, limit: "8" });
    return request(`/api/locations/search?${params}`);
  },
  prefilterMarkets: (payload) =>
    request("/api/market-prefilter", { method: "POST", body: JSON.stringify(payload) }),
  runScan: (payload) =>
    request("/api/scans", { method: "POST", body: JSON.stringify(payload) }),
  cancelScan: (id) => request(`/api/scans/${id}/cancel`, { method: "POST" }),
  retryScan: (id) => request(`/api/scans/${id}/retry`, { method: "POST" }),
  rescoreOpportunity: (id, reason) =>
    request(`/api/opportunities/${id}/rescore`, {
      method: "POST",
      body: JSON.stringify({ reason })
    }),
  promoteOpportunity: (id, payload) =>
    request(`/api/opportunities/${id}/promote`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  compare(ids) {
    return request(`/api/opportunities/compare?${new URLSearchParams({ ids: ids.join(",") })}`);
  }
};

function number(value, fallback = "n/a") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return fallback;
  return Number(value).toFixed(1);
}

function integer(value, fallback = "n/a") {
  if (value === null || value === undefined) return fallback;
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function money(value) {
  return `$${Number(value || 0).toFixed(3)}`;
}

function dateTime(value, fallback = "Unknown") {
  if (!value) return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(date);
}

function componentLabel(value = "") {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function signedPoints(value) {
  if (value === null || value === undefined) return "n/a";
  const points = Number(value);
  return `${points >= 0 ? "+" : ""}${points.toFixed(2)}`;
}

function artifactByKind(artifacts, kind) {
  return artifacts?.find((artifact) => artifact.kind === kind)?.payload;
}

function normalizeService(record) {
  const service = record?.service ? { ...record.service, ...record } : record;
  if (!service) return null;
  return {
    ...service,
    id: service.id,
    aliases: service.aliases || record?.aliases || [],
    configured: service.configured ?? record?.configured ?? true
  };
}

function assessmentScore(opportunity) {
  const assessment = opportunity?.latest_assessment;
  return assessment?.rankable ? assessment.score?.total_score : null;
}

function qualityTone(status) {
  if (status === "pass" || status === "fresh" || status === "completed") return "good";
  if (status === "fail" || status === "stale" || status === "failed") return "bad";
  if (status === "warning" || status === "aging") return "warn";
  return "neutral";
}

function ScanOption({ checked, disabled = false, label, help, onChange }) {
  return (
    <div className={`optionToggle${disabled ? " disabled" : ""}`}>
      <label className="optionChoice">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="checkboxVisual" aria-hidden="true">
          <Check size={13} strokeWidth={3} />
        </span>
        <span className="optionLabel">{label}</span>
      </label>
      <InfoTip label={label} help={help} />
    </div>
  );
}

function InfoTip({ label, help }) {
  return (
    <button
      type="button"
      className="infoTip"
      aria-label={`${label}: ${help}`}
      data-tooltip={help}
    >
      <Info size={14} />
    </button>
  );
}

function SegmentedControl({ label, value, options, onChange }) {
  return (
    <fieldset className="segmentedField">
      <legend>{label}</legend>
      <div className="segmentedControl">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={value === option.value ? "active" : ""}
            aria-pressed={value === option.value}
            onClick={() => onChange(option.value)}
          >
            {option.icon && <option.icon size={15} />}
            {option.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function ServicePicker({
  services,
  catalogVersion,
  query,
  selectedService,
  onQueryChange,
  onSelect
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const normalized = query.trim().toLowerCase();
  const matches = services.filter((service) => {
    if (!normalized) return true;
    return [service.display_name, service.slug, ...(service.aliases || [])]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(normalized));
  });
  const exactMatch = services.some((service) =>
    [service.display_name, service.slug, ...(service.aliases || [])]
      .filter(Boolean)
      .some((value) => value.toLowerCase() === normalized)
  );

  useEffect(() => {
    function dismiss(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  return (
    <label className="serviceField">
      <span className="fieldHeading">
        Service
        <small>Catalog {catalogVersion || "unavailable"}</small>
      </span>
      <div className="comboPicker" ref={rootRef}>
        <div className="comboInput">
          <Search size={15} />
          <input
            role="combobox"
            aria-expanded={open}
            aria-controls="service-options"
            aria-autocomplete="list"
            value={query}
            placeholder="Search configured services"
            onFocus={() => setOpen(true)}
            onChange={(event) => {
              onQueryChange(event.target.value);
              setOpen(true);
            }}
          />
          <button type="button" aria-label="Show service options" onClick={() => setOpen(!open)}>
            <ChevronDown size={16} />
          </button>
        </div>
        <div className={`serviceMode ${selectedService ? "configured" : "draft"}`}>
          {selectedService ? <ShieldCheck size={13} /> : <AlertCircle size={13} />}
          <span>
            {selectedService
              ? `Configured: ${selectedService.seed_queries?.length || 0} seeds, ${selectedService.provider_categories?.length || 0} provider categories`
              : query.trim()
                ? "Unconfigured draft: generic discovery rules"
                : "Select an authoritative service"}
          </span>
        </div>
        {open && (
          <div className="comboMenu" id="service-options" role="listbox">
            {matches.map((service) => (
              <button
                type="button"
                role="option"
                aria-selected={selectedService?.id === service.id}
                key={service.id}
                className="serviceOption"
                onClick={() => {
                  onSelect(service);
                  setOpen(false);
                }}
              >
                <span>
                  <strong>{service.display_name}</strong>
                  <small>{service.description || (service.aliases || []).slice(0, 2).join(" · ")}</small>
                </span>
                {selectedService?.id === service.id && <Check size={16} />}
              </button>
            ))}
            {query.trim() && !exactMatch && (
              <button
                type="button"
                role="option"
                aria-selected={!selectedService}
                className="serviceOption draftOption"
                onClick={() => {
                  onSelect(null);
                  setOpen(false);
                }}
              >
                <AlertTriangle size={16} />
                <span>
                  <strong>Use “{query.trim()}” as an unconfigured draft</strong>
                  <small>One generic seed and no authoritative provider categories</small>
                </span>
              </button>
            )}
            {!matches.length && !query.trim() && (
              <div className="comboEmpty">No configured services are available.</div>
            )}
          </div>
        )}
      </div>
    </label>
  );
}

function LocationPicker({
  form,
  setForm,
  selectedLocation,
  setSelectedLocation,
  options,
  setOptions,
  loading,
  open,
  setOpen,
  pickerRef,
  dismissedRef
}) {
  function updateText(value) {
    setSelectedLocation(null);
    dismissedRef.current = false;
    if (value.trim().length < 2) {
      setOptions([]);
      setOpen(false);
    } else {
      setOpen(true);
    }
    setForm((current) => ({ ...current, location_text: value }));
  }

  function choose(option) {
    setSelectedLocation(option);
    setOpen(false);
    setOptions([]);
    setForm((current) => ({
      ...current,
      location_text: option.label,
      country: option.country || current.country
    }));
  }

  return (
    <label>
      <span className="fieldHeading">Location</span>
      <div className="locationPicker" ref={pickerRef}>
        <div className="locationInputWrap">
          <input
            value={form.location_text}
            onFocus={() => {
              dismissedRef.current = false;
              setOpen(true);
            }}
            onChange={(event) => updateText(event.target.value)}
            placeholder="U.S. city, state, or ZIP"
          />
          <button
            type="button"
            className="locationToggle"
            onClick={() => {
              dismissedRef.current = open;
              setOpen(!open);
            }}
            aria-label="Show location options"
          >
            <ChevronDown size={16} />
          </button>
          {loading && <Loader2 className="locationSpinner spin" size={15} />}
        </div>
        {selectedLocation && (
          <div className="selectedLocation">
            <MapPin size={13} />
            <span>{selectedLocation.county || "Resolved geography"}</span>
            <span>Pop. {integer(selectedLocation.population)}</span>
          </div>
        )}
        {open && (
          <div className="locationMenu">
            {!options.length ? (
              <div className="locationEmpty">
                {loading ? "Searching locations..." : "Type at least two characters to search."}
              </div>
            ) : (
              options.map((option) => (
                <button
                  type="button"
                  key={`${option.source}-${option.id}`}
                  className="locationOption"
                  onClick={() => choose(option)}
                >
                  <span>
                    <strong>{option.label}</strong>
                    <small>{[option.county, option.metro].filter(Boolean).join(" · ")}</small>
                  </span>
                  <span>Pop. {integer(option.population)}</span>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    </label>
  );
}

export default function DiscoveryDashboard() {
  const [opportunities, setOpportunities] = useState([]);
  const [scans, setScans] = useState([]);
  const [audit, setAudit] = useState(null);
  const [meta, setMeta] = useState(null);
  const [services, setServices] = useState([]);
  const [catalogVersion, setCatalogVersion] = useState(null);
  const [selectedService, setSelectedService] = useState(null);
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
  const [prefilterLoading, setPrefilterLoading] = useState(false);
  const [prefilterResult, setPrefilterResult] = useState(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("score");
  const [compareIds, setCompareIds] = useState([]);
  const [compareData, setCompareData] = useState(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [detailTab, setDetailTab] = useState("evidence");
  const [promotionPreview, setPromotionPreview] = useState(null);
  const [promotionLoading, setPromotionLoading] = useState(false);
  const [rescoreOpen, setRescoreOpen] = useState(false);
  const [rescoreReason, setRescoreReason] = useState("");
  const locationPickerRef = useRef(null);
  const locationDismissedRef = useRef(false);
  const [form, setForm] = useState({
    service_text: "",
    location_text: "",
    country: "US",
    scan_profile: "testing",
    dry_run: true,
    async_run: true,
    confirm_live_cost: false
  });

  async function refresh(selectFirst = false) {
    setLoading(true);
    try {
      const [metaData, serviceData, opportunityData, auditData, scanData] =
        await Promise.all([
          api.getMeta(),
          api.getServices().catch(() => ({ catalog_version: null, services: [] })),
          api.listOpportunities(),
          api.getAudit(),
          api.listScans()
        ]);
      const normalizedServices = (serviceData.services || []).map(normalizeService).filter(Boolean);
      setMeta(metaData);
      setCatalogVersion(serviceData.catalog_version);
      setServices(normalizedServices);
      setOpportunities(opportunityData.opportunities || []);
      setAudit(auditData);
      setScans(scanData.scans || []);
      if (selectFirst && opportunityData.opportunities?.[0]) {
        setSelectedId(opportunityData.opportunities[0].id);
      }
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setLoading(false);
    }
  }

  async function reloadDetail(id = selectedId) {
    if (!id) return;
    const data = await api.getOpportunity(id);
    setDetail(data);
    setOpportunities((current) =>
      current.map((item) =>
        item.id === id
          ? { ...item, ...data.opportunity, latest_assessment: data.latest_assessment }
          : item
      )
    );
  }

  useEffect(() => {
    const timer = window.setTimeout(() => refresh(true), 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const active = scans.some((scan) => ["queued", "running"].includes(scan.status));
    if (!active) return undefined;
    const timer = setInterval(() => refresh(false), 4000);
    return () => clearInterval(timer);
  }, [scans]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setDetailLoading(true);
      setPromotionPreview(null);
      setRescoreOpen(false);
      api
        .getOpportunity(selectedId)
        .then((data) => {
          if (!cancelled) setDetail(data);
        })
        .catch((error) => {
          if (!cancelled) setNotice({ type: "error", message: error.message });
        })
        .finally(() => {
          if (!cancelled) setDetailLoading(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [selectedId]);

  useEffect(() => {
    function dismiss(event) {
      if (locationPickerRef.current?.contains(event.target)) return;
      locationDismissedRef.current = true;
      setLocationOpen(false);
    }
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  useEffect(() => {
    const text = form.location_text.trim();
    if (selectedLocation && selectedLocation.label === text) return undefined;
    if (text.length < 2) return undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      setLocationLoading(true);
      api
        .searchLocations(text, form.country)
        .then((result) => {
          if (cancelled) return;
          setLocationOptions(result.locations || []);
          if (!locationDismissedRef.current) setLocationOpen(true);
        })
        .catch(() => {
          if (!cancelled) setLocationOptions([]);
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

  async function submitScan(event) {
    event.preventDefault();
    if (!form.service_text.trim() || !form.location_text.trim()) {
      setNotice({ type: "error", message: "Select a service and resolved location first." });
      return;
    }
    setScanLoading(true);
    setNotice(null);
    try {
      const result = await api.runScan({
        ...form,
        service_id: selectedService?.id || null,
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

  async function runPrefilter() {
    if (!form.service_text.trim()) {
      setNotice({ type: "error", message: "Select a service before finding markets." });
      return;
    }
    setPrefilterLoading(true);
    try {
      const result = await api.prefilterMarkets({
        service_text: form.service_text.trim(),
        geography_kind: "city",
        limit: 12
      });
      setPrefilterResult(result);
      setNotice({
        type: "success",
        message: `Ranked ${integer(result.candidate_count)} markets with zero paid API calls.`
      });
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setPrefilterLoading(false);
    }
  }

  function selectPrefilterMarket(assessment) {
    const location = assessment.location;
    setSelectedLocation(location);
    setLocationOptions([location]);
    setLocationOpen(false);
    setForm((current) => ({ ...current, location_text: location.label }));
    setNotice({ type: "success", message: `${location.label} selected from the shortlist.` });
  }

  async function scanAction(action, id) {
    try {
      const result = action === "cancel" ? await api.cancelScan(id) : await api.retryScan(id);
      setNotice({ type: "success", message: result.message });
      await refresh(false);
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    }
  }

  async function previewPromotion() {
    setPromotionLoading(true);
    try {
      setPromotionPreview(await api.promoteOpportunity(selectedId, { dry_run: true }));
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setPromotionLoading(false);
    }
  }

  async function confirmPromotion() {
    setPromotionLoading(true);
    try {
      const result = await api.promoteOpportunity(selectedId, {
        dry_run: false,
        confirm_live_cost: true
      });
      setNotice({ type: "success", message: result.message });
      setPromotionPreview(null);
      await refresh(false);
      await reloadDetail();
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setPromotionLoading(false);
    }
  }

  async function submitRescore(event) {
    event.preventDefault();
    if (rescoreReason.trim().length < 3) return;
    setDetailLoading(true);
    try {
      const result = await api.rescoreOpportunity(selectedId, rescoreReason.trim());
      setNotice({
        type: "success",
        message: `Rescored with a ${signedPoints(result.diff?.total_delta)} point change.`
      });
      setRescoreOpen(false);
      setRescoreReason("");
      await refresh(false);
      await reloadDetail();
      setDetailTab("history");
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setDetailLoading(false);
    }
  }

  function toggleCompare(opportunity) {
    if (!opportunity.latest_assessment?.rankable) return;
    setCompareData(null);
    setCompareIds((current) =>
      current.includes(opportunity.id)
        ? current.filter((id) => id !== opportunity.id)
        : current.length < 4
          ? [...current, opportunity.id]
          : current
    );
  }

  async function runComparison() {
    if (compareIds.length < 2) return;
    setCompareLoading(true);
    try {
      setCompareData(await api.compare(compareIds));
    } catch (error) {
      setNotice({ type: "error", message: error.message });
    } finally {
      setCompareLoading(false);
    }
  }

  const filtered = useMemo(() => {
    const text = query.trim().toLowerCase();
    const rows = opportunities.filter((item) =>
      !text
        ? true
        : `${item.service} ${item.market} ${item.status}`.toLowerCase().includes(text)
    );
    return [...rows].sort((a, b) => {
      if (sort === "score") {
        const aScore = assessmentScore(a);
        const bScore = assessmentScore(b);
        if (aScore === null && bScore !== null) return 1;
        if (bScore === null && aScore !== null) return -1;
        return (bScore || 0) - (aScore || 0);
      }
      if (sort === "updated") return String(b.updated_at).localeCompare(String(a.updated_at));
      return a.service.localeCompare(b.service);
    });
  }, [opportunities, query, sort]);

  const assessment = detail?.latest_assessment;
  const report = assessment?.report || null;
  const score = assessment?.score || null;
  const quality = assessment?.evidence_quality || {};
  const freshness = assessment?.freshness || {};
  const components = report?.score_breakdown?.components || score?.component_scores || {};
  const componentDetails =
    report?.score_breakdown?.component_details || score?.component_details || {};
  const artifacts = detail?.artifacts || [];
  const scanPayload =
    artifactByKind(artifacts, assessment?.artifact_kind) ||
    artifactByKind(artifacts, "scan_result") ||
    artifactByKind(artifacts, "preliminary_assessment") ||
    {};
  const providers = scanPayload.providers || [];
  const competitors = scanPayload.competitors || [];
  const keywords = scanPayload.metrics || [];
  const demandEvidence = report?.demand || scanPayload.demand_evidence;
  const activeScanCount = scans.filter((scan) => ["queued", "running"].includes(scan.status)).length;
  const bestScore = Math.max(
    0,
    ...opportunities.map((opportunity) => assessmentScore(opportunity) || 0)
  );

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Discovery operations</p>
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
      </header>

      <EnvironmentBanner meta={meta} />

      <section className="scanBand" aria-label="New discovery scan">
        <form onSubmit={submitScan} className="scanForm">
          <ServicePicker
            services={services}
            catalogVersion={catalogVersion}
            query={form.service_text}
            selectedService={selectedService}
            onQueryChange={(value) => {
              setSelectedService(null);
              setPrefilterResult(null);
              setForm((current) => ({ ...current, service_text: value }));
            }}
            onSelect={(service) => {
              setSelectedService(service);
              setPrefilterResult(null);
              if (service) {
                setForm((current) => ({ ...current, service_text: service.display_name }));
              }
            }}
          />
          <LocationPicker
            form={form}
            setForm={setForm}
            selectedLocation={selectedLocation}
            setSelectedLocation={setSelectedLocation}
            options={locationOptions}
            setOptions={setLocationOptions}
            loading={locationLoading}
            open={locationOpen}
            setOpen={setLocationOpen}
            pickerRef={locationPickerRef}
            dismissedRef={locationDismissedRef}
          />
          <SegmentedControl
            label="Scan profile"
            value={form.scan_profile}
            onChange={(scan_profile) => {
              setLastPlan(null);
              setForm((current) => ({ ...current, scan_profile }));
            }}
            options={[
              { value: "testing", label: "Testing", icon: Gauge },
              { value: "full", label: "Full", icon: Layers3 }
            ]}
          />
          <div className="scanOptions" aria-label="Execution options">
            <ScanOption
              checked={form.dry_run}
              label="Dry run"
              help="Build and price the exact plan without making network calls or saving results."
              onChange={(dry_run) => setForm((current) => ({ ...current, dry_run }))}
            />
            <ScanOption
              checked={form.async_run}
              disabled={form.dry_run}
              label="Background"
              help="Queue the scan so it continues while you work elsewhere in the dashboard."
              onChange={(async_run) => setForm((current) => ({ ...current, async_run }))}
            />
            {meta?.data_mode === "live" && !form.dry_run && (
              <ScanOption
                checked={form.confirm_live_cost}
                label="Confirm cost"
                help="Approve the displayed uncached API estimate before this scan is sent."
                onChange={(confirm_live_cost) =>
                  setForm((current) => ({ ...current, confirm_live_cost }))
                }
              />
            )}
          </div>
          <div className="scanCommands">
            <button className="primaryButton" disabled={scanLoading}>
              {scanLoading ? <Loader2 className="spin" size={17} /> : <Play size={17} />}
              {form.dry_run ? "Preview plan" : form.async_run ? "Queue scan" : "Run scan"}
            </button>
            <button
              className="secondaryButton"
              type="button"
              disabled={prefilterLoading}
              onClick={runPrefilter}
            >
              {prefilterLoading ? <Loader2 className="spin" size={17} /> : <Search size={17} />}
              Find markets
            </button>
          </div>
        </form>
        {lastPlan && <PlanPreview plan={lastPlan} />}
        {scans.length > 0 && (
          <div className="scanTimeline">
            {scans.slice(0, 4).map((scan) => (
              <span key={scan.id} className={`scanChip ${scan.status}`}>
                #{scan.id} · {scan.scan_profile} · {scan.status} ·{" "}
                {money(scan.actual_cost_usd || scan.estimated_cost_usd)}
                {["queued", "running"].includes(scan.status) && (
                  <button type="button" onClick={() => scanAction("cancel", scan.id)}>
                    Cancel
                  </button>
                )}
                {["failed", "cancelled"].includes(scan.status) && (
                  <button type="button" onClick={() => scanAction("retry", scan.id)}>
                    Retry
                  </button>
                )}
              </span>
            ))}
          </div>
        )}
        {notice && <Notice notice={notice} onDismiss={() => setNotice(null)} />}
      </section>

      {prefilterResult && (
        <PrefilterResults result={prefilterResult} onSelect={selectPrefilterMarket} />
      )}

      <section className="statsGrid">
        <Metric icon={Database} label="Opportunities" value={opportunities.length} />
        <Metric icon={Activity} label="Active scans" value={activeScanCount} />
        <Metric icon={ShieldCheck} label="Data mode" value={meta?.data_mode || "unknown"} />
        <Metric icon={Gauge} label="Best full score" value={number(bestScore)} />
        <Metric icon={Layers3} label="Configured services" value={services.length} />
        <Metric icon={Database} label="Raw responses" value={audit?.raw_response_count || 0} />
      </section>

      {compareIds.length > 0 && (
        <CompareTray
          ids={compareIds}
          opportunities={opportunities}
          loading={compareLoading}
          onCompare={runComparison}
          onClear={() => {
            setCompareIds([]);
            setCompareData(null);
          }}
        />
      )}

      {compareData && (
        <ComparisonWorkspace
          data={compareData}
          onClose={() => setCompareData(null)}
          onOpen={(id) => {
            setSelectedId(id);
            setCompareData(null);
          }}
        />
      )}

      <section className="workspace">
        <aside className="opportunityPane">
          <div className="paneHeader">
            <div>
              <h2>Opportunities</h2>
              <p>{filtered.length} visible · full scores first</p>
            </div>
            <ArrowDownUp size={18} />
          </div>
          <div className="listControls">
            <label className="searchBox">
              <Search size={16} />
              <input
                placeholder="Filter service or market"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <select value={sort} onChange={(event) => setSort(event.target.value)}>
              <option value="score">Full score</option>
              <option value="updated">Updated</option>
              <option value="service">Service</option>
            </select>
          </div>
          <div className="opportunityList">
            {loading ? (
              <div className="emptyState">Loading opportunities...</div>
            ) : !filtered.length ? (
              <div className="emptyState">Run a scan to create your first opportunity.</div>
            ) : (
              filtered.map((opportunity) => (
                <OpportunityRow
                  key={opportunity.id}
                  opportunity={opportunity}
                  active={selectedId === opportunity.id}
                  selectedForCompare={compareIds.includes(opportunity.id)}
                  comparisonFull={compareIds.length >= 4}
                  onOpen={() => setSelectedId(opportunity.id)}
                  onToggleCompare={() => toggleCompare(opportunity)}
                />
              ))
            )}
          </div>
        </aside>

        <section className="detailPane">
          {!detail && !detailLoading ? (
            <EmptyDetail />
          ) : detailLoading ? (
            <div className="blankDetail">
              <Loader2 className="spin" size={28} />
              <h2>Loading assessment</h2>
            </div>
          ) : (
            <>
              <AssessmentHero
                detail={detail}
                assessment={assessment}
                quality={quality}
                freshness={freshness}
                promotionLoading={promotionLoading}
                promotionPreview={promotionPreview}
                onPreviewPromotion={previewPromotion}
                onConfirmPromotion={confirmPromotion}
                onCancelPromotion={() => setPromotionPreview(null)}
                rescoreOpen={rescoreOpen}
                setRescoreOpen={setRescoreOpen}
                rescoreReason={rescoreReason}
                setRescoreReason={setRescoreReason}
                onRescore={submitRescore}
              />
              <EvidenceQualityBanner quality={quality} rankable={assessment?.rankable} />
              <ComponentGrid components={components} />
              <div className="detailTabs" role="tablist" aria-label="Assessment detail">
                <button
                  type="button"
                  role="tab"
                  aria-selected={detailTab === "evidence"}
                  className={detailTab === "evidence" ? "active" : ""}
                  onClick={() => setDetailTab("evidence")}
                >
                  <BarChart3 size={16} />
                  Evidence
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={detailTab === "history"}
                  className={detailTab === "history" ? "active" : ""}
                  onClick={() => setDetailTab("history")}
                >
                  <History size={16} />
                  History & cost
                </button>
              </div>
              {detailTab === "evidence" ? (
                <EvidenceWorkspace
                  detail={detail}
                  report={report}
                  score={score}
                  scanPayload={scanPayload}
                  keywords={keywords}
                  providers={providers}
                  competitors={competitors}
                  demandEvidence={demandEvidence}
                  componentDetails={componentDetails}
                />
              ) : (
                <HistoryCostWorkspace
                  history={detail.score_history || []}
                  freshness={freshness}
                  ledger={assessment?.cost_ledger}
                  apiCalls={detail.api_calls || []}
                />
              )}
            </>
          )}
        </section>
      </section>
    </main>
  );
}

function EnvironmentBanner({ meta }) {
  if (meta?.synthetic_fixture_data) {
    return (
      <section className="fixtureBanner">
        <AlertTriangle size={18} />
        <strong>Synthetic fixture data</strong>
        <span>Scores and evidence are deterministic test records, not live market research.</span>
      </section>
    );
  }
  if (meta?.dataforseo_sandbox && meta?.data_mode === "live") {
    return (
      <section className="fixtureBanner sandbox">
        <AlertTriangle size={18} />
        <strong>DataForSEO sandbox</strong>
        <span>API plumbing is live, but returned evidence may be mock or semantically unrelated.</span>
      </section>
    );
  }
  if (meta?.data_mode === "live" && meta?.live_api_calls_allowed) {
    return (
      <section className="fixtureBanner production">
        <AlertTriangle size={18} />
        <strong>Production spend enabled</strong>
        <span>Confirmed uncached calls may consume DataForSEO credits.</span>
      </section>
    );
  }
  return null;
}

function Notice({ notice, onDismiss }) {
  return (
    <div className={`notice ${notice.type}`} role={notice.type === "error" ? "alert" : "status"}>
      {notice.type === "error" ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}
      <span>{notice.message}</span>
      <button type="button" onClick={onDismiss} aria-label="Dismiss notice">
        <X size={15} />
      </button>
    </div>
  );
}

function PlanPreview({ plan }) {
  const calls = plan.planned_calls || [];
  return (
    <div className="planPreview">
      <div className="planStrip">
        <StatusBadge tone={plan.blocked ? "bad" : "good"}>
          {plan.blocked ? "Blocked" : componentLabel(plan.scan_profile)}
        </StatusBadge>
        <span>{calls.length} planned</span>
        <span>{calls.filter((call) => !call.cache_hit).length} network</span>
        <span>{calls.filter((call) => call.cache_hit).length} cached</span>
        <span>{money(plan.estimated_uncached_cost_usd)} uncached</span>
      </div>
      {plan.block_reason && <p className="inlineWarning">{plan.block_reason}</p>}
      <div className="plannedCalls">
        {calls.map((call, index) => (
          <span key={call.planned_request_id || `${call.stage}-${call.endpoint}-${index}`}>
            <strong>{componentLabel(call.stage)}</strong>
            <small>{call.cache_hit ? "cache hit" : "network"}</small>
            <small>{money(call.estimated_cost_usd)}</small>
          </span>
        ))}
      </div>
    </div>
  );
}

function PrefilterResults({ result, onSelect }) {
  return (
    <section className="prefilterBand">
      <div className="prefilterHeading">
        <div>
          <p className="eyebrow">Zero-cost public-data prefilter</p>
          <h2>{componentLabel(result.service_profile)}</h2>
          <small>{result.service}</small>
        </div>
        <span>{integer(result.candidate_count)} candidates · ACS · 0 paid calls</span>
      </div>
      <div className="prefilterResults">
        {(result.assessments || []).map((assessment) => (
          <button
            type="button"
            key={assessment.location.geography_id}
            className="prefilterRow"
            onClick={() => onSelect(assessment)}
          >
            <b>#{assessment.rank}</b>
            <span>
              <strong>{assessment.location.label}</strong>
              <small>
                {integer(assessment.input_signals.households)} households ·{" "}
                {Math.round((assessment.input_signals.homeownership_rate || 0) * 100)}% owner
              </small>
            </span>
            <em>{number(assessment.score)}</em>
          </button>
        ))}
      </div>
    </section>
  );
}

function Metric({ icon: Icon, label, value }) {
  return (
    <article className="metric">
      <Icon size={19} />
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function StatusBadge({ tone = "neutral", children, icon: Icon }) {
  return (
    <span className={`statusBadge ${tone}`}>
      {Icon && <Icon size={13} />}
      {children}
    </span>
  );
}

function OpportunityRow({
  opportunity,
  active,
  selectedForCompare,
  comparisonFull,
  onOpen,
  onToggleCompare
}) {
  const assessment = opportunity.latest_assessment;
  const rankable = Boolean(assessment?.rankable);
  const score = rankable ? assessment.score?.total_score : null;
  const assessmentType = assessment?.assessment_type || "unassessed";
  const disabled = !rankable || (comparisonFull && !selectedForCompare);
  return (
    <div className={`opportunityRow ${active ? "active" : ""}`}>
      <button type="button" className="opportunityOpen" onClick={onOpen}>
        <span>
          <strong>{opportunity.service}</strong>
          <small>
            <MapPin size={13} />
            {opportunity.market}
          </small>
          <span className="rowBadges">
            <StatusBadge tone={rankable ? "good" : "neutral"}>
              {assessmentType === "full" ? "Full" : "Preliminary"}
            </StatusBadge>
            {assessment?.evidence_quality?.status && (
              <StatusBadge tone={qualityTone(assessment.evidence_quality.status)}>
                {assessment.evidence_quality.status}
              </StatusBadge>
            )}
          </span>
        </span>
        <ScorePill score={score} confidence={assessment?.score?.confidence} rankable={rankable} />
      </button>
      <label
        className={`compareCheck ${disabled ? "disabled" : ""}`}
        title={
          rankable
            ? comparisonFull && !selectedForCompare
              ? "Remove another opportunity before adding this one."
              : "Select this full assessment for comparison."
            : "Only complete, rankable full assessments can be compared."
        }
      >
        <input
          type="checkbox"
          checked={selectedForCompare}
          disabled={disabled}
          onChange={onToggleCompare}
        />
        <span>
          <Check size={12} />
        </span>
        Compare
      </label>
    </div>
  );
}

function ScorePill({ score, confidence, rankable = true }) {
  return (
    <span className={`scorePill ${rankable ? "" : "unranked"}`}>
      <strong>{rankable ? number(score) : "—"}</strong>
      <small>{rankable ? confidence || "n/a" : "not ranked"}</small>
    </span>
  );
}

function CompareTray({ ids, opportunities, loading, onCompare, onClear }) {
  const names = ids.map((id) => opportunities.find((item) => item.id === id)?.market).filter(Boolean);
  return (
    <section className="compareTray" aria-label="Comparison selection">
      <GitCompareArrows size={18} />
      <div>
        <strong>{ids.length} of 4 selected</strong>
        <span>{names.join(" · ")}</span>
      </div>
      <button className="primaryButton" disabled={ids.length < 2 || loading} onClick={onCompare}>
        {loading ? <Loader2 className="spin" size={16} /> : <GitCompareArrows size={16} />}
        Compare full assessments
      </button>
      <button className="iconButton" onClick={onClear} aria-label="Clear comparison">
        <X size={17} />
      </button>
    </section>
  );
}

function ComparisonWorkspace({ data, onClose, onOpen }) {
  const items = data.opportunities || [];
  const componentKeys = Object.keys(COMPONENT_MAXIMUMS);
  return (
    <section className="comparisonWorkspace">
      <header>
        <div>
          <p className="eyebrow">Full-assessment comparison</p>
          <h2>Opportunity underwriting</h2>
        </div>
        <button className="iconButton" onClick={onClose} aria-label="Close comparison">
          <X size={18} />
        </button>
      </header>
      <div className="comparisonGrid" style={{ "--comparison-count": items.length }}>
        <div className="comparisonLabels">
          <strong>Opportunity</strong>
          <span>Total score</span>
          <span>Confidence</span>
          <span>Evidence quality</span>
          <span>Freshness</span>
          {componentKeys.map((key) => <span key={key}>{componentLabel(key)}</span>)}
          <span>Actual API cost</span>
        </div>
        {items.map((item) => {
          const assessment = item.latest_assessment;
          const score = assessment.score || {};
          return (
            <button
              type="button"
              className="comparisonColumn"
              key={item.opportunity.id}
              onClick={() => onOpen(item.opportunity.id)}
            >
              <strong>
                {item.opportunity.service}
                <small>{item.opportunity.market}</small>
              </strong>
              <b>{number(score.total_score)}</b>
              <span>{score.confidence || "unknown"}</span>
              <StatusBadge tone={qualityTone(assessment.evidence_quality?.status)}>
                {assessment.evidence_quality?.status || "unknown"}
              </StatusBadge>
              <StatusBadge tone={qualityTone(assessment.freshness?.overall_status)}>
                {assessment.freshness?.overall_status || "unknown"}
              </StatusBadge>
              {componentKeys.map((key) => (
                <span key={key}>
                  {number(score.component_scores?.[key])} / {COMPONENT_MAXIMUMS[key]}
                </span>
              ))}
              <span>{money(assessment.cost_ledger?.actual_cost_usd)}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function EmptyDetail() {
  return (
    <div className="blankDetail">
      <Sparkles size={28} />
      <h2>Select an opportunity</h2>
      <p>Evidence quality, scores, history, freshness, and API costs will appear here.</p>
    </div>
  );
}

function AssessmentHero({
  detail,
  assessment,
  quality,
  freshness,
  promotionLoading,
  promotionPreview,
  onPreviewPromotion,
  onConfirmPromotion,
  onCancelPromotion,
  rescoreOpen,
  setRescoreOpen,
  rescoreReason,
  setRescoreReason,
  onRescore
}) {
  const score = assessment?.score;
  const promotion = detail.promotion || {};
  return (
    <>
      <div className="detailHero">
        <div>
          <div className="heroMeta">
            <span>Opportunity #{detail.opportunity.id}</span>
            <StatusBadge tone={assessment?.assessment_type === "full" ? "good" : "warn"}>
              {assessment?.assessment_type === "full" ? "Full assessment" : "Preliminary"}
            </StatusBadge>
            <StatusBadge tone={qualityTone(quality.status)} icon={ShieldCheck}>
              Evidence {quality.status || "unknown"}
            </StatusBadge>
            <StatusBadge tone={qualityTone(freshness.overall_status)} icon={Clock3}>
              {freshness.overall_status || "unknown"}
            </StatusBadge>
          </div>
          <h2>{detail.opportunity.service} in {detail.opportunity.market}</h2>
          <p>{score?.explanation || "No score explanation has been stored yet."}</p>
          <p className="assessmentTimestamp">
            Assessed {dateTime(assessment?.created_at)} · Scan #{assessment?.scan?.id || "n/a"}
          </p>
        </div>
        <div className="heroActions">
          <ScoreDial
            score={score?.total_score}
            confidence={score?.confidence}
            rankable={assessment?.rankable}
          />
          <div className="heroButtons">
            {promotion.eligible && (
              <button className="primaryButton" onClick={onPreviewPromotion} disabled={promotionLoading}>
                {promotionLoading ? <Loader2 className="spin" size={16} /> : <TrendingUp size={16} />}
                Promote to full
              </button>
            )}
            <button className="ghostButton" onClick={() => setRescoreOpen(!rescoreOpen)}>
              <RefreshCw size={16} />
              Rescore
            </button>
          </div>
          {!promotion.eligible && promotion.reason && !promotion.already_full && (
            <small className="actionReason">{promotion.reason}</small>
          )}
        </div>
      </div>
      {promotionPreview && (
        <PromotionPreview
          preview={promotionPreview}
          loading={promotionLoading}
          onConfirm={onConfirmPromotion}
          onCancel={onCancelPromotion}
        />
      )}
      {rescoreOpen && (
        <form className="rescorePanel" onSubmit={onRescore}>
          <div>
            <strong>Rescore stored evidence</strong>
            <span>This changes scoring only. It does not make new API calls.</span>
          </div>
          <label>
            <span>Reason</span>
            <textarea
              autoFocus
              required
              minLength={3}
              maxLength={500}
              value={rescoreReason}
              placeholder="What configuration or underwriting assumption changed?"
              onChange={(event) => setRescoreReason(event.target.value)}
            />
          </label>
          <div>
            <button type="submit" className="primaryButton" disabled={rescoreReason.trim().length < 3}>
              <RefreshCw size={16} />
              Save rescore
            </button>
            <button type="button" className="ghostButton" onClick={() => setRescoreOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </>
  );
}

function PromotionPreview({ preview, loading, onConfirm, onCancel }) {
  const plan = preview.scan_plan || {};
  return (
    <section className="promotionPreview">
      <div>
        <TrendingUp size={20} />
        <span>
          <strong>Promote testing evidence to a full assessment</strong>
          <small>
            {preview.additional_uncached_call_count || 0} additional network calls ·{" "}
            {money(preview.additional_estimated_cost_usd)} estimated
          </small>
        </span>
      </div>
      <div className="promotionFacts">
        <span>{plan.planned_calls?.length || 0} total planned calls</span>
        <span>{plan.planned_calls?.filter((call) => call.cache_hit).length || 0} cache hits</span>
        <span>Source scan #{preview.source_scan_run_id}</span>
      </div>
      <div>
        <button className="primaryButton" onClick={onConfirm} disabled={loading}>
          {loading ? <Loader2 className="spin" size={16} /> : <Check size={16} />}
          Confirm and queue full scan
        </button>
        <button className="ghostButton" onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

function ScoreDial({ score, confidence, rankable }) {
  const value = Math.max(0, Math.min(100, score || 0));
  return (
    <div
      className={`scoreDial ${rankable ? "" : "unranked"}`}
      style={{ "--score": `${value * 3.6}deg` }}
    >
      <div>
        <strong>{number(score, "—")}</strong>
        <span>{rankable ? confidence || "unknown" : "not ranked"}</span>
      </div>
    </div>
  );
}

function EvidenceQualityBanner({ quality, rankable }) {
  const issues = quality?.issues || [];
  if (!quality?.status) return null;
  const tone = qualityTone(quality.status);
  return (
    <section className={`qualityBanner ${tone}`}>
      <div className="qualityHeading">
        {tone === "bad" ? <ShieldAlert size={20} /> : <ShieldCheck size={20} />}
        <div>
          <strong>
            {quality.status === "pass"
              ? "Evidence passed semantic quality checks"
              : quality.status === "warning"
                ? "Evidence needs cautious interpretation"
                : "Evidence is unusable for ranking"}
          </strong>
          <span>
            {rankable
              ? "This full assessment may be compared and sorted."
              : "This assessment is excluded from full-opportunity ranking and comparison."}
          </span>
        </div>
      </div>
      {issues.length > 0 && (
        <div className="qualityIssues">
          {issues.map((issue) => (
            <div key={issue.code}>
              {issue.severity === "error" ? <AlertCircle size={15} /> : <AlertTriangle size={15} />}
              <span>
                <strong>{componentLabel(issue.stage)}</strong>
                {issue.message}
              </span>
              <StatusBadge tone={issue.severity === "error" ? "bad" : "warn"}>
                {issue.severity}
              </StatusBadge>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ComponentGrid({ components }) {
  return (
    <div className="componentGrid">
      {Object.entries(COMPONENT_MAXIMUMS).map(([key, maximum]) => (
        <ComponentScore
          key={key}
          label={componentLabel(key)}
          value={components?.[key]}
          maximum={maximum}
        />
      ))}
    </div>
  );
}

function ComponentScore({ label, value, maximum }) {
  const percentage = Math.max(0, Math.min(100, ((value || 0) / maximum) * 100));
  return (
    <article className="component">
      <span>
        {label}
        <strong>{number(value)} / {maximum}</strong>
      </span>
      <div className="bar"><i style={{ width: `${percentage}%` }} /></div>
    </article>
  );
}

function EvidenceWorkspace({
  detail,
  report,
  score,
  scanPayload,
  keywords,
  providers,
  competitors,
  demandEvidence,
  componentDetails
}) {
  const keywordDecisions = detail.keyword_decisions || scanPayload.keyword_decisions || [];
  return (
    <div className="detailGrid">
      <Panel title="Market interpretation" icon={MapPin}>
        <Table
          columns={["Field", "Value"]}
          rows={[
            ["Market", report?.market_interpretation?.input_market || detail.opportunity.market],
            ["Type", report?.market_interpretation?.market_type || "unknown"],
            ["County", report?.market_interpretation?.county || "n/a"],
            ["Metro", report?.market_interpretation?.metro || "n/a"],
            ["Population", integer(report?.market_interpretation?.population)],
            [
              "Provider radius",
              report?.market_interpretation?.boundary_radius_km
                ? `${report.market_interpretation.boundary_radius_km} km`
                : "n/a"
            ],
            ["Evidence source", report?.scan_metadata?.evidence_source_mode || "unknown"]
          ]}
        />
      </Panel>
      {report?.public_data_prefilter && (
        <Panel title="Public-data prefilter" icon={Database}>
          <Table
            columns={["Signal", "Value"]}
            rows={[
              ["Profile", componentLabel(report.public_data_prefilter.service_profile)],
              ["Score", `${number(report.public_data_prefilter.score)} / 100`],
              ["Recommendation", componentLabel(report.public_data_prefilter.recommendation)],
              ["Households", integer(report.public_data_prefilter.input_signals.households)],
              ["Housing units", integer(report.public_data_prefilter.input_signals.housing_units)],
              [
                "Homeownership",
                `${Math.round((report.public_data_prefilter.input_signals.homeownership_rate || 0) * 100)}%`
              ],
              ["Median year built", report.public_data_prefilter.input_signals.median_year_built || "n/a"]
            ]}
          />
        </Panel>
      )}
      <Panel title="Keyword demand" icon={Search}>
        <Table
          columns={["Keyword", "Intent", "Volume", "Granularity", "CPC"]}
          rows={keywords.slice(0, 10).map((item) => [
            item.keyword,
            item.intent,
            integer(item.search_volume),
            item.market_granularity || "unknown",
            money(item.cpc)
          ])}
        />
        {demandEvidence?.warning && <p className="panelWarning">{demandEvidence.warning}</p>}
      </Panel>
      <Panel title="Keyword decisions" icon={ShieldCheck}>
        <Table
          columns={["Keyword", "Decision", "Rank", "Reason"]}
          rows={keywordDecisions.slice(0, 10).map((item) => [
            item.keyword,
            item.representative ? "SERP representative" : item.decision,
            item.rank || "n/a",
            item.reason || "n/a"
          ])}
        />
      </Panel>
      <Panel title="SERP composition" icon={Globe2}>
        <Table
          columns={["Classification", "Count"]}
          rows={Object.entries(report?.serp_composition?.classification_counts || {}).map(
            ([label, value]) => [componentLabel(label), value]
          )}
        />
      </Panel>
      <Panel title="Top competitors" icon={CircleDollarSign}>
        <Table
          columns={["Domain", "Ref. domains", "Type", "Position"]}
          rows={competitors.slice(0, 8).map((item) => [
            item.domain,
            integer(item.referring_domains),
            componentLabel(item.page_type),
            item.serp_position || item.position || "n/a"
          ])}
        />
      </Panel>
      <Panel title="Provider suitability" icon={Building2} wide>
        <Table
          columns={["Provider", "Score", "Service", "Geography", "Status", "Contact"]}
          rows={providers.map((item) => [
            item.name,
            item.suitability_score ?? "n/a",
            formatProviderSignal(item, "service_fit"),
            formatProviderSignal(item, "geographic_fit"),
            item.business_status || "unknown",
            formatProviderSignal(item, "contactability")
          ])}
        />
      </Panel>
      <Panel title="Score calculation trace" icon={FileText} wide>
        {Object.keys(componentDetails || {}).length ? (
          <div className="scoreTraces">
            {Object.entries(componentDetails).map(([label, trace]) => (
              <section className="scoreTrace" key={label}>
                <div className="scoreTraceHeading">
                  <div>
                    <strong>{componentLabel(label)}</strong>
                    <span>{trace.explanation}</span>
                  </div>
                  <b>{number(trace.score)} / {number(trace.maximum_score)}</b>
                </div>
                <div className="scoreTraceSteps">
                  {(trace.calculation_steps || []).map((step, index) => (
                    <div className="scoreTraceStep" key={`${step.label}-${index}`}>
                      <span>
                        <strong>{step.label}</strong>
                        <small>{step.detail}</small>
                      </span>
                      <b className={Number(step.points) < 0 ? "negative" : "positive"}>
                        {signedPoints(step.points)}
                      </b>
                    </div>
                  ))}
                </div>
                <code>{trace.formula}</code>
              </section>
            ))}
          </div>
        ) : (
          <Table
            columns={["Component", "Explanation"]}
            rows={Object.entries(score?.component_explanations || {}).map(([key, value]) => [
              componentLabel(key),
              value
            ])}
          />
        )}
      </Panel>
    </div>
  );
}

function HistoryCostWorkspace({ history, freshness, ledger, apiCalls }) {
  return (
    <div className="detailGrid historyGrid">
      <Panel title="Evidence freshness" icon={Clock3}>
        <div className="freshnessSummary">
          <StatusBadge tone={qualityTone(freshness?.overall_status)}>
            {freshness?.overall_status || "unknown"}
          </StatusBadge>
          <span>
            Oldest evidence:{" "}
            {freshness?.oldest_age_days === null || freshness?.oldest_age_days === undefined
              ? "unknown"
              : `${number(freshness.oldest_age_days)} days`}
          </span>
        </div>
        <div className="freshnessGroups">
          {Object.entries(freshness?.groups || {}).map(([name, group]) => (
            <div key={name}>
              <span>
                <strong>{componentLabel(name)}</strong>
                <small>
                  Newest {dateTime(group.newest_at)} · limit {group.maximum_age_days} days
                </small>
              </span>
              <StatusBadge tone={qualityTone(group.status)}>{group.status}</StatusBadge>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Score history" icon={History}>
        <div className="historyTimeline">
          {history.length ? history.map((entry) => (
            <div className="historyEntry" key={entry.artifact_id}>
              <span className="historyMarker" />
              <div>
                <strong>
                  {number(entry.score?.total_score)}
                  {entry.diff?.total_delta !== null && entry.diff?.total_delta !== undefined && (
                    <b className={Number(entry.diff.total_delta) < 0 ? "negative" : "positive"}>
                      {signedPoints(entry.diff.total_delta)}
                    </b>
                  )}
                </strong>
                <span>
                  {componentLabel(entry.assessment_type || entry.artifact_kind)} ·{" "}
                  {entry.score?.scoring_version || "unknown version"}
                </span>
                <small>{entry.reason || "Original scan assessment"}</small>
                <time>{dateTime(entry.created_at)}</time>
                {entry.diff?.component_deltas && (
                  <div className="deltaList">
                    {Object.entries(entry.diff.component_deltas)
                      .filter(([, value]) => Number(value) !== 0)
                      .map(([name, value]) => (
                        <span key={name}>
                          {componentLabel(name)} <b>{signedPoints(value)}</b>
                        </span>
                      ))}
                  </div>
                )}
              </div>
            </div>
          )) : <p className="muted">No score history has been stored.</p>}
        </div>
      </Panel>
      <Panel title="Planned vs. executed API calls" icon={CircleDollarSign} wide>
        <CostLedger ledger={ledger} apiCalls={apiCalls} />
      </Panel>
    </div>
  );
}

function CostLedger({ ledger, apiCalls }) {
  if (!ledger) {
    return <p className="muted">No reconciled cost ledger is available for this assessment.</p>;
  }
  const calls = ledger.calls?.length ? ledger.calls : apiCalls;
  return (
    <>
      <div className="ledgerSummary">
        <LedgerMetric label="Planned" value={ledger.planned_call_count} />
        <LedgerMetric label="Executed" value={ledger.executed_call_count} />
        <LedgerMetric label="Network" value={ledger.network_call_count} />
        <LedgerMetric label="Cache hits" value={ledger.cache_hit_count} />
        <LedgerMetric label="Failed" value={ledger.failed_call_count} tone="bad" />
        <LedgerMetric label="Unexecuted" value={ledger.unexecuted_call_count} tone="warn" />
        <LedgerMetric label="Unexpected" value={ledger.unexpected_call_count} tone="bad" />
        <LedgerMetric label="Estimated" value={money(ledger.estimated_cost_usd)} />
        <LedgerMetric label="Actual" value={money(ledger.actual_cost_usd)} />
      </div>
      <div className={`ledgerStatus ${ledger.ledger_complete ? "good" : "warn"}`}>
        {ledger.ledger_complete ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
        {ledger.ledger_complete
          ? "Every planned request reconciles to a terminal execution record."
          : "The ledger contains pending, unexecuted, or unexpected requests."}
      </div>
      <Table
        columns={["Stage", "Endpoint", "Planned", "Executed", "Cache", "Estimate", "Actual", "Error"]}
        rows={(calls || []).map((call) => [
          componentLabel(call.stage),
          call.endpoint || "n/a",
          call.planned_status || (call.planned_request_id ? "planned" : "unexpected"),
          call.execution_status || call.status,
          call.cache_hit ? "hit" : "network",
          money(call.estimated_cost_usd),
          money(call.actual_cost_usd),
          call.error_summary || "—"
        ])}
      />
    </>
  );
}

function LedgerMetric({ label, value, tone = "" }) {
  return (
    <span className={`ledgerMetric ${tone}`}>
      <small>{label}</small>
      <strong>{value ?? 0}</strong>
    </span>
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
  if (!rows?.length) return <p className="muted">No data saved yet.</p>;
  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatProviderSignal(provider, signal) {
  const value = provider?.suitability_signals?.[signal]?.normalized;
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "n/a";
}
