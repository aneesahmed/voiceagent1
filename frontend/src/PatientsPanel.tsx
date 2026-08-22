import { useEffect, useState } from "react";

const API_BASE_URL = "http://localhost:8001";

interface Patient {
  patient_id: string;
  // Optional beyond patient_id/timestamps: the voice agent's incremental
  // save_patient can leave a genuinely partial "draft" row mid-registration
  // (see CLAUDE.md decision #20) -- is_complete tells you which this is.
  first_name: string | null;
  last_name: string | null;
  date_of_birth: string | null;
  sex: string | null;
  phone_number: string | null;
  email: string | null;
  address_line_1: string | null;
  address_line_2: string | null;
  city: string | null;
  state: string | null;
  zip_code: string | null;
  insurance_provider: string | null;
  insurance_member_id: string | null;
  preferred_language: string;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  created_at: string;
  is_complete: boolean;
}

function formatPhone(digits: string | null): string {
  if (!digits) return "—";
  return digits.length === 10 ? `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}` : digits;
}

export default function PatientsPanel() {
  const [patients, setPatients] = useState<Patient[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    fetch(`${API_BASE_URL}/patients`)
      .then((res) => res.json())
      .then((body) => {
        if (body.error) throw new Error(typeof body.error === "string" ? body.error : "Request failed");
        setPatients(body.data);
      })
      .catch(() => setError("Couldn't reach the backend."))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-4 lg:sticky lg:top-8 lg:max-h-[calc(100vh-4rem)] flex flex-col">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Registered Patients</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Live view of what the voice agent has saved. Refresh after a test call.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs text-slate-500 hover:text-slate-800 disabled:opacity-40 shrink-0 mt-0.5"
          title="Refresh"
        >
          {loading ? "…" : "⟳"}
        </button>
      </div>

      {error && <p className="text-xs text-red-600 bg-red-50 rounded-lg px-2 py-1.5">{error}</p>}
      {!patients && !error && <p className="text-xs text-slate-400">Loading…</p>}
      {patients && patients.length === 0 && (
        <p className="text-xs text-slate-400">No patients yet -- start a call and register one.</p>
      )}

      <div className="space-y-2 overflow-y-auto">
        {patients?.map((p) => {
          const isOpen = expanded === p.patient_id;
          return (
            <div key={p.patient_id} className="border border-slate-100 rounded-xl overflow-hidden">
              <button
                onClick={() => setExpanded(isOpen ? null : p.patient_id)}
                className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-slate-50"
              >
                <span className="text-xs font-medium text-slate-800 truncate flex items-center gap-1.5">
                  {p.first_name || p.last_name
                    ? `${p.first_name ?? "?"} ${p.last_name ?? ""}`.trim()
                    : formatPhone(p.phone_number) !== "—"
                      ? formatPhone(p.phone_number)
                      : "(no info yet)"}
                  {!p.is_complete && (
                    <span className="text-[9px] font-semibold text-amber-700 bg-amber-50 rounded-full px-1.5 py-0.5">
                      DRAFT
                    </span>
                  )}
                </span>
                <span className="text-slate-400 text-xs shrink-0">{isOpen ? "−" : "+"}</span>
              </button>

              {isOpen && (
                <dl className="px-3 pb-3 space-y-1 text-[11px] text-slate-600">
                  <div className="flex justify-between gap-2">
                    <dt className="text-slate-400">DOB</dt>
                    <dd>{p.date_of_birth || "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-slate-400">Gender</dt>
                    <dd>{p.sex || "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-slate-400">Phone</dt>
                    <dd>{formatPhone(p.phone_number)}</dd>
                  </div>
                  {p.email && (
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-400">Email</dt>
                      <dd className="truncate">{p.email}</dd>
                    </div>
                  )}
                  {(p.address_line_1 || p.city || p.state || p.zip_code) && (
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-400">Address</dt>
                      <dd className="text-right">
                        {[p.address_line_1, p.address_line_2, p.city, [p.state, p.zip_code].filter(Boolean).join(" ")]
                          .filter(Boolean)
                          .join(", ")}
                      </dd>
                    </div>
                  )}
                  {p.insurance_provider && (
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-400">Insurance</dt>
                      <dd className="text-right">
                        {p.insurance_provider}
                        {p.insurance_member_id ? ` (${p.insurance_member_id})` : ""}
                      </dd>
                    </div>
                  )}
                  {p.preferred_language !== "English" && (
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-400">Language</dt>
                      <dd>{p.preferred_language}</dd>
                    </div>
                  )}
                  {p.emergency_contact_name && (
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-400">Emergency contact</dt>
                      <dd className="text-right">
                        {p.emergency_contact_name}
                        {p.emergency_contact_phone ? ` -- ${formatPhone(p.emergency_contact_phone)}` : ""}
                      </dd>
                    </div>
                  )}
                  <div className="flex justify-between gap-2 pt-1 border-t border-slate-100 mt-1">
                    <dt className="text-slate-400">Registered</dt>
                    <dd>{new Date(p.created_at).toLocaleString()}</dd>
                  </div>
                </dl>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
