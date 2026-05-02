import { Profile } from "../types";
import { PipelineStatusState } from "../hooks/useAudit";

interface Props {
  profile: Profile | null;
  webFootprintStatus: PipelineStatusState;
}

interface EnrichmentRecord {
  email: string;
  firstSeen: string;
  lastSeen: string;
  emailProvider: string;
  canReceive: boolean;
  names: { value: string; source: string }[];
  usernames: { value: string; source: string; breached?: boolean }[];
  profileLinks: { platform: string; handle: string; url: string }[];
  locations: { value: string; source: string }[];
  registrations: string[];
  breaches: { source: string; date: string; fields: string[] }[];
  infostealer: { stealer: string; date: string; combos: number }[];
  accounts: {
    platform: string;
    fields: { label: string; value: string }[];
  }[];
  timeline: { date: string; label: string; source: string }[];
}

const localFixtures = import.meta.glob<{ default: Record<string, EnrichmentRecord> }>(
  "../data/enrichment.local.json",
  { eager: true },
);

const ENRICHMENT_INDEX: Record<string, EnrichmentRecord> =
  Object.values(localFixtures)[0]?.default ?? {};

function lookup(profile: Profile | null): EnrichmentRecord | null {
  if (!profile) return null;
  const key = profile.username.trim().toLowerCase();
  return ENRICHMENT_INDEX[key] ?? null;
}

export default function EnrichmentPanel({ profile, webFootprintStatus }: Props) {
  const record = lookup(profile);

  const pipelineDone =
    webFootprintStatus.status === "complete" ||
    webFootprintStatus.status === "error" ||
    webFootprintStatus.status === "budget_exceeded";

  if (!record) return null;
  if (!pipelineDone) return <EnrichmentLoading />;

  const RIGHT_RAIL_PLATFORMS = ["LinkedIn", "VSCO", "GitHub"];
  const railAccounts = record.accounts.filter((a) =>
    RIGHT_RAIL_PLATFORMS.includes(a.platform),
  );
  const overflowAccounts = record.accounts.filter(
    (a) => !RIGHT_RAIL_PLATFORMS.includes(a.platform),
  );

  return (
    <section className="border border-kali-border bg-kali-surface transition-colors hover:border-kali-text">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-kali-border px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h2 className="font-mono text-[13px] font-bold uppercase tracking-label text-kali-text">
            // enriched profile
          </h2>
          <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
            {record.email}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3 font-mono text-[10px] uppercase tracking-label text-kali-label">
          <span>first seen {record.firstSeen}</span>
          <span>last seen {record.lastSeen}</span>
          <span>provider {record.emailProvider}</span>
          <span>
            receives {record.canReceive ? "yes" : "no"}
          </span>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-0 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div
          className="bg-kali-bg p-4 lg:border-r lg:border-kali-border"
          style={{ minHeight: 520 }}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card title={`names found · ${record.names.length}`}>
              <ul className="space-y-2">
                {record.names.map((n, i) => (
                  <Row key={i} primary={n.value} meta={n.source} />
                ))}
              </ul>
            </Card>

            <Card title={`usernames · ${record.usernames.length}`}>
              <ul className="space-y-2">
                {record.usernames.map((u, i) => (
                  <Row
                    key={i}
                    primary={
                      <>
                        {u.value}
                        {u.breached && (
                          <span className="ml-2 border border-kali-text px-1 text-[9px] uppercase tracking-label text-kali-text">
                            breach
                          </span>
                        )}
                      </>
                    }
                    meta={u.source}
                  />
                ))}
              </ul>
            </Card>

            <Card
              title={`profile links · ${record.profileLinks.length}`}
              className="sm:col-span-2"
            >
              <ul className="grid grid-cols-1 gap-x-4 gap-y-2 md:grid-cols-2">
                {record.profileLinks.map((p, i) => (
                  <Row
                    key={i}
                    primary={
                      <>
                        {p.platform}
                        <span className="ml-2 text-kali-dim">{p.handle}</span>
                      </>
                    }
                    meta={p.url}
                  />
                ))}
              </ul>
            </Card>

            <Card
              title={`locations · ${record.locations.length}`}
              className="sm:col-span-2"
            >
              <ul className="grid grid-cols-1 gap-x-4 gap-y-2 md:grid-cols-2">
                {record.locations.map((l, i) => (
                  <Row key={i} primary={l.value} meta={l.source} />
                ))}
              </ul>
            </Card>

            <Card title={`infostealer logs · ${record.infostealer.length}`}>
              <ul className="space-y-2">
                {record.infostealer.map((s, i) => (
                  <Row
                    key={i}
                    primary={s.stealer}
                    meta={`${s.date} · ${s.combos} combo`}
                  />
                ))}
              </ul>
            </Card>

            <Card title={`data breaches · ${record.breaches.length}`}>
              <ul className="space-y-2">
                {record.breaches.map((b, i) => (
                  <Row
                    key={i}
                    primary={b.source}
                    meta={`${b.date} · ${b.fields.join(", ")}`}
                  />
                ))}
              </ul>
            </Card>

            <Card
              title={`registrations · ${record.registrations.length}`}
              className="sm:col-span-2"
            >
              <div className="flex flex-wrap gap-1.5">
                {record.registrations.map((r) => (
                  <span
                    key={r}
                    className="border border-kali-border px-1.5 py-0.5 text-[10px] tracking-label text-kali-dim"
                  >
                    {r}
                  </span>
                ))}
              </div>
            </Card>

            <div className="grid grid-cols-1 gap-4 sm:col-span-2 sm:grid-cols-3">
              {overflowAccounts.map((a) => (
                <Card
                  key={a.platform}
                  title={`${a.platform.toLowerCase()} · ${a.fields.length} fields`}
                >
                  <ul className="space-y-2">
                    {a.fields.map((f, i) => (
                      <li key={i} className="flex flex-col gap-0.5">
                        <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
                          {f.label}
                        </span>
                        <span className="break-words font-mono text-[11px] leading-snug text-kali-text">
                          {f.value}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              ))}

              <Card title={`timeline · ${record.timeline.length}`}>
                <ul className="space-y-2">
                  {record.timeline.map((t, i) => (
                    <li
                      key={i}
                      className="flex flex-col gap-0.5 border-l border-kali-text pl-2"
                    >
                      <span className="font-mono text-[12px] leading-snug text-kali-text">
                        {t.label}
                      </span>
                      <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
                        {t.date} · {t.source}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>
        </div>

        <aside className="flex flex-col gap-4 border-t border-kali-border bg-kali-surface px-5 py-5 font-mono text-[11px] text-kali-text lg:border-t-0">
          <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
            account detail
          </span>
          <div className="space-y-3">
            {railAccounts.map((a) => (
              <div key={a.platform} className="border border-kali-border bg-kali-bg p-3">
                <div className="mb-2 font-mono text-[11px] font-bold uppercase tracking-label text-kali-text">
                  {a.platform}
                </div>
                <ul className="space-y-2">
                  {a.fields.map((f, i) => (
                    <li key={i} className="flex flex-col gap-0.5">
                      <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
                        {f.label}
                      </span>
                      <span className="break-words font-mono text-[11px] leading-snug text-kali-text">
                        {f.value}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </section>
  );
}

interface CardProps {
  title: string;
  className?: string;
  children: React.ReactNode;
}

function Card({ title, className, children }: CardProps) {
  return (
    <div
      className={`border border-kali-border bg-kali-surface p-3 font-mono text-[12px] text-kali-text ${className ?? ""}`}
    >
      <div className="mb-2 font-mono text-[10px] uppercase tracking-label text-kali-label">
        {title}
      </div>
      {children}
    </div>
  );
}

interface RowProps {
  primary: React.ReactNode;
  meta: React.ReactNode;
}

function Row({ primary, meta }: RowProps) {
  return (
    <li className="flex flex-col gap-0.5">
      <span className="break-words font-mono text-[12px] leading-snug text-kali-text">
        {primary}
      </span>
      <span className="break-words font-mono text-[10px] uppercase tracking-label text-kali-label">
        {meta}
      </span>
    </li>
  );
}

function EnrichmentLoading() {
  return (
    <section className="border border-kali-border bg-kali-surface">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-kali-border px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h2 className="font-mono text-[13px] font-bold uppercase tracking-label text-kali-text">
            // enriched profile
          </h2>
          <span className="font-mono text-[10px] uppercase tracking-label text-kali-label animate-running-pulse">
            waiting on web footprint
          </span>
        </div>
      </header>

      <div className="flex items-center gap-3 bg-kali-bg p-6">
        <span
          aria-hidden="true"
          className="font-mono text-[13px] text-kali-text animate-running-pulse"
        >
          [...]
        </span>
        <span className="font-mono text-[11px] uppercase tracking-label text-kali-label">
          intelbase enrichment unlocks once // web footprint settles
        </span>
      </div>
    </section>
  );
}
