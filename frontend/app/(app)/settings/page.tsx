"use client";

import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import {
  Bell,
  Fingerprint,
  Globe,
  Info,
  LogOut,
  Monitor,
  Moon,
  Palette,
  ShieldCheck,
  Sun,
  User,
} from "lucide-react";
import { cn } from "@/lib/cn";
import {
  accountInitials,
  localisationCapabilities,
  notificationCapabilities,
  readThemePreference,
  securityCapabilities,
  shortAccountId,
  workspaceSummary,
} from "@/lib/account";
import { useSession, type SessionStatus } from "@/lib/store/auth";
import { runtimeMode } from "@/lib/runtime-config";
import { Avatar, Badge } from "@/components/system/badges";
import { Button, ChoiceChip } from "@/components/system/controls";
import { Card, DemoBadge, LiveBadge, PageHeader } from "@/components/system/ui";
import { WriteModal } from "@/components/system/WriteModal";

/**
 * Account & profile.
 *
 * Every value here comes from the real session payload (`GET /auth/me`): user
 * id, email, display name and business memberships. The backend has no profile
 * update, session-management, password, locale or notification endpoints, so
 * those surfaces state the limitation instead of rendering controls that would
 * silently discard a change. Appearance is the one preference that is genuinely
 * writable — `next-themes` stores it in this browser, which the panel says.
 */

const subscribeToNothing = () => () => {};

/**
 * True only after hydration. `next-themes` resolves the stored preference on
 * the client, so anything deriving text from it must render the same unresolved
 * output on the server and on the first client pass, or React reports a
 * hydration mismatch. This avoids the setState-in-effect pattern for that test.
 */
function useMounted(): boolean {
  return useSyncExternalStore(
    subscribeToNothing,
    () => true,
    () => false,
  );
}

interface AccountSection {
  id: string;
  label: string;
  icon: typeof User;
}

const sections: AccountSection[] = [
  { id: "profile", label: "Profile", icon: User },
  { id: "account", label: "Account", icon: Fingerprint },
  { id: "preferences", label: "Preferences", icon: Globe },
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "security", label: "Sessions & security", icon: ShieldCheck },
  { id: "notifications", label: "Notifications", icon: Bell },
];

function Section({
  id,
  title,
  description,
  icon: Icon,
  badge,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  icon: typeof User;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="scroll-mt-16 lg:scroll-mt-4">
      <Card className="overflow-hidden">
        <header className="flex items-start gap-2.5 border-b border-line/80 px-4 py-3">
          <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[0.55rem] bg-brand-soft text-brand">
            <Icon aria-hidden className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2
              id={`${id}-heading`}
              className="text-heading-sm font-bold tracking-tight text-fg"
            >
              {title}
            </h2>
            {description && (
              <p className="mt-0.5 text-caption leading-5 text-fg-muted">{description}</p>
            )}
          </div>
          {badge && <div className="shrink-0">{badge}</div>}
        </header>
        <div className="px-4">{children}</div>
      </Card>
    </section>
  );
}

function DetailRow({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-1 border-b border-line/70 py-3 last:border-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <p className="text-caption font-semibold text-fg">{label}</p>
        {hint && <p className="mt-0.5 text-label leading-5 text-fg-subtle">{hint}</p>}
      </div>
      <div className="min-w-0 break-words text-body-sm text-fg-muted sm:max-w-[52%] sm:text-right">
        {value}
      </div>
    </div>
  );
}

function UnavailableRow({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="flex flex-col gap-1 border-b border-line/70 py-3 last:border-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <p className="text-caption font-semibold text-fg">{label}</p>
      <div className="flex min-w-0 items-start gap-1.5 sm:max-w-[62%] sm:justify-end">
        <Badge tone="neutral" size="xs" className="mt-0.5 shrink-0">
          Not available
        </Badge>
        <p className="text-label leading-5 text-fg-subtle sm:text-right">{detail}</p>
      </div>
    </div>
  );
}

function Note({ children }: { children: ReactNode }) {
  return (
    <div className="my-3 flex items-start gap-2 rounded-control border border-line bg-surface-sunken/50 px-3 py-2.5">
      <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-subtle" />
      <p className="text-label leading-5 text-fg-muted">{children}</p>
    </div>
  );
}

function ActionRow({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 border-b border-line/70 py-3 last:border-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <p className="text-caption font-semibold text-fg">{title}</p>
        <p className="mt-0.5 text-label leading-5 text-fg-subtle">{detail}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function AppearancePanel() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  // `next-themes` only resolves the stored preference after hydration, so the
  // server renders the unresolved note and no pressed chip. Everything that
  // depends on the resolved palette waits for the mount to keep hydration in
  // sync; before that the panel reads as "resolving", which is also true.
  const mounted = useMounted();
  const preference = mounted ? readThemePreference(theme) : null;
  const options = [
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
    { value: "system", label: "System", icon: Monitor },
  ] as const;
  const palette = !mounted
    ? null
    : resolvedTheme === "dark"
      ? "dark"
      : resolvedTheme === "light"
        ? "light"
        : null;
  return (
    <Section
      id="appearance"
      title="Appearance"
      description="The theme applies to the whole workspace on this device."
      icon={Palette}
      badge={
        <Badge tone="success" size="xs">
          Editable
        </Badge>
      }
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <div
          role="group"
          aria-label="Theme"
          className="inline-flex flex-wrap gap-1 rounded-full border border-line bg-surface-sunken/60 p-1"
        >
          {options.map((option) => (
            <ChoiceChip
              key={option.value}
              pressed={preference === option.value}
              onClick={() => setTheme(option.value)}
              className="px-3"
            >
              <option.icon aria-hidden className="h-3.5 w-3.5" />
              {option.label}
            </ChoiceChip>
          ))}
        </div>
      </div>
      <Note>
        {palette
          ? `Currently rendering the ${palette} palette. `
          : "Resolving the active palette… "}
        Your choice is stored in this browser only — it is not saved to your account, so it does not
        follow you to another device.
      </Note>
    </Section>
  );
}

function GuestAccountView({ status }: { status: SessionStatus }) {
  const message =
    runtimeMode === "demo"
      ? "This demo deployment renders the simulated Northstar business and never contacts a backend, so there is no signed-in account, session or notification data to show."
      : status === "unreachable"
        ? "The backend can't be reached, so your account details could not be loaded. Retry the connection, then reload this page."
        : "No account session is active in this deployment.";
  const rows = [
    { label: "Appearance", note: "Available now — stored in this browser.", ok: true },
    { label: "Profile & account", note: "Needs a signed-in live session.", ok: false },
    { label: "Preferences", note: "Needs a signed-in live session.", ok: false },
    { label: "Sessions & security", note: "Needs a signed-in live session.", ok: false },
    { label: "Notifications", note: "Needs a signed-in live session.", ok: false },
  ];
  return (
    <Card className="p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-sunken text-fg-muted">
          <Info aria-hidden className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h2 className="text-heading-sm font-bold tracking-tight text-fg">No account session</h2>
          <p className="mt-1 text-body-sm leading-6 text-fg-muted">{message}</p>
        </div>
      </div>
      <ul className="mt-4 divide-y divide-line/70 rounded-control border border-line bg-surface-sunken/40">
        {rows.map((row) => (
          <li
            key={row.label}
            className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2"
          >
            <span className="text-caption font-semibold text-fg">{row.label}</span>
            <span className="flex items-center gap-1.5">
              <Badge tone={row.ok ? "success" : "neutral"} size="xs">
                {row.ok ? "Available" : "Unavailable"}
              </Badge>
              <span className="text-label text-fg-subtle">{row.note}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function AccountPage() {
  const router = useRouter();
  const { status, user, businesses, currentBusinessId, businessNames, logout } = useSession();
  const [signOutOpen, setSignOutOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [activeSection, setActiveSection] = useState<string>(sections[0].id);
  const account = status === "authenticated" ? user : null;

  // Highlights the section currently in view. Navigation itself is plain
  // in-page anchors, so it works with the keyboard and without JavaScript;
  // this only adds orientation.
  useEffect(() => {
    if (!account || typeof IntersectionObserver === "undefined") return;
    const nodes = sections
      .map((section) => document.getElementById(section.id))
      .filter((node): node is HTMLElement => node !== null);
    if (nodes.length === 0) return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target.id);
          else visible.delete(entry.target.id);
        }
        const first = sections.find((section) => visible.has(section.id));
        if (first) setActiveSection(first.id);
      },
      { rootMargin: "-20% 0px -65% 0px" },
    );
    for (const node of nodes) observer.observe(node);
    return () => observer.disconnect();
  }, [account]);

  async function confirmSignOut() {
    setSigningOut(true);
    // Never optimistic: the store clears local identity only after the server
    // call settles, and records an error when the server did not confirm.
    await logout();
    setSigningOut(false);
    setSignOutOpen(false);
    router.push("/login");
  }

  if (!account) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader
          title="Account"
          sub="Your profile, workspace access, appearance and session security."
          badge={runtimeMode === "demo" ? <DemoBadge /> : undefined}
        />
        <GuestAccountView status={status} />
        <AppearancePanel />
      </div>
    );
  }

  const currentWorkspace = currentBusinessId
    ? (businessNames[currentBusinessId] ?? shortAccountId(currentBusinessId))
    : "No workspace selected";

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Account"
        sub="Your profile, workspace access, appearance and session security."
        badge={<LiveBadge />}
        actions={
          <Button variant="secondary" onClick={() => setSignOutOpen(true)}>
            <LogOut aria-hidden className="h-4 w-4" />
            Sign out
          </Button>
        }
      />

      <Card className="relative overflow-hidden">
        <span
          aria-hidden
          className="pointer-events-none absolute -right-10 -top-16 h-40 w-40 rounded-full bg-brand/10 blur-3xl"
        />
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-brand/25 to-transparent"
        />
        <div className="relative flex flex-wrap items-center gap-4 p-4 sm:p-5">
          <Avatar name={accountInitials(account.displayName, account.email)} hue={217} size={56} />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-heading-sm font-bold tracking-tight text-fg">
              {account.displayName}
            </h2>
            <p className="truncate text-body-sm text-fg-muted">{account.email}</p>
            <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-label text-fg-subtle">
              <span>{workspaceSummary(businesses.length)}</span>
              <span aria-hidden>·</span>
              <span title={account.id}>Account {shortAccountId(account.id)}</span>
            </p>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[196px_minmax(0,1fr)] lg:items-start lg:gap-5">
        <nav
          aria-label="Account sections"
          className="sticky top-0 z-10 -mx-4 min-w-0 border-b border-line/70 bg-surface/85 px-4 py-2 backdrop-blur-xl sm:-mx-5 sm:px-5 lg:static lg:mx-0 lg:rounded-card lg:border lg:border-line lg:bg-surface/70 lg:p-2 lg:shadow-card"
        >
          <ul className="flex gap-1 overflow-x-auto lg:flex-col lg:gap-0.5 lg:overflow-visible">
            {sections.map((section) => {
              const active = activeSection === section.id;
              return (
                <li key={section.id} className="shrink-0 lg:shrink">
                  <a
                    href={`#${section.id}`}
                    aria-current={active ? "true" : undefined}
                    className={cn(
                      "flex items-center gap-2 whitespace-nowrap rounded-control px-2.5 py-1.5 text-caption font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
                      active
                        ? "bg-brand-soft text-fg ring-1 ring-inset ring-brand/20"
                        : "text-fg-muted hover:bg-surface-hover hover:text-fg",
                    )}
                  >
                    <section.icon aria-hidden className="h-4 w-4 shrink-0" />
                    <span className="truncate">{section.label}</span>
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-col gap-4">
          <Section
            id="profile"
            title="Profile"
            description="How you appear across the workspace."
            icon={User}
            badge={
              <Badge tone="neutral" size="xs">
                Read-only
              </Badge>
            }
          >
            <DetailRow label="Display name" value={account.displayName} />
            <DetailRow
              label="Email address"
              value={account.email}
              hint="Used to sign in and to identify you in activity records."
            />
            <DetailRow
              label="Avatar"
              value={`Initials from “${account.displayName}”`}
              hint="Generated in the interface; no image is stored on your account."
            />
            <Note>
              Profile editing is not available in this deployment. Account details are read from the
              server on each session check, and there is no update endpoint to write a new name,
              email or avatar to — nothing here pretends an edit was saved.
            </Note>
          </Section>

          <Section
            id="account"
            title="Account"
            description="Workspace access and account-level actions for this session."
            icon={Fingerprint}
            badge={
              <Badge tone="neutral" size="xs">
                Read-only
              </Badge>
            }
          >
            <DetailRow
              label="Account ID"
              value={<span className="break-all font-mono text-label">{account.id}</span>}
              hint="Share this with a workspace operator if you need support."
            />
            <DetailRow label="Workspaces" value={workspaceSummary(businesses.length)} />
            <DetailRow label="Current workspace" value={currentWorkspace} />
            <ActionRow
              title="Sign out"
              detail="Revokes this session on the server and clears the session cookie in this browser."
            >
              <Button variant="secondary" onClick={() => setSignOutOpen(true)}>
                <LogOut aria-hidden className="h-4 w-4" />
                Sign out
              </Button>
            </ActionRow>
            <div className="py-3">
              <p className="text-caption font-semibold text-fg">Delete account</p>
              <p className="mt-0.5 text-label leading-5 text-fg-subtle">
                Not available. Account removal is handled by a workspace operator — the API exposes
                no self-serve deletion, and no control is shown that could not complete it.
              </p>
            </div>
          </Section>

          <Section
            id="preferences"
            title="Preferences"
            description="Language, time zone and regional formatting."
            icon={Globe}
            badge={
              <Badge tone="neutral" size="xs">
                Unavailable
              </Badge>
            }
          >
            {localisationCapabilities.map((capability) => (
              <UnavailableRow key={capability.label} {...capability} />
            ))}
            <Note>
              These preferences need account storage the backend does not have. They are listed so
              you can see what is missing, but no control is offered that would discard a change.
            </Note>
          </Section>

          <AppearancePanel />

          <Section
            id="security"
            title="Sessions & security"
            description="The session protecting this browser right now."
            icon={ShieldCheck}
          >
            <DetailRow
              label="Current session"
              value={
                <Badge tone="success" size="xs">
                  Active now
                </Badge>
              }
              hint="This browser. The session id lives in an HttpOnly cookie that page scripts cannot read."
            />
            <DetailRow
              label="Request protection"
              value="CSRF token, memory only"
              hint="Attached to writes for this tab and never written to storage, so a reload re-issues it with the session check."
            />
            <ActionRow
              title="Revoke this session"
              detail="Signing out ends the server-side session for this browser immediately."
            >
              <Button variant="destructive" onClick={() => setSignOutOpen(true)}>
                <LogOut aria-hidden className="h-4 w-4" />
                Sign out
              </Button>
            </ActionRow>
            {securityCapabilities.map((capability) => (
              <UnavailableRow key={capability.label} {...capability} />
            ))}
          </Section>

          <Section
            id="notifications"
            title="Notifications"
            description="Where Fluxus tells you something needs attention."
            icon={Bell}
            badge={
              <Badge tone="neutral" size="xs">
                Unavailable
              </Badge>
            }
          >
            <Note>
              Approvals are surfaced in the top bar badge and on the Approvals page — that is the
              only alerting Fluxus does in this deployment.
            </Note>
            {notificationCapabilities.map((capability) => (
              <UnavailableRow key={capability.label} {...capability} />
            ))}
          </Section>
        </div>
      </div>

      {signOutOpen && (
        <WriteModal
          title="Sign out of Fluxus"
          dirty={false}
          phase={signingOut ? "saving" : "idle"}
          onClose={() => {
            if (!signingOut) setSignOutOpen(false);
          }}
          footer={
            <>
              <Button
                variant="destructive"
                className="flex-1 justify-center"
                loading={signingOut}
                onClick={() => void confirmSignOut()}
              >
                Sign out
              </Button>
              <Button
                variant="secondary"
                disabled={signingOut}
                onClick={() => setSignOutOpen(false)}
              >
                Cancel
              </Button>
            </>
          }
        >
          <p className="text-body-sm leading-6 text-fg-muted">
            This ends the session on the server and clears the session cookie in this browser. You
            will need your password to sign back in.
          </p>
          <p className="break-words text-label text-fg-subtle">Signed in as {account.email}</p>
        </WriteModal>
      )}
    </div>
  );
}
