"use client";

/**
 * Integrations (Stage 19) — live provider catalog and OAuth connection
 * management. Nothing here is invented: demo mode states plainly that
 * integrations require the backend, unconfigured providers stay disabled, and
 * connected accounts come only from the server for the selected business.
 */

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  Lock,
  Mail,
  Plug,
  ShieldCheck,
  Unplug,
} from "lucide-react";
import { Badge, type BadgeTone } from "@/components/system/badges";
import { Button } from "@/components/system/controls";
import { Card, GhostButton, LiveBadge, PageHeader, PrimaryButton, Skeleton } from "@/components/system/ui";
import type {
  ApiIntegrationCapability,
  ApiIntegrationConnection,
  ApiIntegrationProvider,
} from "@/lib/api";
import { useSession } from "@/lib/store/auth";
import {
  useLiveIntegrations,
  useLiveIntegrationsActions,
  type IntegrationActionRecord,
} from "@/lib/store/integrations";
import { isLiveContext } from "@/lib/store/live";

const HEALTH_TONE: Record<string, BadgeTone> = {
  ok: "success",
  degraded: "warning",
  needs_reconnect: "danger",
  revoked: "neutral",
};

const HEALTH_LABEL: Record<string, string> = {
  ok: "Healthy",
  degraded: "Degraded",
  needs_reconnect: "Reconnect needed",
  revoked: "Revoked",
};

const OAUTH_ERROR_MESSAGE: Record<string, string> = {
  authorization_denied: "The provider authorization was cancelled or denied.",
  invalid_state: "That authorization link is invalid or expired. Start the connection again.",
  not_configured: "This integration is not configured on the server yet.",
  provider_error: "The provider could not complete the authorization. Try again.",
  auth_error: "The provider rejected the authorization. Try connecting again.",
  scope_missing: "The provider did not grant every requested permission.",
  integration_credentials_unavailable: "Stored credentials could not be read. Reconnect the account.",
};

function formatWhen(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function ProviderMark({ provider }: { provider: ApiIntegrationProvider }) {
  const Icon = provider.id === "gmail" ? Mail : Plug;
  return (
    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-line bg-surface-sunken text-brand">
      <Icon className="h-5 w-5" aria-hidden />
    </span>
  );
}

function CapabilityPill({ capability }: { capability: ApiIntegrationCapability }) {
  const write = capability.risk === "write";
  return (
    <span
      title={capability.title}
      className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2 py-0.5 text-[11.5px] font-medium text-ink-500 dark:text-slate-300"
    >
      {write ? <Lock className="h-3 w-3" aria-hidden /> : <ShieldCheck className="h-3 w-3" aria-hidden />}
      {capability.title}
    </span>
  );
}

function ActionFeedback({ record }: { record?: IntegrationActionRecord }) {
  if (!record || record.phase === "idle" || record.phase === "working") return null;
  const error = record.phase === "error";
  return (
    <p
      role={error ? "alert" : "status"}
      className={
        error
          ? "mt-2 rounded-lg bg-danger-soft px-2.5 py-1.5 text-[12px] text-danger-fg"
          : "mt-2 rounded-lg bg-success-soft px-2.5 py-1.5 text-[12px] text-success-fg"
      }
    >
      {record.message}
    </p>
  );
}

function ProviderCard({
  businessId,
  provider,
  connected,
}: {
  businessId: string;
  provider: ApiIntegrationProvider;
  connected: ApiIntegrationConnection[];
}) {
  const connect = useLiveIntegrationsActions((s) => s.connect);
  const record = useLiveIntegrationsActions(
    (s) => s.records[`${businessId}/connect/${provider.id}`],
  );
  const [pendingWrites, setPendingWrites] = useState(false);
  const working = record?.phase === "working";

  const activeConnections = connected.filter((row) => row.status === "connected");
  const hasReadConnection = activeConnections.length > 0;
  const hasWriteConnection = activeConnections.some((row) => row.canWrite);
  const canConnectRead = provider.configured && provider.readScopes.length > 0;
  const canConnectWrite =
    provider.configured && provider.writeScopes.length > 0 && !hasWriteConnection;

  async function start(includeWrites: boolean) {
    setPendingWrites(includeWrites);
    const url = await connect(businessId, provider.id, includeWrites);
    if (url) {
      // Full browser navigation: the backend owns state, PKCE and callback.
      window.location.assign(url);
    } else {
      setPendingWrites(false);
    }
  }

  // Reconnecting a read+send account must keep the write scope: a read-only
  // reconnect would silently drop send access.
  const reconnectWrites = hasWriteConnection;

  return (
    <Card className="flex flex-col p-4">
      <div className="flex items-start gap-3">
        <ProviderMark provider={provider} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[14.5px] font-bold">{provider.name}</p>
            {provider.configured ? (
              hasWriteConnection ? (
                <Badge tone="success">Connected · read + send</Badge>
              ) : hasReadConnection ? (
                <Badge tone="success">Connected · read-only</Badge>
              ) : (
                <Badge tone="brand">Ready to connect</Badge>
              )
            ) : (
              <Badge tone="warning">Server setup required</Badge>
            )}
          </div>
          <p className="mt-1 text-[12.5px] leading-5 text-ink-500 dark:text-slate-400">
            {provider.description}
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {provider.capabilities.map((capability) => (
          <CapabilityPill key={capability.id} capability={capability} />
        ))}
      </div>

      <div className="mt-3 grid gap-1 text-[12px] text-ink-500 dark:text-slate-400">
        <p>
          <span className="font-semibold text-ink-600 dark:text-slate-200">Read access</span>{" "}
          {provider.readScopes.length > 0 ? provider.readScopes.join(", ") : "none requested"}
        </p>
        <p>
          <span className="font-semibold text-ink-600 dark:text-slate-200">Write access</span>{" "}
          {provider.writeScopes.length > 0
            ? `${provider.writeScopes.join(", ")} — every write still needs a human approval`
            : "none requested"}
        </p>
      </div>

      {!provider.configured && (
        <div className="mt-3 rounded-xl border border-warning-line bg-warning-soft p-2.5 text-[12px] text-warning-fg">
          <p className="flex items-center gap-1.5 font-semibold">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
            Not configured on this server
          </p>
          <p className="mt-0.5">
            Missing: {provider.missingConfiguration.length > 0
              ? provider.missingConfiguration.join(", ")
              : "provider settings"}
            . An operator must add them before any account can connect.
          </p>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <PrimaryButton
          size="sm"
          loading={working && pendingWrites === reconnectWrites}
          disabled={!canConnectRead || working}
          onClick={() => void start(reconnectWrites)}
        >
          {reconnectWrites
            ? "Reconnect read + send"
            : hasReadConnection
              ? "Reconnect read-only"
              : "Connect read-only"}
        </PrimaryButton>
        {canConnectWrite && (
          <GhostButton size="sm" loading={working && pendingWrites} disabled={working} onClick={() => void start(true)}>
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
            Grant send access
          </GhostButton>
        )}
        <a
          href={provider.docsUrl}
          target="_blank"
          rel="noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-[12px] font-medium text-brand hover:underline"
        >
          API guide <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
      </div>
      <ActionFeedback record={record} />
    </Card>
  );
}

function ConnectionCard({
  businessId,
  connection,
}: {
  businessId: string;
  connection: ApiIntegrationConnection;
}) {
  const disconnect = useLiveIntegrationsActions((s) => s.disconnect);
  const record = useLiveIntegrationsActions(
    (s) => s.records[`${businessId}/disconnect/${connection.id}`],
  );
  const [confirming, setConfirming] = useState(false);
  const working = record?.phase === "working";
  const granted = connection.capabilities.filter((c) => c.granted);
  const missing = connection.capabilities.filter((c) => !c.granted);

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[14.5px] font-bold">{connection.providerName}</p>
            <Badge tone={HEALTH_TONE[connection.health] ?? "neutral"}>
              {HEALTH_LABEL[connection.health] ?? connection.health}
            </Badge>
            {connection.canWrite ? (
              <Badge tone="warning">Read + send</Badge>
            ) : (
              <Badge tone="neutral">Read-only</Badge>
            )}
          </div>
          <p className="mt-0.5 text-[12.5px] text-ink-500 dark:text-slate-400">
            {connection.displayName} · {connection.status}
          </p>
        </div>
        <span className="flex items-center gap-1 text-[11.5px] text-ink-400 dark:text-slate-500">
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
          Credentials encrypted at rest
        </span>
      </div>

      <dl className="mt-3 grid gap-x-4 gap-y-1.5 text-[12px] sm:grid-cols-2">
        <div className="rounded-lg bg-mist-50 px-2.5 py-1.5 dark:bg-white/5">
          <dt className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">Scopes</dt>
          <dd className="break-words leading-5">{connection.scopes.join(", ") || "none"}</dd>
        </div>
        <div className="rounded-lg bg-mist-50 px-2.5 py-1.5 dark:bg-white/5">
          <dt className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">Token</dt>
          <dd className="leading-5">
            Expires {formatWhen(connection.expiresAt)} · refreshed {formatWhen(connection.lastRefreshedAt)}
          </dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {granted.map((capability) => (
          <Badge key={capability.id} tone="success" size="sm">
            <Check className="h-3 w-3" aria-hidden /> {capability.title}
          </Badge>
        ))}
        {missing.map((capability) => (
          <Badge key={capability.id} tone="neutral" size="sm" title="Not granted by this connection">
            <Lock className="h-3 w-3" aria-hidden /> {capability.title}
          </Badge>
        ))}
      </div>

      {connection.lastError && (
        <p role="alert" className="mt-3 rounded-lg bg-warning-soft px-2.5 py-1.5 text-[12px] text-warning-fg">
          Last provider error: {connection.lastError}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {confirming ? (
          <>
            <Button
              variant="destructive"
              size="sm"
              loading={working}
              onClick={() => {
                setConfirming(false);
                void disconnect(businessId, connection.id);
              }}
            >
              <Unplug className="h-3.5 w-3.5" aria-hidden />
              Confirm disconnect
            </Button>
            <GhostButton size="sm" disabled={working} onClick={() => setConfirming(false)}>
              Cancel
            </GhostButton>
          </>
        ) : (
          <GhostButton size="sm" disabled={working} onClick={() => setConfirming(true)}>
            <Unplug className="h-3.5 w-3.5" aria-hidden />
            Disconnect
          </GhostButton>
        )}
        <span className="text-[11.5px] text-ink-400 dark:text-slate-500">
          Owner-only. Disconnecting discards the stored credentials.
        </span>
      </div>
      <ActionFeedback record={record} />
    </Card>
  );
}

type OAuthNoticeState = { tone: "success" | "error"; text: string } | null;

function readOAuthNotice(params: { get(key: string): string | null }): OAuthNoticeState {
  const result = params.get("integration");
  if (result === "connected") {
    const provider = params.get("provider");
    const account = params.get("account");
    return {
      tone: "success",
      text: `${provider ? provider[0].toUpperCase() + provider.slice(1) : "Integration"} connected${
        account ? ` as ${account}` : ""
      }. The server holds the credentials; models never see them.`,
    };
  }
  if (result === "error") {
    const code = params.get("code") ?? "";
    return {
      tone: "error",
      text: OAUTH_ERROR_MESSAGE[code] ?? "The connection could not be completed. Try again.",
    };
  }
  return null;
}

function OAuthNotice() {
  const params = useSearchParams();
  const notice = readOAuthNotice(params);
  const hasResult = params.get("integration") !== null;

  useEffect(() => {
    // Strip OAuth query parameters (which can include an account name) from
    // the visible URL after they have been read once. This uses the History
    // API rather than a router navigation so the current render (and the
    // notice above) is not discarded.
    if (hasResult) {
      window.history.replaceState(window.history.state, "", window.location.pathname);
    }
  }, [hasResult]);

  if (!notice) return null;
  return (
    <div
      role={notice.tone === "error" ? "alert" : "status"}
      className={
        notice.tone === "error"
          ? "rounded-xl border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger-fg"
          : "rounded-xl border border-success-line bg-success-soft px-3 py-2 text-[12.5px] text-success-fg"
      }
    >
      {notice.text}
    </div>
  );
}

function LiveIntegrationsView({ businessId }: { businessId: string }) {
  const { status, providers, connections, error, load, reset } = useLiveIntegrations();
  const [connectedOnly, setConnectedOnly] = useState(false);

  useEffect(() => {
    void load(businessId);
    return () => reset();
  }, [businessId, load, reset]);

  const connectionsByProvider = useMemo(() => {
    const map = new Map<string, ApiIntegrationConnection[]>();
    for (const connection of connections) {
      const rows = map.get(connection.provider) ?? [];
      rows.push(connection);
      map.set(connection.provider, rows);
    }
    return map;
  }, [connections]);

  const visibleProviders = connectedOnly
    ? providers.filter((provider) => (connectionsByProvider.get(provider.id) ?? []).length > 0)
    : providers;

  return (
    <>
      <PageHeader
        title="Integrations"
        sub="Connect once; agents use only the capabilities you grant, and every write still requires your approval."
        badge={<LiveBadge />}
        actions={
          <label className="flex items-center gap-2 text-[12px] font-medium text-ink-500 dark:text-slate-300">
            <input
              type="checkbox"
              checked={connectedOnly}
              onChange={(event) => setConnectedOnly(event.target.checked)}
              className="h-3.5 w-3.5 rounded border-line"
            />
            Connected only
          </label>
        }
      />
      <Suspense fallback={null}>
        <OAuthNotice />
      </Suspense>

      {status === "loading" || status === "idle" ? (
        <div className="grid gap-3 lg:grid-cols-2" role="status" aria-label="Loading integrations">
          {[0, 1].map((i) => (
            <Card key={i} className="p-4">
              <div className="flex items-start gap-3">
                <Skeleton className="h-10 w-10 rounded-xl" />
                <div className="flex-1">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="mt-2 h-3 w-11/12" />
                  <Skeleton className="mt-1.5 h-3 w-2/3" />
                </div>
              </div>
              <div className="mt-3 flex gap-1.5">
                <Skeleton className="h-5 w-24 rounded-full" />
                <Skeleton className="h-5 w-24 rounded-full" />
              </div>
              <Skeleton className="mt-3 h-3 w-1/2" />
              <Skeleton className="mt-4 h-8 w-40" />
            </Card>
          ))}
        </div>
      ) : status === "error" ? (
        <Card className="p-8 text-center">
          <div role="alert">
            <p className="text-[14px] font-semibold">Couldn&apos;t load integrations</p>
            <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500 dark:text-slate-400">{error}</p>
          </div>
          <div className="mt-3 flex justify-center">
            <PrimaryButton onClick={() => void load(businessId)}>Retry</PrimaryButton>
          </div>
        </Card>
      ) : (
        <>
          {connections.length > 0 && (
            <section aria-labelledby="connected-accounts" className="flex flex-col gap-2">
              <h2 id="connected-accounts" className="text-[13px] font-bold uppercase tracking-wide text-ink-400">
                Connected accounts
              </h2>
              <div className="grid gap-3 lg:grid-cols-2">
                {connections.map((connection) => (
                  <ConnectionCard key={connection.id} businessId={businessId} connection={connection} />
                ))}
              </div>
            </section>
          )}

          <section aria-labelledby="available-integrations" className="mt-2 flex flex-col gap-2">
            <h2 id="available-integrations" className="text-[13px] font-bold uppercase tracking-wide text-ink-400">
              Available integrations
            </h2>
            {visibleProviders.length === 0 ? (
              <Card className="p-8 text-center text-[13px] text-ink-400">
                No connected integrations in this business yet. Clear “Connected only” to see
                what is available.
              </Card>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {visibleProviders.map((provider) => (
                  <ProviderCard
                    key={provider.id}
                    businessId={businessId}
                    provider={provider}
                    connected={connectionsByProvider.get(provider.id) ?? []}
                  />
                ))}
              </div>
            )}
          </section>

          <Card className="p-4 text-[12.5px] leading-5 text-ink-500 dark:text-slate-400">
            <p className="flex items-center gap-1.5 font-semibold text-ink-600 dark:text-slate-200">
              <Lock className="h-3.5 w-3.5" aria-hidden />
              How permissions work
            </p>
            <p className="mt-0.5">
              Connecting an account does not let an agent use it. Each agent must also be granted
              the specific capability. Reads are limited to what you approved; drafts and sends
              create a real approval request and only run after you approve it. Email content is
              treated as untrusted data, never as instructions.
            </p>
          </Card>
        </>
      )}
    </>
  );
}

const DEMO_PROVIDERS = [
  {
    id: "gmail",
    name: "Gmail",
    blurb: "Read mail, prepare drafts and send email — sends are always approval-gated.",
    capabilities: ["List messages", "Read a message", "Read a thread", "List labels", "Create draft", "Send message"],
  },
] as const;

function DemoIntegrationsView() {
  return (
    <>
      <PageHeader
        title="Integrations"
        sub="External integrations run against the connected backend. Nothing is simulated as a live connection."
      />
      <Card className="p-4 text-[13px] leading-5 text-ink-500 dark:text-slate-400">
        <p className="font-semibold text-ink-600 dark:text-slate-200">Live integrations only</p>
        <p className="mt-1">
          Sign in to a business on a configured server to connect an account. In demo mode no
          OAuth flow starts, no credentials exist, and no email can be read or sent.
        </p>
      </Card>
      <div className="grid gap-3 lg:grid-cols-2">
        {DEMO_PROVIDERS.map((provider) => (
          <Card key={provider.id} className="p-4">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-line bg-surface-sunken text-brand">
                <Mail className="h-5 w-5" aria-hidden />
              </span>
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-[14.5px] font-bold">{provider.name}</p>
                  <Badge tone="neutral">Demo · not connected</Badge>
                </div>
                <p className="mt-1 text-[12.5px] text-ink-500 dark:text-slate-400">{provider.blurb}</p>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {provider.capabilities.map((capability) => (
                <span
                  key={capability}
                  className="rounded-full border border-line bg-surface px-2 py-0.5 text-[11.5px] text-ink-500 dark:text-slate-300"
                >
                  {capability}
                </span>
              ))}
            </div>
            <div className="mt-4 flex items-center gap-2">
              <PrimaryButton size="sm" disabled title="Live integration — requires a configured backend and a signed-in business.">
                Connect
              </PrimaryButton>
              <span className="text-[11.5px] text-ink-400 dark:text-slate-500">
                Available after sign-in on a configured server
              </span>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}

export default function IntegrationsPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveIntegrationsView businessId={currentBusinessId} />;
  }
  return <DemoIntegrationsView />;
}
