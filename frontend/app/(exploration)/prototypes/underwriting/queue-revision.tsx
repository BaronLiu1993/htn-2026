'use client';

import { useMemo, useState } from 'react';
import { Dialog } from '@base-ui/react/dialog';
import { Popover } from '@base-ui/react/popover';
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Check,
  ChevronDown,
  CircleX,
  Database,
  ListFilter,
  Search,
  Target,
  X,
} from 'lucide-react';
import { Account, accounts, groups } from './data';
import { Badge } from './shared';

type ReviewMode = 'inline' | 'rail' | 'ledger';
type View = 'queue' | 'activity' | 'guidelines';
type StatusFilter = Account['status'] | null;

type Evidence = {
  id: string;
  label: string;
  record: string;
  field: string;
  value: string;
  rule: string;
  preview: string;
};

const statusCards = [
  { status: 'Target', tone: 'target', icon: Target },
  { status: 'Acceptable', tone: 'acceptable', icon: Check },
  { status: 'Needs review', tone: 'amber', icon: AlertTriangle },
  { status: 'Out of appetite', tone: 'red', icon: CircleX },
] as const;

const targetLabels = [
  'Risk state',
  'Insured value',
  'Premium',
  'Building age',
];

function accountEvidence(account: Account): Evidence[] {
  const lossMissing = account.id === 'E';

  return [
    {
      id: 'business',
      label: 'New Property business',
      record: `Submission SUB-${account.id}`,
      field: 'line_of_business · transaction_type',
      value: 'Commercial Property · New business',
      rule: 'Eligibility requires new Commercial Property business.',
      preview: 'Submission is new Commercial Property business.',
    },
    {
      id: 'state',
      label: 'Risk state',
      record: `Location LOC-${account.id}`,
      field: 'state',
      value: account.state,
      rule:
        'Eligible states include the fixture state list. OH, PA, MD, CO, CA, and FL are target states.',
      preview: `${account.state} is ${account.targets[0] ? 'a target state' : 'eligible, but not a target state'}.`,
    },
    {
      id: 'tiv',
      label: 'Insured value',
      record: `Building BLD-${account.id}`,
      field: 'total_insured_value_usd',
      value: `$${account.tiv}M`,
      rule:
        'Eligible values are positive and at most $150M. The $50M–$100M range is a target preference.',
      preview: `$${account.tiv}M is ${account.targets[1] ? 'inside the target range' : 'eligible, outside the target range'}.`,
    },
    {
      id: 'premium',
      label: 'Premium',
      record: `Policy POL-${account.id}`,
      field: 'annual_premium_usd',
      value: `$${account.premium}K`,
      rule:
        'Appetite is $50K–$175K. The $75K–$100K range is a target preference.',
      preview:
        account.premium > 175
          ? `$${account.premium}K exceeds the $175K maximum.`
          : `$${account.premium}K is within appetite${account.targets[2] ? ' and the target range' : ''}.`,
    },
    {
      id: 'year',
      label: 'Building year',
      record: `Building BLD-${account.id}`,
      field: 'year_built',
      value: String(account.year),
      rule:
        'Buildings must be newer than 1990. Years after 2010 are a target preference.',
      preview: `Built in ${account.year}; ${account.targets[3] ? 'target preference met' : 'eligible age'}.`,
    },
    {
      id: 'construction',
      label: 'Construction',
      record: `Building BLD-${account.id}`,
      field: 'construction_type',
      value: '100% noncombustible',
      rule: 'Noncombustible construction is accepted by this fixture rubric.',
      preview: 'Construction is recorded as 100% noncombustible.',
    },
    {
      id: 'losses',
      label: 'Five-year losses',
      record: lossMissing
        ? `Submission SUB-${account.id} evidence register`
        : `Loss summary LOS-${account.id}`,
      field: lossMissing ? 'five_year_loss_history' : 'coverage_window · incurred_total_usd',
      value: lossMissing ? 'Missing — value unknown' : 'Sep 19, 2021–Sep 19, 2026 · $40,000',
      rule:
        'A complete five-year loss history under $100K is required. Missing history keeps eligibility unresolved.',
      preview: lossMissing
        ? 'No complete five-year record is attached. This is unknown, not $0.'
        : 'The complete fixture window totals $40,000.',
    },
  ];
}

function EvidenceMarker({
  evidence,
  index,
  onInspect,
}: {
  evidence: Evidence;
  index: number;
  onInspect?: (evidence: Evidence) => void;
}) {
  return (
    <Popover.Root>
      <Popover.Trigger
        className="evidence-marker"
        aria-label={`Evidence ${index}: ${evidence.label}. ${evidence.preview}`}
        openOnHover
        delay={140}
        closeDelay={120}
        onClick={() => onInspect?.(evidence)}
      >
        {index}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={8} className="evidence-positioner">
          <Popover.Popup className="evidence-popup">
            <Popover.Arrow className="evidence-arrow" />
            <div className="evidence-popup-head">
              <Popover.Title>{evidence.label}</Popover.Title>
              <Popover.Close aria-label="Close evidence">
                <X size={14} />
              </Popover.Close>
            </div>
            <Popover.Description>{evidence.preview}</Popover.Description>
            <dl>
              <div>
                <dt>Source record</dt>
                <dd>{evidence.record}</dd>
              </div>
              <div>
                <dt>Field</dt>
                <dd>{evidence.field}</dd>
              </div>
              <div>
                <dt>Value</dt>
                <dd>{evidence.value}</dd>
              </div>
              <div>
                <dt>Applicable rule</dt>
                <dd>{evidence.rule}</dd>
              </div>
            </dl>
            <p className="fixture-label">Fictional fixture record</p>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

function EvidenceRef({
  items,
  evidenceId,
  onInspect,
}: {
  items: Evidence[];
  evidenceId: string;
  onInspect?: (evidence: Evidence) => void;
}) {
  const index = items.findIndex((item) => item.id === evidenceId);
  const evidence = items[index];

  return evidence ? (
    <EvidenceMarker
      evidence={evidence}
      index={index + 1}
      onInspect={onInspect}
    />
  ) : null;
}

function EvidenceBundle({
  items,
  evidenceIds,
  label,
  summary,
  onInspect,
}: {
  items: Evidence[];
  evidenceIds: string[];
  label: string;
  summary: string;
  onInspect?: (evidence: Evidence) => void;
}) {
  const records = evidenceIds
    .map((id) => items.find((item) => item.id === id))
    .filter((item): item is Evidence => Boolean(item));

  if (records.length === 0) return null;

  return (
    <Popover.Root>
      <Popover.Trigger
        className="evidence-bundle-trigger"
        aria-label={`${label}. ${summary}`}
        openOnHover
        delay={140}
        closeDelay={180}
        onClick={() => onInspect?.(records[0])}
      >
        {label}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner
          sideOffset={8}
          className="evidence-positioner evidence-bundle-positioner"
        >
          <Popover.Popup className="evidence-popup evidence-bundle-popup">
            <Popover.Arrow className="evidence-arrow" />
            <div className="evidence-popup-head">
              <Popover.Title>{label}</Popover.Title>
              <Popover.Close aria-label="Close evidence">
                <X size={14} />
              </Popover.Close>
            </div>
            <Popover.Description>{summary}</Popover.Description>
            <div className="evidence-bundle-list">
              {records.map((record) => (
                <section key={record.id}>
                  <div className="bundle-record-head">
                    <strong>{record.label}</strong>
                    <span>{record.value}</span>
                  </div>
                  <div className="bundle-record-source">
                    <span>Source</span>
                    <code>{record.record}</code>
                    <span>Field</span>
                    <code>{record.field}</code>
                  </div>
                  <p><span>Rule</span>{record.rule}</p>
                </section>
              ))}
            </div>
            <p className="fixture-label">Fictional fixture records</p>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

function AccountFacts({
  account,
  evidence,
  onInspect,
}: {
  account: Account;
  evidence: Evidence[];
  onInspect?: (evidence: Evidence) => void;
}) {
  const facts = [
    ['Risk state', account.state, 'state'],
    ['Insured value', `$${account.tiv}M`, 'tiv'],
    ['Premium', `$${account.premium}K`, 'premium'],
    ['Built', String(account.year), 'year'],
  ];

  return (
    <div className="review-facts">
      {facts.map(([label, value, evidenceId]) => (
        <div key={label}>
          <span>{label}</span>
          <strong>
            {value}{' '}
            <EvidenceRef
              items={evidence}
              evidenceId={evidenceId}
              onInspect={onInspect}
            />
          </strong>
        </div>
      ))}
    </div>
  );
}

function EvidenceRail({
  evidence,
  active,
}: {
  evidence: Evidence[];
  active: Evidence;
}) {
  return (
    <aside className="evidence-rail" aria-label="Evidence index">
      <div className="section-kicker">Evidence index</div>
      <div className="rail-active" aria-live="polite">
        <span>Selected source</span>
        <strong>{active.record}</strong>
        <code>{active.field}</code>
        <p>{active.value}</p>
      </div>
      <ol>
        {evidence.map((item, index) => (
          <li key={item.id} data-active={item.id === active.id ? '' : undefined}>
            <span>{index + 1}</span>
            <div>
              <strong>{item.label}</strong>
              <small>{item.record}</small>
            </div>
          </li>
        ))}
      </ol>
    </aside>
  );
}

function ClaimLedger({
  account,
  evidence,
  onInspect,
}: {
  account: Account;
  evidence: Evidence[];
  onInspect?: (evidence: Evidence) => void;
}) {
  const claims = [
    {
      label: 'Eligibility',
      value:
        account.group === 'Investigate'
          ? 'Unresolved'
          : account.group === 'Out of appetite'
            ? 'Excluded'
            : 'Eligible',
      ids:
        account.group === 'Investigate'
          ? ['losses']
          : account.group === 'Out of appetite'
            ? ['premium']
            : ['business', 'construction', 'losses'],
    },
    {
      label: 'Primary reason',
      value: account.reason,
      ids:
        account.group === 'Investigate'
          ? ['losses']
          : account.group === 'Out of appetite'
            ? ['premium']
            : account.targets
                .map((match, index) => (match ? ['state', 'tiv', 'premium', 'year'][index] : null))
                .filter((id): id is string => Boolean(id)),
    },
    {
      label: 'Next action',
      value: account.action,
      ids: account.group === 'Investigate' ? ['losses'] : ['business'],
    },
  ];

  return (
    <div className="claim-ledger">
      {claims.map((claim) => (
        <section key={claim.label}>
          <div>
            <span>{claim.label}</span>
            <p>{claim.value}</p>
          </div>
          <div className="claim-evidence" aria-label={`Evidence for ${claim.label}`}>
            {claim.ids.map((id) => (
              <EvidenceRef
                key={id}
                items={evidence}
                evidenceId={id}
                onInspect={onInspect}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function ReviewAssessment({
  account,
  mode,
}: {
  account: Account;
  mode: ReviewMode;
}) {
  const evidence = accountEvidence(account);
  const [activeEvidence, setActiveEvidence] = useState(evidence[0]);
  const [actionOpen, setActionOpen] = useState(false);
  const targetCount = account.targets.filter(Boolean).length;
  const unresolved = account.group === 'Investigate';
  const excluded = account.group === 'Out of appetite';
  const inspect = mode === 'rail' ? setActiveEvidence : undefined;

  return (
    <div className={`revision-review review-${mode}`}>
      <div className="review-main">
        <div className="review-identity">
          <div>
            <div className="section-kicker">Submission {account.id} · New business</div>
            <Dialog.Title>{account.name}</Dialog.Title>
            <p>Commercial property · 1 location · USD</p>
          </div>
          <Badge a={account} />
        </div>

        <div className="review-next-action">
          <span>Next action</span>
          <strong>{account.action}</strong>
        </div>

        {mode === 'ledger' ? (
          <ClaimLedger account={account} evidence={evidence} onInspect={inspect} />
        ) : (
          <section className="assessment-summary">
            <div className="section-kicker">Assessment</div>
            <h3>
              {unresolved
                ? 'Eligibility is not established.'
                : excluded
                  ? 'This account is outside appetite.'
                  : account.status === 'Target'
                    ? 'Eligible with a strong target fit.'
                    : 'Eligible with an acceptable target fit.'}
            </h3>
            <p>
              {unresolved ? (
                <>
                  The complete five-year loss history is missing. The loss amount is unknown,
                  not zero —{' '}
                  <EvidenceBundle
                    items={evidence}
                    evidenceIds={['losses']}
                    label="loss history evidence"
                    summary="One record shows that complete loss history has not been provided."
                    onInspect={inspect}
                  />
                  .
                </>
              ) : excluded ? (
                <>
                  The requested premium is ${account.premium}K. It exceeds the $175K appetite
                  maximum —{' '}
                  <EvidenceBundle
                    items={evidence}
                    evidenceIds={['premium']}
                    label="premium evidence"
                    summary="One policy record supports the confirmed premium exclusion."
                    onInspect={inspect}
                  />
                  .
                </>
              ) : (
                <>
                  Required eligibility evidence is complete —{' '}
                  <EvidenceBundle
                    items={evidence}
                    evidenceIds={['business', 'state', 'tiv', 'premium', 'year', 'construction', 'losses']}
                    label="7 supporting records"
                    summary="Seven fixture records support the eligibility assessment."
                    onInspect={inspect}
                  />
                  . This account meets {targetCount} of 4 target preferences —{' '}
                  <EvidenceBundle
                    items={evidence}
                    evidenceIds={['state', 'tiv', 'premium', 'year']}
                    label="preference evidence"
                    summary="Four source fields support the target-preference count."
                    onInspect={inspect}
                  />
                  .
                </>
              )}
            </p>
          </section>
        )}

        {(unresolved || excluded) && (
          <section className={`review-alert ${excluded ? 'danger' : 'warning'}`}>
            {excluded ? <CircleX size={16} /> : <AlertTriangle size={16} />}
            <div>
              <strong>{excluded ? 'Confirmed exclusion' : 'Missing evidence'}</strong>
              <p>
                {excluded
                  ? 'Target preferences cannot offset a confirmed appetite exclusion.'
                  : 'Request a complete five-year loss run before assigning a final fit score.'}
              </p>
            </div>
          </section>
        )}

        <section>
          <div className="section-kicker">Supporting facts</div>
          <AccountFacts account={account} evidence={evidence} onInspect={inspect} />
        </section>

        <section className="target-breakdown">
          <div className="section-kicker">Target preferences</div>
          {unresolved || excluded ? (
            <p className="score-withheld">
              Final fit score withheld. {unresolved ? 'Establish eligibility first.' : 'The account is excluded.'}
            </p>
          ) : (
            <p className="fit-summary">
              <strong>{targetCount} of 4</strong> preferences match · {targetCount * 25}/100 fit
            </p>
          )}
          <div className="target-list">
            {targetLabels.map((label, index) => {
              const evidenceId = ['state', 'tiv', 'premium', 'year'][index];
              const isExclusion = excluded && evidenceId === 'premium';
              return (
                <div key={label}>
                  <span>
                    {label}{' '}
                    <EvidenceRef
                      items={evidence}
                      evidenceId={evidenceId}
                      onInspect={inspect}
                    />
                  </span>
                  <strong className={isExclusion ? 'negative' : ''}>
                    {isExclusion
                      ? 'Outside appetite'
                      : account.targets[index]
                        ? 'Target preference'
                        : 'Acceptable'}
                  </strong>
                </div>
              );
            })}
          </div>
          <p className="review-disclaimer">
            Target fit orders eligible accounts. It is not an approval probability or profit forecast.
          </p>
        </section>

        <details className="agent-activity">
          <summary>
            <span>
              <Activity size={15} /> Agent activity
            </span>
            <span>4 illustrative steps <ChevronDown size={14} /></span>
          </summary>
          <div className="activity-body">
            <p>Illustrative trace only. No model or live API ran.</p>
            <ol>
              <li>Discover fixture resources and fields.</li>
              <li>Find policy and exposure records for submission {account.id}.</li>
              <li>Verify appetite evidence and completeness.</li>
              <li>
                {unresolved
                  ? 'Keep eligibility unresolved because loss history is missing.'
                  : excluded
                    ? 'Record the premium exclusion and withhold fit scoring.'
                    : 'Establish eligibility, then evaluate target preferences.'}
              </li>
            </ol>
            <details className="raw-query">
              <summary>Raw query intent</summary>
              <pre>{JSON.stringify({ resource: 'Policy', submission_id: account.id, purpose: 'Retrieve evidence for appetite review' }, null, 2)}</pre>
            </details>
          </div>
        </details>

        <div className="review-action">
          <button className="primary" onClick={() => setActionOpen((open) => !open)}>
            {actionOpen ? 'Hide prototype outcome' : account.action}
            <span>↗</span>
          </button>
          {actionOpen && (
            <div role="status" className="action-result">
              <strong>{unresolved ? 'Request draft prepared — not sent' : 'Review opened in prototype'}</strong>
              <p>
                {unresolved
                  ? 'Please provide complete five-year loss runs, including confirmation for years with no claims.'
                  : 'Confirm the source evidence and document the underwriting decision. This prototype does not approve or decline coverage.'}
              </p>
            </div>
          )}
        </div>
      </div>

      {mode === 'rail' && <EvidenceRail evidence={evidence} active={activeEvidence} />}
    </div>
  );
}

function AccountReview({
  account,
  mode,
  label,
}: {
  account: Account;
  mode: ReviewMode;
  label: string;
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger className="text-button">
        {label} <span>↗</span>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Backdrop className="proto-backdrop" />
        <Dialog.Popup className={`revision-drawer drawer-${mode}`}>
          <Dialog.Close className="close revision-close" aria-label="Close review">
            <X size={18} />
          </Dialog.Close>
          <ReviewAssessment account={account} mode={mode} />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Sidebar({ view, onView }: { view: View; onView: (view: View) => void }) {
  const primary = [
    { id: 'queue' as const, label: 'Queue', icon: ListFilter },
    { id: 'activity' as const, label: 'Run activity', icon: Activity },
  ];

  return (
    <aside className="revision-sidebar" aria-label="Underwriting navigation">
      <nav>
        {primary.map((item) => (
          <button
            key={item.id}
            aria-current={view === item.id ? 'page' : undefined}
            onClick={() => onView(item.id)}
          >
            <item.icon size={16} />
            {item.label}
          </button>
        ))}
      </nav>
      <div className="sidebar-secondary">
        <button
          aria-current={view === 'guidelines' ? 'page' : undefined}
          onClick={() => onView('guidelines')}
        >
          <BookOpen size={16} />
          Guidelines
        </button>
        <div className="demo-state">
          <span /> Demo data
          <small>5 fictional fixtures</small>
        </div>
      </div>
    </aside>
  );
}

function RunActivity() {
  return (
    <main className="supporting-view">
      <div className="compact-heading">
        <div>
          <h1>Run activity</h1>
          <p>Illustrative traces for the five fixture submissions.</p>
        </div>
        <span className="demo-inline">Demo data</span>
      </div>
      <div className="activity-table">
        {accounts.map((account, index) => (
          <div key={account.id}>
            <span className="activity-status"><Check size={14} /> Complete</span>
            <strong>{account.name}</strong>
            <span>4 steps</span>
            <time>09:{42 - index * 3}</time>
          </div>
        ))}
      </div>
      <p className="support-note">No live service or model ran. Times and traces are illustrative.</p>
    </main>
  );
}

function Guidelines() {
  return (
    <main className="supporting-view guidelines-view">
      <div className="compact-heading">
        <div>
          <h1>Property appetite</h1>
          <p>Proposed team rubric v0.1 · fictional demo</p>
        </div>
      </div>
      <div className="guideline-list">
        <section>
          <span>1</span>
          <div>
            <h2>Establish eligibility</h2>
            <p>Check business type, state, value, premium, building age, construction, and complete loss history.</p>
          </div>
        </section>
        <section>
          <span>2</span>
          <div>
            <h2>Separate unknowns and exclusions</h2>
            <p>Missing evidence stays unresolved. A confirmed rule failure stays out of appetite.</p>
          </div>
        </section>
        <section>
          <span>3</span>
          <div>
            <h2>Evaluate target preferences</h2>
            <p>Only eligible accounts receive a fit score. Fit is not an approval or profit forecast.</p>
          </div>
        </section>
      </div>
    </main>
  );
}

function QueueView({ mode }: { mode: ReviewMode }) {
  const [group, setGroup] = useState('Pursue');
  const [status, setStatus] = useState<StatusFilter>(null);
  const [search, setSearch] = useState('');

  const rows = useMemo(
    () =>
      accounts.filter(
        (account) =>
          account.group === group &&
          (!status || account.status === status) &&
          account.name.toLowerCase().includes(search.toLowerCase()),
      ),
    [group, search, status],
  );

  function selectGroup(nextGroup: string) {
    setGroup(nextGroup);
    setStatus(null);
  }

  function selectStatus(nextStatus: string) {
    const account = accounts.find((item) => item.status === nextStatus);
    if (!account) return;
    if (status === nextStatus) {
      setStatus(null);
      return;
    }
    setGroup(account.group);
    setStatus(nextStatus);
  }

  return (
    <main className="revision-queue">
      <div className="compact-heading queue-heading">
        <div>
          <h1>Queue</h1>
          <p>5 submissions · 3 eligible · 1 unresolved · 1 excluded</p>
        </div>
        <span className="rubric-state">Property appetite · v0.1</span>
      </div>

      <section className="status-cards" aria-label="Filter queue by appetite status">
        {statusCards.map((card) => {
          const count = accounts.filter((account) => account.status === card.status).length;
          const Icon = card.icon;
          return (
            <button
              key={card.status}
              className={card.tone}
              aria-pressed={status === card.status}
              onClick={() => selectStatus(card.status)}
            >
              <span>
                <Icon size={14} /> {card.status}
              </span>
              <strong>{count}</strong>
              <small>{status === card.status ? 'Filtering queue' : 'View accounts'}</small>
            </button>
          );
        })}
      </section>

      <div className="revision-toolbar">
        <div className="filters" aria-label="Action groups">
          {groups.map((item) => (
            <button
              key={item}
              aria-pressed={group === item && status === null}
              data-current-group={group === item ? '' : undefined}
              onClick={() => selectGroup(item)}
            >
              {item}
              <span>{accounts.filter((account) => account.group === item).length}</span>
            </button>
          ))}
        </div>
        <label className="queue-search">
          <span className="sr-only">Search accounts</span>
          <Search size={14} />
          <input
            placeholder="Search accounts…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>

      <div className="table-wrap revision-table">
        <table>
          <thead>
            <tr>
              <th>Account / reason</th>
              <th>Appetite</th>
              <th>Target fit</th>
              <th>Premium</th>
              <th>Next step</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((account) => (
              <tr key={account.id}>
                <td>
                  <strong>{account.name}</strong>
                  <small>{account.state} · ${account.tiv}M insured value · Built {account.year}</small>
                  <p>{account.reason}</p>
                </td>
                <td><Badge a={account} /></td>
                <td>
                  {account.group === 'Pursue' ? (
                    <><strong>{account.targets.filter(Boolean).length} of 4</strong><small>target preferences</small></>
                  ) : (
                    <span className="muted">Not scored</span>
                  )}
                </td>
                <td>${account.premium}K</td>
                <td>
                  <AccountReview
                    account={account}
                    mode={mode}
                    label={
                      account.group === 'Investigate'
                        ? 'Resolve gap'
                        : account.group === 'Out of appetite'
                          ? 'View exclusion'
                          : 'Review account'
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="empty">
            <Database size={18} />
            <p>No accounts match this filter.</p>
            <button onClick={() => setSearch('')}>Clear search</button>
          </div>
        )}
      </div>
      <div className="footnote">
        <span>Property · New business · USD</span>
        <span>Recommendations require human review.</span>
      </div>
    </main>
  );
}

export default function RevisedQueue({ reviewMode }: { reviewMode: ReviewMode }) {
  const [view, setView] = useState<View>('queue');

  return (
    <div className="revision-shell">
      <Sidebar view={view} onView={setView} />
      <div className="revision-content">
        {view === 'queue' && <QueueView mode={reviewMode} />}
        {view === 'activity' && <RunActivity />}
        {view === 'guidelines' && <Guidelines />}
      </div>
    </div>
  );
}
