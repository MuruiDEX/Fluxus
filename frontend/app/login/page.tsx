"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Cpu } from "lucide-react";
import { useSession } from "@/lib/store/auth";
import { Card, GhostButton, PrimaryButton } from "@/components/system/ui";

export default function LoginPage() {
  const router = useRouter();
  const { login, error, status } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      if (await login(email.trim(), password)) router.push("/");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-mist-50 px-4 dark:bg-night-950">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-cyan-500 text-white">
            <Cpu className="h-4 w-4" />
          </span>
          <span className="text-[15px] font-bold">MUSE SPARK</span>
        </div>
        <h1 className="mt-3 text-[18px] font-bold">Log in</h1>
        <p className="mt-0.5 text-[13px] text-ink-500 dark:text-slate-400">
          Local operator account. No demo data leaves this view.
        </p>
        <form onSubmit={submit} className="mt-4 flex flex-col gap-3">
          <label className="text-[12.5px] font-medium">
            Email
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 h-10 w-full rounded-[10px] border border-mist-200 bg-transparent px-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10"
            />
          </label>
          <label className="text-[12.5px] font-medium">
            Password
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 h-10 w-full rounded-[10px] border border-mist-200 bg-transparent px-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10"
            />
          </label>
          {error && (
            <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-[12.5px] text-red-600 dark:bg-red-500/10 dark:text-red-300">
              {error}
            </p>
          )}
          <PrimaryButton className="justify-center">
            {busy || status === "unknown" ? "Please wait…" : "Log in"}
          </PrimaryButton>
        </form>
        <GhostButton className="mt-3 w-full justify-center" onClick={() => router.push("/")}>
          Continue in demo mode
        </GhostButton>
      </Card>
    </div>
  );
}
